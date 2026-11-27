// TM67: offline Outbox - Swift port of the W008/W014/TM33 sync
// discipline for the CLIENT side, with the TM34 typed-conflict model.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

public enum OutboxState: String, Codable, Sendable {
    case pending
    case sending
    case acknowledged             // server 'applied' or 'duplicate'
    case failed                   // server 'rejected', non-conflict
    case conflict                 // typed conflict (TM34 vocabulary)
}

public struct OutboxOperation: Codable, Sendable, Equatable {
    public let operationID: String
    public let op: String                     // sync op vocabulary
    /// Mutable ONLY via OutboxEngine.resolveConflict (rebase flow:
    /// same operation id, corrected payload). TM-04 fix: was `let`,
    /// which made the documented rebase path uncompilable.
    public var payloadJSON: String
    public let dependsOn: [String]             // op ids that must ack first
    public var state: OutboxState
    public var attempts: Int
    public var nextAttemptAt: Date?
    public var lastErrorCode: String?
    public var lastErrorMessage: String?

    public init(operationID: String, op: String, payloadJSON: String,
                dependsOn: [String] = []) {
        self.operationID = operationID
        self.op = op
        self.payloadJSON = payloadJSON
        self.dependsOn = dependsOn
        self.state = .pending
        self.attempts = 0
        self.nextAttemptAt = nil
        self.lastErrorCode = nil
        self.lastErrorMessage = nil
    }
}

/// Persistence injection (CoreData/GRDB/file-backed on the app side;
/// in-memory doubles in tests).
public protocol OutboxStore: AnyObject, Sendable {
    func loadAll() -> [OutboxOperation]
    func save(_ operation: OutboxOperation) throws
    func saveAll(_ operations: [OutboxOperation]) throws
}

/// The transport executes one BATCH of operations and returns per-op
/// receipts in the server's shape (status: applied/rejected/
/// duplicate; error code + details for rejects).
public struct SyncReceipt: Sendable, Equatable {
    public let operationID: String
    public let status: String
    public let errorCode: String?
    public let errorMessage: String?

    public init(operationID: String, status: String,
                errorCode: String? = nil, errorMessage: String? = nil) {
        self.operationID = operationID
        self.status = status
        self.errorCode = errorCode
        self.errorMessage = errorMessage
    }
}

public protocol SyncTransport: Sendable {
    func sendBatch(_ operations: [OutboxOperation]) async throws
        -> [SyncReceipt]
}

public enum OutboxError: Error, Equatable {
    case duplicateOperationID(String)
    case dependencyMissing(String)
    case dependencyNotAcknowledged(String)
}

/// Deterministic backoff for tests; wall-clock on the app side.
public protocol BackoffClock: Sendable {
    func now() -> Date
}

public struct ExponentialBackoff: Sendable {
    public let baseSeconds: Double
    public let maxSeconds: Double
    public let jitterRatio: Double     // 0 = deterministic (tests)

    public init(baseSeconds: Double = 1.0, maxSeconds: Double = 300.0,
                jitterRatio: Double = 0.0) {
        self.baseSeconds = baseSeconds
        self.maxSeconds = maxSeconds
        self.jitterRatio = jitterRatio
    }

    public func nextAttempt(after attempts: Int,
                           clock: BackoffClock) -> Date {
        let exp = min(maxSeconds,
                      baseSeconds * pow(2, Double(attempts)))
        let jitter = jitterRatio == 0
            ? 0
            : exp * jitterRatio
                * (Double((attempts.hashValue % 100)) / 100.0 - 0.5)
        return clock.now().addingTimeInterval(max(0, exp + jitter))
    }
}

public final class OutboxEngine: @unchecked Sendable {
    /// Internal (not private): the rule suite reads the store via
    /// @testable to assert state transitions (TM-04 fix: the suite
    /// was authored against this access level but never compiled).
    let store: OutboxStore
    private let backoff: ExponentialBackoff
    private let clock: BackoffClock

    public init(store: OutboxStore, backoff: ExponentialBackoff =
                    ExponentialBackoff(),
                clock: BackoffClock) {
        self.store = store
        self.backoff = backoff
        self.clock = clock
    }

    // MARK: - restart recovery

    /// Call once at launch: a 'sending' op found after a restart is a
    /// crash remnant (the batch either completed - the server ledger
    /// will answer 'duplicate' - or never landed). Either way the op
    /// goes BACK to pending; the server idempotency ledger (W008)
    /// makes re-sending safe.
    public func recoverAfterRestart() throws -> Int {
        let all = store.loadAll()
        var recovered = 0
        for var op in all where op.state == .sending {
            op.state = .pending
            op.attempts += 0            // the crash attempt already counted
            op.nextAttemptAt = clock.now()
            try store.save(op)
            recovered += 1
        }
        return recovered
    }

    // MARK: - enqueue

    public func enqueue(_ operation: OutboxOperation) throws {
        let all = store.loadAll()
        guard !all.contains(where: {
            $0.operationID == operation.operationID }) else {
            throw OutboxError.duplicateOperationID(
                operation.operationID)
        }
        // dependencies must exist (they may still be pending - that
        // is fine, ordering is enforced at DRAIN time)
        let ids = Set(all.map(\.operationID))
        for dep in operation.dependsOn where !ids.contains(dep) {
            throw OutboxError.dependencyMissing(dep)
        }
        try store.save(operation)
    }

    // MARK: - drain (one batch attempt)

    /// Pick a dependency-closed, due batch and send it. Receipts are
    /// applied with the TM34 rules:
    ///   applied / duplicate  -> acknowledged
    ///   revision_conflict / idempotency_conflict / annotation_deleted
    ///     / batch_id_conflict -> conflict (UI surfaces it; NEVER a
    ///     silent overwrite; never auto-retried)
    ///   other rejected        -> failed (backoff retry)
    @discardableResult
    public func drain(maxBatch: Int = 50,
                      transport: SyncTransport) async throws -> Int {
        var all = store.loadAll()
        let acked = Set(all.filter { $0.state == .acknowledged }
                        .map(\.operationID))
        let now = clock.now()
        let due = all.filter { op in
            op.state == .pending
            && (op.nextAttemptAt == nil
                || op.nextAttemptAt! <= now)
        }
        // dependency-closed subset
        var batch: [OutboxOperation] = []
        var picked = Set<String>()
        for op in due.sorted(by: { $0.operationID
                < $1.operationID }) {
            guard op.dependsOn.allSatisfy({ acked.contains($0) })
            else { continue }
            batch.append(op)
            picked.insert(op.operationID)
            if batch.count >= maxBatch { break }
        }
        guard !batch.isEmpty else { return 0 }

        // mark sending (crash between here and the receipt handling
        // is recovered by recoverAfterRestart + the server ledger)
        for var op in batch {
            op.state = .sending
            try store.save(op)
        }

        let receipts: [SyncReceipt]
        do {
            receipts = try await transport.sendBatch(batch)
        } catch {
            // transport failure: back to pending with backoff
            for var op in batch {
                op.state = .pending
                op.attempts += 1
                op.nextAttemptAt = backoff.nextAttempt(
                    after: op.attempts, clock: clock)
                op.lastErrorCode = "transport_error"
                op.lastErrorMessage = String(
                    describing: error)
                try store.save(op)
            }
            return 0
        }

        let byID = Dictionary(uniqueKeysWithValues:
                                receipts.map { ($0.operationID, $0) })
        var acknowledgedCount = 0
        for var op in batch {
            guard let r = byID[op.operationID] else { continue }
            switch r.status {
            case "applied", "duplicate":
                op.state = .acknowledged
                op.nextAttemptAt = nil
                op.lastErrorCode = nil
                op.lastErrorMessage = nil
                acknowledgedCount += 1
            case "rejected":
                let code = r.errorCode ?? ""
                let conflictCodes: Set<String> = [
                    "revision_conflict", "idempotency_conflict",
                    "annotation_deleted", "batch_id_conflict",
                    "project_archived",
                ]
                if conflictCodes.contains(code) {
                    op.state = .conflict      // surfaced, not retried
                } else {
                    op.state = .failed
                    op.nextAttemptAt = backoff.nextAttempt(
                        after: op.attempts, clock: clock)
                }
                op.lastErrorCode = code
                op.lastErrorMessage = r.errorMessage
            default:
                op.state = .failed
                op.lastErrorCode = "unknown_receipt_status"
                op.lastErrorMessage = r.status
            }
            try store.save(op)
        }
        return acknowledgedCount
    }

    // MARK: - manual conflict resolution (TM34: never silent)

    /// The user resolved a conflict off-line (rebased the payload);
    /// the op re-enters the pending queue with the NEW payload under
    /// the SAME operation id - the server ledger replays it as the
    /// corrected operation.
    public func resolveConflict(operationID: String,
                                newPayloadJSON: String) throws {
        let all = store.loadAll()
        guard var op = all.first(where: {
            $0.operationID == operationID }) else {
            throw OutboxError.dependencyMissing(operationID)
        }
        guard op.state == .conflict else {
            throw OutboxError.dependencyNotAcknowledged(operationID)
        }
        // payload replaced, attempt counter reset - a NEW submission
        op.payloadJSON = newPayloadJSON
        op.state = .pending
        op.attempts = 0
        op.nextAttemptAt = clock.now()
        op.lastErrorCode = nil
        op.lastErrorMessage = nil
        try store.save(op)
    }
}

// MARK: - in-memory store + fixed clock (test doubles; the XCTest
// suite on Mac drives the engine with them)

public final class InMemoryOutboxStore: OutboxStore, @unchecked Sendable {
    private var ops: [String: OutboxOperation] = [:]
    private let lock = NSLock()

    public init() {}

    public func loadAll() -> [OutboxOperation] {
        lock.lock(); defer { lock.unlock() }
        return ops.values.sorted { $0.operationID < $1.operationID }
    }

    public func save(_ operation: OutboxOperation) {
        lock.lock(); defer { lock.unlock() }
        ops[operation.operationID] = operation
    }

    public func saveAll(_ operations: [OutboxOperation]) {
        lock.lock(); defer { lock.unlock() }
        for o in operations { ops[o.operationID] = o }
    }
}

public struct FixedClock: BackoffClock {
    public let date: Date
    public init(date: Date = Date(timeIntervalSince1970: 1_000_000)) {
        self.date = date
    }
    public func now() -> Date { date }
}

/// Reference-clock the rule suite can STEP between drain rounds.
/// TM-04 fix: FixedClock never advances, so "retry is due" tests
/// built on it could never pass - the op's nextAttemptAt always
/// sat one baseInterval in the future of the frozen 'now'.
/// (Class semantics: the engine must observe later advances.)
public final class MutableClock: BackoffClock, @unchecked Sendable {
    private var current: Date
    private let lock = NSLock()

    public init(date: Date = Date(timeIntervalSince1970: 1_000_000)) {
        self.current = date
    }

    public func now() -> Date {
        lock.lock(); defer { lock.unlock() }
        return current
    }

    public func advance(by seconds: TimeInterval) {
        lock.lock(); defer { lock.unlock() }
        current = current.addingTimeInterval(seconds)
    }
}
