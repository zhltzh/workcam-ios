// TM70: evidence pipeline crash-recovery state machine - Swift port
// of backend/techlab/recovery_sm.py (TM32) + the client capture
// transaction model (Mac Night 04 fix reference).
//
// STATES ARE DERIVED FROM FACTS, NEVER STORED: the client mirrors the
// server's fact-based derivation so the two sides cannot drift - a
// stored status is a status that lies after the next crash.
//
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

public enum PipelineState: String, Codable, Sendable {
    case empty
    case registered                    // metadata row only (F1)
    case orphanBytes = "orphan_bytes"   // bytes with no row (F2)
    case storedUnbound = "stored_unbound"   // F1+F2, no binding (F3)
    case bound                          // F1+F2+F3 complete
    case evidencePending = "evidence_pending"   // +F4, unverified
    case evidenceFailed = "evidence_failed"     // F4 explicit failure
    case trustedSigned = "trusted_signed"       // F5 server-verified
    case violation                      // trust claim without facts
}

public struct PipelineFacts: Sendable, Equatable {
    public let metadataRow: Bool                 // F1
    public let originalBytesOnDisk: Bool          // F2
    public let bindingRow: Bool                  // F3
    public let evidenceLinkRow: Bool              // F4
    public let linkVerified: Bool                // F5
    public let linkFailed: Bool

    public init(metadataRow: Bool = false,
               originalBytesOnDisk: Bool = false,
               bindingRow: Bool = false,
               evidenceLinkRow: Bool = false,
               linkVerified: Bool = false,
               linkFailed: Bool = false) {
        self.metadataRow = metadataRow
        self.originalBytesOnDisk = originalBytesOnDisk
        self.bindingRow = bindingRow
        self.evidenceLinkRow = evidenceLinkRow
        self.linkVerified = linkVerified
        self.linkFailed = linkFailed
    }
}

/// Pure derivation - byte-for-byte the same rule set as the Python
/// engine (violations FIRST: a trust claim without its facts is never
/// benign, and recovery never patches a violation by inventing rows).
public func deriveState(_ f: PipelineFacts) -> PipelineState {
    if f.linkVerified, !f.evidenceLinkRow || !f.bindingRow {
        return .violation
    }
    if f.linkFailed && !f.evidenceLinkRow {
        return .violation
    }
    if f.evidenceLinkRow && !f.bindingRow {
        return .violation
    }
    if f.bindingRow && !(f.metadataRow && f.originalBytesOnDisk) {
        return .violation
    }
    if f.linkVerified { return .trustedSigned }
    if f.linkFailed { return .evidenceFailed }
    if f.evidenceLinkRow { return .evidencePending }
    if f.bindingRow { return .bound }
    if f.metadataRow && f.originalBytesOnDisk { return .storedUnbound }
    if f.originalBytesOnDisk { return .orphanBytes }
    if f.metadataRow { return .registered }
    return .empty
}

public enum RecoveryAction: String, Sendable {
    case none
    case awaitUpload = "await_upload"
    case reportOrphanBytes = "report_orphan_bytes"
    case rebind
    case reverify
    case keepFailed = "keep_failed"
    case reportViolation = "report_violation"
}

public struct RecoveryPlan: Sendable, Equatable {
    public let state: PipelineState
    public let action: RecoveryAction
    public let isDestructive: Bool     // always false by construction

    public var invariants: [String] {
        ["I1 strict fact ordering (violations escalate, never patched)",
         "I2 originals never deleted or overwritten",
         "I3 idempotent: re-running yields the same plan",
         "I4 no trust upgrade without server-executed proof"]
    }
}

public func planRecovery(_ f: PipelineFacts) -> RecoveryPlan {
    let state = deriveState(f)
    let action: RecoveryAction
    switch state {
    case .empty: action = .none
    case .registered: action = .awaitUpload
    case .orphanBytes: action = .reportOrphanBytes
    case .storedUnbound: action = .rebind
    case .bound: action = .none
    case .evidencePending: action = .reverify
    case .evidenceFailed: action = .keepFailed
    case .trustedSigned: action = .none
    case .violation: action = .reportViolation
    }
    return RecoveryPlan(state: state, action: action,
                         isDestructive: false)
}

// MARK: - capture intent transaction (client-side staging)

/// The CLIENT half of the Night 04 fix: a capture moves through a
/// STAGING directory with completion markers, so a crash at any
/// point leaves recoverable facts on disk - never a half-written
/// "evidence" file that later gets mistaken for a complete one.
public struct CaptureTransaction: Sendable, Equatable {
    public let captureID: String
    public let stagedOriginalPath: String
    public let stagedManifestPath: String
    public let completionMarkerPath: String

    public init(captureID: String,
                stagedOriginalPath: String,
                stagedManifestPath: String,
                completionMarkerPath: String) {
        self.captureID = captureID
        self.stagedOriginalPath = stagedOriginalPath
        self.stagedManifestPath = stagedManifestPath
        self.completionMarkerPath = completionMarkerPath
    }

    /// The staged capture is COMPLETE only when the marker exists -
    /// the marker is written LAST, after the original and manifest
    /// both hash-verified. A crash mid-write leaves no marker and the
    /// recovery sweep re-runs the pipeline; a markerless staged set
    /// is never promoted to 'evidence'.
    public static func isComplete(markerExists: Bool,
                                  originalHashesMatch: Bool,
                                  manifestParses: Bool) -> Bool {
        markerExists && originalHashesMatch && manifestParses
    }
}
