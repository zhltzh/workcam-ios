// TM-04 I05: user data export and account-deletion candidate flows.
// CANDIDATE DESIGN - the actual account deletion (server side) is a
// DECISION_REQUIRED product step; this models the SAFE state
// machine only.
//
// RED LINES (task sheet I05 + the standing evidence discipline):
//   - export ALWAYS includes originals (full portability), and the
//     export manifest is hash-verified before the archive is
//     called complete
//   - deletion is a TWO-PHASE flow: request -> waiting period ->
//     confirmed delete. The waiting period is counted against an
//     injected clock; cancellation during the window restores the
//     account untouched.
//   - deletion NEVER auto-runs: the confirm call is explicit, and
//     nothing in this engine deletes data itself - it computes the
//     PHASE transitions and the deletion plan (the server + a human
//     review execute it)
//   - the deletion plan lists original evidence explicitly so the
//     review screen can say exactly what will be gone forever

import Foundation

// MARK: - export manifest

public struct ExportManifestItem: Codable, Sendable, Equatable {
    public let relativePath: String
    public let byteSize: Int
    public let sha256: String

    public init(relativePath: String, byteSize: Int,
                sha256: String) {
        self.relativePath = relativePath
        self.byteSize = byteSize
        self.sha256 = sha256
    }
}

public struct ExportManifest: Codable, Sendable, Equatable {
    public let schemaVersion: String
    public let exportedAt: Date
    public let items: [ExportManifestItem]

    public init(exportedAt: Date, items: [ExportManifestItem]) {
        self.schemaVersion = "workcam-export/1.0"
        self.exportedAt = exportedAt
        self.items = items.sorted { $0.relativePath
                                    < $1.relativePath }
    }

    public var totalBytes: Int {
        items.reduce(0) { $0 + $1.byteSize }
    }

    /// Completeness check: every item needs a digest; duplicates
    /// cannot appear (two identical paths = a corrupt manifest).
    public var isComplete: Bool {
        let paths = items.map(\.relativePath)
        guard Set(paths).count == paths.count else { return false }
        return items.allSatisfy {
            $0.sha256.count == 64 && $0.byteSize >= 0
        }
    }
}

// MARK: - deletion state machine

public enum DeletionRequestState: Equatable, Sendable {
    case none
    /// requested at Date; the waiting window runs from there
    case requested(at: Date)
    /// cancelled before the window ended - account restored
    /// untouched, machine back to none (audit kept by caller)
    case cancelled
    /// window over and user confirmed - the PLAN is ready; the
    /// engine never executes it
    case confirmed(plan: DeletionPlan)
}

public struct DeletionPlanItem: Codable, Sendable, Equatable {
    public enum Kind: String, Codable, Sendable {
        case originalPhoto = "original_photo"
        case derivativePhoto = "derivative_photo"
        case issueRecord = "issue_record"
        case annotationRecord = "annotation_record"
        case auditRecord = "audit_record"
        case accountRecord = "account_record"
    }

    public let kind: Kind
    public let identifier: String
    public let note: String

    public init(kind: Kind, identifier: String, note: String) {
        self.kind = kind
        self.identifier = identifier
        self.note = note
    }
}

public typealias DeletionPlan = [DeletionPlanItem]

public enum AccountDeletionEngine {
    public static let waitingWindowSeconds: TimeInterval
        = 7 * 24 * 3600        // 7 days, fixed product decision

    public enum TransitionError: Error, Equatable {
        case nothingRequested
        case windowStillOpen(remainingSeconds: Int)
        case alreadyFinal
    }

    /// Phase advance with an INJECTED clock (the two-flakes rule).
    public static func advance(
        _ state: DeletionRequestState,
        to now: Date) throws -> DeletionRequestState {
        switch state {
        case .none:
            throw TransitionError.nothingRequested
        case .requested(let at):
            let elapsed = now.timeIntervalSince(at)
            let remaining = waitingWindowSeconds - elapsed
            if remaining > 0 {
                throw TransitionError.windowStillOpen(
                    remainingSeconds: Int(remaining
                        .rounded(.up)))
            }
            // window over: ready for CONFIRMATION - but the engine
            // does not confirm on its own; produce the
            // pending-confirmation marker by throwing? No - the
            // confirm() call is explicit, so advance() over the
            // window returns a state the UI renders as "confirmable".
            return .requested(at: at)   // unchanged; use confirm()
        case .cancelled:
            return .none
        case .confirmed:
            throw TransitionError.alreadyFinal
        }
    }

    /// Is the request confirmable right now (window elapsed)?
    public static func isConfirmable(
        _ state: DeletionRequestState, now: Date) -> Bool {
        guard case .requested(let at) = state else { return false }
        return now.timeIntervalSince(at) >= waitingWindowSeconds
    }

    /// Explicit confirmation AFTER the window: builds the plan the
    /// server/human review executes. The engine deletes nothing.
    public static func confirm(
        _ state: DeletionRequestState,
        originals: [String],
        derivatives: [String],
        issues: [String],
        annotations: [String],
        auditEntries: [String],
        now: Date) throws -> DeletionRequestState {
        guard case .requested(let at) = state else {
            if case .confirmed = state {
                throw TransitionError.alreadyFinal
            }
            throw TransitionError.nothingRequested
        }
        guard now.timeIntervalSince(at) >= waitingWindowSeconds else {
            throw TransitionError.windowStillOpen(
                remainingSeconds: Int((waitingWindowSeconds
                    - now.timeIntervalSince(at)).rounded(.up)))
        }
        var plan: DeletionPlan = []
        plan += originals.map {
            DeletionPlanItem(kind: .originalPhoto, identifier: $0,
                             note: "original evidence file - " +
                                   "gone forever")
        }
        plan += derivatives.map {
            DeletionPlanItem(kind: .derivativePhoto,
                             identifier: $0,
                             note: "derived preview/watermark")
        }
        plan += issues.map {
            DeletionPlanItem(kind: .issueRecord, identifier: $0,
                             note: "issue + its history")
        }
        plan += annotations.map {
            DeletionPlanItem(kind: .annotationRecord,
                             identifier: $0, note: "annotation")
        }
        plan += auditEntries.map {
            DeletionPlanItem(kind: .auditRecord, identifier: $0,
                             note: "audit journal entry")
        }
        plan.append(DeletionPlanItem(
            kind: .accountRecord, identifier: "account",
            note: "account row + credentials"))
        return .confirmed(plan: plan)
    }

    /// Cancel inside or after the window (but before confirm):
    /// account restored untouched.
    public static func cancel(_ state: DeletionRequestState)
            -> DeletionRequestState {
        guard case .requested = state else { return state }
        return .cancelled
    }
}
