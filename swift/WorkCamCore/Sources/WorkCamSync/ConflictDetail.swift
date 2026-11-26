// TM68: conflict-detail engine - Swift port of the TM34 server
// conflict vocabulary as a UI-consumable model.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).
//
// POLICY (from TM34, enforced by the server AND mirrored here):
//   important engineering records are NEVER silently overwritten.
//   Every conflict carries: what clashed, both sides' versions, the
//   server's current revision (for rebase), and the resolution
//   actions the user may take. The engine never picks one on its own.

import Foundation
import WorkCamModels

public enum ConflictKind: String, Codable, Sendable, CaseIterable {
    case revisionConflict = "revision_conflict"
    case idempotencyConflict = "idempotency_conflict"
    case annotationDeleted = "annotation_deleted"
    case batchIDConflict = "batch_id_conflict"
    case projectArchived = "project_archived"
    case issueStatusConflict = "issue_status_conflict"
}

public enum ConflictResolutionAction: String, Codable, Sendable {
    case rebaseWithCurrentRevision      // server echoed current_revision
    case keepServerDiscardLocal         // user discards their edit
    case reapplyUnderNewID              // idempotency-key collision
    case recreateAnnotation             // deleted-vs-edit: new annotation
    case cancelBatchAndRestart          // batch param drift
    case unarchiveFirst                 // archived project: admin call
}

public struct ConflictDetail: Codable, Sendable, Equatable {
    public let kind: ConflictKind
    public let entityType: String          // project/annotation/issue/...
    public let entityID: String?
    public let localRevision: Int?
    public let serverCurrentRevision: Int?
    public let serverMessage: String
    public let localPayloadJSON: String
    public let allowedActions: [ConflictResolutionAction]

    /// Build from a sync receipt error (the TM34 typed envelope).
    public static func from(serverErrorCode: String,
                            serverMessage: String,
                            entityType: String,
                            entityID: String?,
                            localRevision: Int?,
                            localPayloadJSON: String)
            -> ConflictDetail? {
        guard let kind = ConflictKind(
            rawValue: serverErrorCode) else {
            return nil              // not a conflict code - caller
        }                           // treats it as a plain failure
        var actions: [ConflictResolutionAction] = []
        var serverRev: Int?
        switch kind {
        case .revisionConflict, .issueStatusConflict:
            // the server message embeds current_revision (TM34 #9);
            // the client may rebase and retry
            if let r = Self.extractRevision(serverMessage) {
                serverRev = r
                actions.append(.rebaseWithCurrentRevision)
            }
            actions.append(.keepServerDiscardLocal)
        case .idempotencyConflict:
            actions.append(.reapplyUnderNewID)
            actions.append(.keepServerDiscardLocal)
        case .annotationDeleted:
            actions.append(.recreateAnnotation)
            actions.append(.keepServerDiscardLocal)
        case .batchIDConflict:
            actions.append(.cancelBatchAndRestart)
        case .projectArchived:
            actions.append(.unarchiveFirst)
        }
        return ConflictDetail(
            kind: kind, entityType: entityType, entityID: entityID,
            localRevision: localRevision,
            serverCurrentRevision: serverRev,
            serverMessage: serverMessage,
            localPayloadJSON: localPayloadJSON,
            allowedActions: actions)
    }

    /// "'current_revision': 3" / "current 3" patterns from the
    /// server's conflict messages.
    static func extractRevision(_ message: String) -> Int? {
        for pattern in ["'current_revision': ", "current_revision\": ",
                        "current "] {
            if let r = message.range(of: pattern) {
                let tail = message[r.upperBound...]
                let digits = tail.prefix(while: { $0.isNumber })
                if let v = Int(digits) { return v }
            }
        }
        return nil
    }

    public enum CodingKeys: String, CodingKey {
        case kind
        case entityType = "entity_type"
        case entityID = "entity_id"
        case localRevision = "local_revision"
        case serverCurrentRevision = "server_current_revision"
        case serverMessage = "server_message"
        case localPayloadJSON = "local_payload_json"
        case allowedActions = "allowed_actions"
    }
}

/// UI-facing summary line for each kind (English UI per project
/// rules; the wording keeps the no-silent-overwrite promise visible).
public extension ConflictDetail {
    var userSummary: String {
        switch kind {
        case .revisionConflict:
            return "This record changed on the server while you " +
                "were editing. Your changes were NOT discarded."
        case .idempotencyConflict:
            return "An operation with this key already exists with " +
                "different content."
        case .annotationDeleted:
            return "This annotation was deleted elsewhere; deleted " +
                "annotations are immutable."
        case .batchIDConflict:
            return "A batch with this ID already processed this " +
                "photo with different parameters."
        case .projectArchived:
            return "This project is archived and refuses new " +
                "photos."
        case .issueStatusConflict:
            return "The issue status changed on the server; your " +
                "transition was not applied."
        }
    }
}
