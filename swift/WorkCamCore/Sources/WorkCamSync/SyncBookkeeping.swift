// TM-04 B05/B09/B10: sync bookkeeping - versioned UUID mappings,
// lifecycle/terminal-state sync semantics, and atomic cursor state.
// Real Windows compile + unit tests.
//
// RED LINES:
//   - B05: one local id maps to AT MOST ONE server id; a second,
//     DIFFERENT server id for the same local id is a named refusal
//     (duplicate-association guard), while replaying the SAME pair
//     is idempotent and returns the original mapping version.
//   - B09: closed is terminal for CONTENT edits (reopen is an
//     explicit new event, never an implicit mutation); archived
//     projects refuse new photos; tombstones are permanent records.
//   - B10: cursors persist atomically (AtomicFile) per owner+entity
//     scope; a crash resumes from the last committed cursor, never
//     skipping events (at-most-once advancement with replay safety
//     on the server ledger).

import Foundation

// MARK: - B05: versioned UUID mapping

public enum UUIDMappingError: Error, Equatable {
    case conflictingServerID(entityType: String, localID: String,
                              have: String, got: String)
}

public struct UUIDMapping: Codable, Sendable, Equatable {
    public let entityType: String
    public let localID: String
    public let serverID: String
    /// Monotonic schema/mapping version - bump when the table shape
    /// or association algorithm changes.
    public let mappingVersion: Int
    public let mappedAt: Date
}

public final class UUIDMappingStore: @unchecked Sendable {
    private var byLocal: [String: UUIDMapping] = [:]   // "type|local"
    private var byServer: [String: UUIDMapping] = [:] // "type|server"
    private let lock = NSLock()

    public private(set) var mappingVersion = 1

    public init() {}

    private static func localKey(_ t: String, _ l: String) -> String {
        t + "|" + l
    }
    private static func serverKey(_ t: String, _ s: String) -> String {
        t + "|" + s
    }

    /// Associate a local id with its server id. Same pair replays
    /// idempotently; a DIFFERENT server id for an already-mapped
    /// local id is refused by name.
    @discardableResult
    public func associate(entityType: String, localID: String,
                           serverID: String, at: Date) throws -> UUIDMapping {
        lock.lock(); defer { lock.unlock() }
        let lk = Self.localKey(entityType, localID)
        let sk = Self.serverKey(entityType, serverID)
        if let existing = byLocal[lk] {
            guard existing.serverID == serverID else {
                throw UUIDMappingError.conflictingServerID(
                    entityType: entityType, localID: localID,
                    have: existing.serverID, got: serverID)
            }
            return existing          // idempotent replay
        }
        // a server id already bound to a different local id is
        // equally a duplicate association
        if let other = byServer[sk], other.localID != localID {
            throw UUIDMappingError.conflictingServerID(
                entityType: entityType, localID: other.localID,
                have: other.localID, got: localID)
        }
        let mapping = UUIDMapping(
            entityType: entityType, localID: localID,
            serverID: serverID, mappingVersion: mappingVersion,
            mappedAt: at)
        byLocal[lk] = mapping
        byServer[sk] = mapping
        return mapping
    }

    public func serverID(entityType: String, localID: String)
            -> UUIDMapping? {
        lock.lock(); defer { lock.unlock() }
        return byLocal[Self.localKey(entityType, localID)]
    }

    public func localID(entityType: String, serverID: String)
            -> UUIDMapping? {
        lock.lock(); defer { lock.unlock() }
        return byServer[Self.serverKey(entityType, serverID)]
    }

    public var count: Int {
        lock.lock(); defer { lock.unlock() }
        return byLocal.count
    }
}

// MARK: - B09: lifecycle sync semantics

public enum EntityLifecycle: String, Codable, Sendable, Equatable,
                            CaseIterable {
    case active
    case tombstoned              // deletion marker (permanent record)
    case archived                // reversible by an explicit admin op
    case closedTerminal = "closed_terminal"   // content edits refused

    /// Which transitions the SYNC layer may carry. Terminal rules:
    ///   - closedTerminal refuses CONTENT edits; an explicit reopen
    ///     is a separate recorded event, not a state mutation here.
    ///   - archived refuses new-photo intake (B05/M03 rule).
    ///   - tombstoned is final: no transition out, history stays.
    public static func allowedTransition(
        from: EntityLifecycle, to: EntityLifecycle) -> Bool {
        switch (from, to) {
        case (.active, .active),
             (.active, .tombstoned),
             (.active, .archived),
             (.active, .closedTerminal):
            return true
        case (.archived, .active):        // unarchive: explicit op
            return true
        case (.tombstoned, .tombstoned):  // replay of the same marker
            return true
        default:
            return false                  // everything else refused
        }
    }

    public var acceptsNewPhotos: Bool {
        self == .active
    }

    public var allowsContentEdits: Bool {
        switch self {
        case .active, .archived:
            // archived projects keep their EXISTING records editable?
            // No: archived = read-only snapshot; unarchive first.
            return false
        case .closedTerminal, .tombstoned:
            return false
        }
    }
}

// MARK: - B10: owner-scoped cursor state, atomically persisted

public struct SyncCursorState: Codable, Sendable, Equatable {
    public let ownerID: String
    public let entityType: String       // nil-scope = full stream
    public let position: Int
    public let updatedAt: Date

    public init(ownerID: String, entityType: String,
                position: Int, updatedAt: Date) {
        self.ownerID = ownerID
        self.entityType = entityType
        self.position = position
        self.updatedAt = updatedAt
    }
}

public final class CursorStateStore: @unchecked Sendable {
    private let fileURL: URL
    private let lock = NSLock()
    private var state: [String: SyncCursorState] = [:]   // "owner|type"

    public init(fileURL: URL) {
        self.fileURL = fileURL
        if let data = try? Data(contentsOf: fileURL),
           let decoded = try? JSONDecoder().decode(
                [String: SyncCursorState].self, from: data) {
            state = decoded
        }
    }

    private static func key(_ owner: String, _ type: String) -> String {
        owner + "|" + type
    }

    /// Persist the new position atomically BEFORE the caller pulls
    /// the next page: a crash after pull replays events (server
    /// ledger tolerates replays), a crash before pull never skips.
    public func advance(ownerID: String, entityType: String,
                        to position: Int, at: Date) throws {
        lock.lock()
        let k = Self.key(ownerID, entityType)
        guard state[k]?.position ?? -1 < position else {
            lock.unlock()
            return              // never move backwards
        }
        state[k] = SyncCursorState(ownerID: ownerID,
                                   entityType: entityType,
                                   position: position, updatedAt: at)
        let snapshot = state
        lock.unlock()
        let data = try JSONEncoder().encode(snapshot)
        try AtomicFile.writeVerified(data, to: fileURL)
    }

    public func position(ownerID: String, entityType: String) -> Int {
        lock.lock(); defer { lock.unlock() }
        return state[Self.key(ownerID, entityType)]?.position ?? 0
    }
}
