// TM-04 B04/B06/B07: stale-intent audit + rebind, server project
// import planning, and remediation link semantics. Real Windows
// compile + unit tests.
//
// B04 RED LINES:
//   - a rebased operation gets a NEW operation id (replaying the
//     old id with different content would hit the server ledger's
//     idempotency_conflict), and the audit trail keeps the OLD
//     receipt rows - supersession is recorded, nothing overwritten
//   - dependents follow their producer: a rebind rewrites
//     dependsOn edges to the new ids transitively (one pass over a
//     DAG, cycles impossible because the outbox already refuses
//     them at enqueue time)
// B06: an import plan NEVER auto-creates over a name collision -
//   collisions surface as .needsDecision. Already-mapped server ids
//   are idempotent .linkExisting actions.
// B07: remediation photo-role links are CAS-gated; withdrawing a
//   link is an explicit, recorded event (never a silent overwrite);
//   cross-project links are refused by name.

import Foundation

// MARK: - B04: intent audit + rebind

public struct SupersessionRecord: Codable, Sendable, Equatable {
    public let oldOperationID: String
    public let newOperationID: String
    public let reason: String
    public let recordedAt: Date

    public init(oldOperationID: String, newOperationID: String,
                reason: String, recordedAt: Date) {
        self.oldOperationID = oldOperationID
        self.newOperationID = newOperationID
        self.reason = reason
        self.recordedAt = recordedAt
    }
}

public enum IntentAudit {

    public enum AuditError: Error, Equatable {
        case unknownStaleID(String)
    }

    /// Rebind stale operations: each stale op is re-issued under a
    /// NEW id (fresh idempotency key for the server ledger),
    /// attempt counters reset, and every dependent's dependsOn edge
    /// is rewritten to follow the producer's new id. The returned
    /// records are the APPEND-ONLY supersession audit trail - old
    /// receipts stay untouched in the caller's ledger.
    public static func rebindStale(
        operations: [OutboxOperation],
        staleIDs: Set<String>,
        uuidProvider: UUIDProvider,
        reason: String,
        at: Date) throws -> (operations: [OutboxOperation],
                             supersessions: [SupersessionRecord]) {
        guard staleIDs.isSubset(of: Set(operations.map(\.operationID)))
        else {
            let unknown = staleIDs.subtracting(
                Set(operations.map(\.operationID))).sorted().first!
            throw AuditError.unknownStaleID(unknown)
        }

        var newForOld: [String: String] = [:]
        var supersessions: [SupersessionRecord] = []
        var rebound: [OutboxOperation] = []
        for op in operations {
            if staleIDs.contains(op.operationID) {
                let newID = uuidProvider.next()
                newForOld[op.operationID] = newID
                var fresh = op
                fresh.payloadJSON = op.payloadJSON   // content kept;
                // ConflictResolution stamps base_revision before
                // this op re-enters the queue
                supersessions.append(SupersessionRecord(
                    oldOperationID: op.operationID,
                    newOperationID: newID,
                    reason: reason, recordedAt: at))
                rebound.append(OutboxOperation(
                    operationID: newID, op: op.op,
                    payloadJSON: op.payloadJSON,
                    dependsOn: op.dependsOn))
                // note: dependents are fixed in the second pass
                _ = fresh
            } else {
                rebound.append(op)
            }
        }
        // second pass: rewrite dependency edges to follow rebinds
        rebound = rebound.map { op in
            guard op.dependsOn.contains(where: {
                newForOld[$0] != nil }) else { return op }
            var fixed = op
            fixed = OutboxOperation(
                operationID: op.operationID, op: op.op,
                payloadJSON: op.payloadJSON,
                dependsOn: op.dependsOn.map { newForOld[$0] ?? $0 })
            return fixed
        }
        return (rebound, supersessions)
    }
}

// MARK: - B06: server project import plan (preview + confirm + idempotent)

public enum ImportAction: Equatable, Sendable {
    /// Local has no record; creating is safe. `localID` is the id
    /// the local store WILL use.
    case createNew(serverID: String, localID: String)
    /// Already mapped (B05): re-import links the existing local
    /// record - idempotent, no new row.
    case linkExisting(serverID: String, localID: String)
    /// A local record with the same NAME exists but has no mapping:
    /// the engine never guesses - the user decides.
    case needsDecision(serverID: String, name: String,
                       conflictingLocalID: String)
}

public struct ServerProjectStub: Equatable, Sendable {
    public let serverID: String
    public let name: String

    public init(serverID: String, name: String) {
        self.serverID = serverID
        self.name = name
    }
}

public enum ImportPlanner {

    /// Build the import PLAN (preview only - nothing is written).
    /// localProjects: the local store's current rows;
    /// mappings: the B05 UUID mapping table (already-associated
    /// server ids are idempotent link actions).
    public static func plan(
        serverProjects: [ServerProjectStub],
        localProjects: [ProjectRecord],
        mappings: UUIDMappingStore,
        uuidProvider: UUIDProvider) -> [ImportAction] {
        let byName = Dictionary(
            localProjects.map { ($0.name, $0) },
            uniquingKeysWith: { a, _ in a })
        return serverProjects.map { sp in
            if let mapped = mappings.localID(
                entityType: "project", serverID: sp.serverID) {
                return .linkExisting(serverID: sp.serverID,
                                     localID: mapped.localID)
            }
            if let clash = byName[sp.name],
               mappings.serverID(entityType: "project",
                                 localID: clash.projectID) == nil {
                return .needsDecision(
                    serverID: sp.serverID, name: sp.name,
                    conflictingLocalID: clash.projectID)
            }
            return .createNew(serverID: sp.serverID,
                              localID: uuidProvider.next())
        }
    }
}

// MARK: - B07: remediation photo-role link semantics

public enum IssuePhotoRole: String, Codable, Sendable, Equatable,
                            CaseIterable {
    case before
    case progress
    case after
}

public enum RemediationLinkError: Error, Equatable {
    case crossProjectRefusal(photoProject: String, issueProject: String)
    case staleRevision(issueID: String, have: Int, need: Int)
    case roleNotSettable(role: IssuePhotoRole)
}

public struct RemediationLinkEvent: Codable, Sendable, Equatable {
    public enum Kind: String, Codable, Sendable {
        case link
        case unlink           // withdraw - recorded, never silent
        case replace          // CAS replace of the same role
    }
    public let kind: Kind
    public let issueID: String
    public let photoID: String
    public let role: IssuePhotoRole
    /// The revision the caller based this change on; the server's
    /// CAS rejects mismatches (and the client routes to B02).
    public let baseIssueRevision: Int
    public let recordedAt: Date

    public init(kind: Kind, issueID: String, photoID: String,
                role: IssuePhotoRole, baseIssueRevision: Int,
                recordedAt: Date) {
        self.kind = kind
        self.issueID = issueID
        self.photoID = photoID
        self.role = role
        self.baseIssueRevision = baseIssueRevision
        self.recordedAt = recordedAt
    }
}

public enum RemediationLinkSemantics {

    /// Validate one link op before it enters the outbox.
    ///   - cross-project photo/issue pairs are refused by NAME
    ///   - the base revision must be carried (CAS contract)
    /// `before` photos cannot be replaced once an `after` photo
    /// links the same issue - the pair is a legal record (withdraw
    /// is an explicit unlink event instead).
    public static func validate(
        event: RemediationLinkEvent,
        issueProjectID: String,
        photoProjectID: String,
        currentIssueRevision: Int,
        issueHasAfterPhoto: Bool) throws {
        guard photoProjectID == issueProjectID else {
            throw RemediationLinkError.crossProjectRefusal(
                photoProject: photoProjectID,
                issueProject: issueProjectID)
        }
        guard event.baseIssueRevision == currentIssueRevision else {
            throw RemediationLinkError.staleRevision(
                issueID: event.issueID,
                have: event.baseIssueRevision,
                need: currentIssueRevision)
        }
        if event.kind == .replace, event.role == .before,
           issueHasAfterPhoto {
            throw RemediationLinkError.roleNotSettable(role: .before)
        }
    }

    /// Applied append-only journal of link events per issue - the
    /// history queries read this, the live state derives from it.
    public static func liveRoles(
        events: [RemediationLinkEvent]) -> [IssuePhotoRole: String] {
        var out: [IssuePhotoRole: String] = [:]
        for e in events.sorted(by: { $0.recordedAt < $1.recordedAt }) {
            switch e.kind {
            case .link, .replace:
                out[e.role] = e.photoID
            case .unlink:
                if out[e.role] == e.photoID {
                    out.removeValue(forKey: e.role)
                }
            }
        }
        return out
    }
}
