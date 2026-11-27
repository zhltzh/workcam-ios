// TM-04 C13: permission / feature-availability / offline state
// models. The honest-wording rules match SyncCenterReference and
// SettingsReference footers (English UI, no overclaiming).

import Foundation

// MARK: - permission state

public enum PermissionStatus: String, Codable, Sendable, Equatable {
    case notDetermined = "not_determined"
    case granted
    case denied
    /// The OS-level restricted state (parental controls etc.) -
    /// distinct from a user denial, surfaced differently.
    case restricted
}

/// The permission domains the app actually uses. Adding one here is
/// a REVIEWED act (App-Side Files stay in sync with Info.plist).
public enum PermissionDomain: String, Codable, Sendable, CaseIterable {
    case camera
    case locationWhenInUse = "location_when_in_use"
    case photoLibraryAdd = "photo_library_add"
}

public struct PermissionSnapshot: Codable, Sendable, Equatable {
    public let domain: PermissionDomain
    public let status: PermissionStatus
    public let checkedAt: Date

    public init(domain: PermissionDomain, status: PermissionStatus,
                checkedAt: Date) {
        self.domain = domain
        self.status = status
        self.checkedAt = checkedAt
    }
}

// MARK: - feature availability

public enum FeatureAvailability: Equatable, Sendable {
    case available
    /// The named blocker is user-readable; no invented capabilities.
    case unavailable(reason: String)
    /// Available only after the given permission resolves to granted.
    case requiresPermission(PermissionDomain)

    public var isAvailable: Bool {
        if case .available = self { return true }
        return false
    }
}

/// Single source for feature gating. The CAMERA RULE from the
/// camera seam (TM67/FE-E03) lives here too: offline NEVER blocks
/// the shutter - it only labels the queue.
public enum FeatureGate {
    public static func captureAvailability(
        camera: PermissionStatus,
        offline: OfflineState) -> FeatureAvailability {
        switch camera {
        case .granted:
            return .available        // offline does NOT gate capture
        case .notDetermined:
            return .requiresPermission(.camera)
        case .denied:
            return .unavailable(
                reason: "Camera access was denied in Settings.")
        case .restricted:
            return .unavailable(
                reason: "Camera access is restricted on this device.")
        }
    }

    public static func syncAvailability(
        authenticated: Bool,
        offline: OfflineState) -> FeatureAvailability {
        guard authenticated else {
            return .unavailable(
                reason: "Device is not enrolled with the server yet.")
        }
        if case .offline = offline {
            // Queued work is honest; the queue drains on reconnect.
            return .unavailable(
                reason: "Offline - operations queue locally and " +
                        "sync when the connection returns.")
        }
        return .available
    }
}

// MARK: - offline state

public enum OfflineState: Equatable, Sendable {
    case online
    case offline
    /// Unknown means we have not probed recently - the UI shows the
    /// last known direction, never a fabricated confidence.
    case unknown

    public var isOnline: Bool { self == .online }
}
