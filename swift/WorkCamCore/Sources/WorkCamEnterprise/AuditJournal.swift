// TM-04 H06 (EXPERIMENTAL enterprise reserve): organization audit
// trail. Every privileged action appends an immutable record -
// history is append-only, sequenced per organization, and queryable
// by actor/action/window for enterprise traceability.
//
// RED LINES:
//   - append-only: no update, no delete API exists on the journal
//   - the actor is the ENROLLED member id - the journal never
//     stores personal data beyond the id (privacy posture)
//   - entries carry the target id + a payload HINT (like the
//     stable backend's sync events), not full payloads

import Foundation

public struct AuditEntry: Codable, Sendable, Equatable {
    public let organizationID: String
    public let sequence: Int          // per-org monotone
    public let actorMemberID: String
    public let action: String         // free vocabulary, logged verbatim
    public let targetType: String      // project | photo | issue | member | role | invitation
    public let targetID: String
    public let payloadHint: String
    public let recordedAt: Date

    public init(organizationID: String, sequence: Int,
                actorMemberID: String, action: String,
                targetType: String, targetID: String,
                payloadHint: String, recordedAt: Date) {
        self.organizationID = organizationID
        self.sequence = sequence
        self.actorMemberID = actorMemberID
        self.action = action
        self.targetType = targetType
        self.targetID = targetID
        self.payloadHint = payloadHint
        self.recordedAt = recordedAt
    }
}

public final class OrganizationAuditJournal: @unchecked Sendable {
    private var entries: [AuditEntry] = []
    private var sequenceByOrg: [String: Int] = [:]
    private let lock = NSLock()

    public init() {}

    /// Append one entry. The per-org sequence is assigned HERE and
    /// is strictly monotone; concurrent appends cannot interleave
    /// sequences.
    @discardableResult
    public func append(organizationID: String,
                       actorMemberID: String, action: String,
                       targetType: String, targetID: String,
                       payloadHint: String, at: Date) -> AuditEntry {
        lock.lock(); defer { lock.unlock() }
        let seq = (sequenceByOrg[organizationID] ?? 0) + 1
        sequenceByOrg[organizationID] = seq
        let entry = AuditEntry(
            organizationID: organizationID, sequence: seq,
            actorMemberID: actorMemberID, action: action,
            targetType: targetType, targetID: targetID,
            payloadHint: payloadHint, recordedAt: at)
        entries.append(entry)
        return entry
    }

    // MARK: queries (read-only views)

    public func history(organizationID: String) -> [AuditEntry] {
        lock.lock(); defer { lock.unlock() }
        return entries.filter {
            $0.organizationID == organizationID
        }.sorted { $0.sequence < $1.sequence }
    }

    public func history(organizationID: String,
                        by actor: String) -> [AuditEntry] {
        history(organizationID: organizationID)
            .filter { $0.actorMemberID == actor }
    }

    public func history(organizationID: String,
                        action: String) -> [AuditEntry] {
        history(organizationID: organizationID)
            .filter { $0.action == action }
    }

    public func history(organizationID: String,
                        targetID: String) -> [AuditEntry] {
        history(organizationID: organizationID)
            .filter { $0.targetID == targetID }
    }

    /// Windowed query with an injected clock basis: entries whose
    /// recordedAt falls in [from, to]. The clock never asserts
    /// ordering - the per-org sequence already provides order.
    public func history(organizationID: String,
                        from: Date, to: Date) -> [AuditEntry] {
        history(organizationID: organizationID)
            .filter { $0.recordedAt >= from && $0.recordedAt <= to }
    }
}
