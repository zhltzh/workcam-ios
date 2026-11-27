// TM-04 B02: conflict-resolution core - the four user decisions and
// their exact outbox semantics. Complements ConflictDetail (which
// declares what the server allows) with the EXECUTION of what the
// user chose.
//
// RED LINES (task sheet B queue):
//   - keepLocal is an EXPLICIT user decision, never automatic.
//   - takeServer never deletes the local edit: the op (and its
//     payload) stays in the store, state moves to acknowledged, and
//     the audit trail records the decision.
//   - manualMerge requires a user-authored payload; this engine
//     never fabricates one.
//   - deferred keeps the op in the conflict queue; it never decays
//     into pending on its own.
//
// All four paths re-enter through the server's revision gate (CAS):
// a stale decision simply produces a new conflict, never a silent
// overwrite.

import Foundation
import WorkCamEvidence

public enum ConflictDecision: String, Codable, Sendable, Equatable {
    case keepLocal = "keep_local"
    case takeServer = "take_server"
    case manualMerge = "manual_merge"
    case deferred = "deferred"
}

public enum ConflictResolutionError: Error, Equatable, Sendable {
    /// keepLocal needs the server's current revision for the CAS
    /// resubmission; the receipt did not carry one.
    case missingServerRevision
    /// manualMerge needs the user-authored merged payload.
    case missingMergedPayload
    /// Only an op in the conflict state may be resolved.
    case notInConflict(String)
}

public enum ConflictResolution {

    /// Apply the user decision to a conflicted operation and return
    /// the updated op for the store. The caller persists it; the
    /// engine does not touch the store here so the whole decision
    /// can be persisted atomically with its audit entry.
    public static func apply(
        _ decision: ConflictDecision,
        to op: OutboxOperation,
        serverRevision: Int?,
        mergedPayloadJSON: String? = nil,
        clock: BackoffClock
    ) throws -> OutboxOperation {
        guard op.state == .conflict else {
            throw ConflictResolutionError.notInConflict(
                op.operationID)
        }
        var resolved = op
        resolved.lastErrorCode = nil
        resolved.lastErrorMessage = "resolved: " + decision.rawValue
        switch decision {
        case .keepLocal:
            // Explicit local-wins: resubmit the LOCAL payload but
            // stamp the server's current revision so the server's
            // CAS gate sees an honest base. Without the server
            // revision this decision cannot be executed safely.
            guard let rev = serverRevision else {
                throw ConflictResolutionError.missingServerRevision
            }
            resolved.payloadJSON = try stampBaseRevision(
                op.payloadJSON, serverRevision: rev)
            resolved.state = .pending
            resolved.attempts = 0
            resolved.nextAttemptAt = clock.now()
        case .takeServer:
            // Server wins by user choice. The local edit is NOT
            // deleted: the op with its original payload stays in the
            // store, acknowledged, as the audit record.
            resolved.state = .acknowledged
            resolved.nextAttemptAt = nil
        case .manualMerge:
            guard let merged = mergedPayloadJSON, !merged.isEmpty
            else {
                throw ConflictResolutionError.missingMergedPayload
            }
            guard let rev = serverRevision else {
                throw ConflictResolutionError.missingServerRevision
            }
            resolved.payloadJSON = try stampBaseRevision(
                merged, serverRevision: rev)
            resolved.state = .pending
            resolved.attempts = 0
            resolved.nextAttemptAt = clock.now()
        case .deferred:
            // Stays a conflict; no automatic retry (nextAttemptAt is
            // cleared so drain never picks it up by accident).
            resolved.state = .conflict
            resolved.nextAttemptAt = nil
            resolved.lastErrorCode = "deferred"
        }
        return resolved
    }

    /// Stamp `base_revision` into a JSON-object payload so the
    /// resubmission tells the server which version it was based on.
    /// Non-object payloads are rejected (sync ops are objects).
    static func stampBaseRevision(
        _ payloadJSON: String, serverRevision: Int) throws -> String {
        guard let data = payloadJSON.data(using: .utf8),
              var obj = try? JSONDecoder().decode(
                  [String: JSONValue].self, from: data) else {
            throw ConflictResolutionError.missingServerRevision
        }
        obj["base_revision"] = .number(Double(serverRevision))
        let out = try JSONEncoder()
            .encode(obj)
        return String(data: out, encoding: .utf8) ?? payloadJSON
    }
}
