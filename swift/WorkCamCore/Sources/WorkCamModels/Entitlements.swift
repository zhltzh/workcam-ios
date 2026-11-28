// TM-04 I01 + I02: Free/Pro/Team entitlement abstraction and the
// offline license-grace model. CANDIDATE DESIGN (task sheet I queue:
// "candidate schemes" only) - Windows authors the pure logic; the
// real StoreKit 2 purchase path stays Mac (I03).
//
// RED LINES:
//   - entitlements are FACTS about a tier, not strings compared in
//     a hundred scattered places - one matrix gates everything
//   - the grace window NEVER silently grants Pro forever: it has a
//     fixed length, it is counted against an injected clock, and
//     when it ends the features go dark - with the user's LOCAL
//     DATA intact (read-only fallback, never deletion)
//   - no pricing, no currency, no purchase logic lives here

import Foundation

// MARK: - I01: tiers and the entitlement matrix

public enum ProductTier: String, Codable, Sendable, Equatable,
                          CaseIterable {
    case free
    case pro
    case team
}

public enum ProductFeature: String, Sendable, Equatable, CaseIterable {
    case capturePhotos = "capture_photos"
    case professionalReports = "professional_reports"
    case sixReportTypes = "six_report_types"
    case evidenceExport = "evidence_export"          // ZIP V2
    case advancedMeasurement = "advanced_measurement" // 3D/volume PoC
    case teamWorkspace = "team_workspace"             // enterprise reserve
    case autoRegistration = "auto_registration"       // E07 (experimental)
    case ocrLabeling = "ocr_labeling"                 // E09
}

public enum EntitlementMatrix {

    /// Which tier unlocks which feature. The matrix is the single
    /// authority; absent = refused (deny-by-default, same
    /// discipline as the enterprise role matrix).
    public static func tier(_ tier: ProductTier,
                            grants feature: ProductFeature) -> Bool {
        switch tier {
        case .free:
            return feature == .capturePhotos
                || feature == .professionalReports
        case .pro:
            return feature != .teamWorkspace
        case .team:
            return true
        }
    }

    public static func grants(for tier: ProductTier)
            -> [ProductFeature] {
        ProductFeature.allCases.filter {
            EntitlementMatrix.tier(tier, grants: $0)
        }
    }

    /// The honest UI label for a locked feature: says what unlocks
    /// it, never invents a discount or urgency.
    public static func lockedReason(
        _ feature: ProductFeature) -> String {
        switch feature {
        case .teamWorkspace:
            return "Team Workspace is part of the Team plan."
        default:
            return "This feature requires WorkCam Pro."
        }
    }
}

// MARK: - I02: offline license grace

public struct LicenseState: Codable, Sendable, Equatable {
    public enum Kind: String, Codable, Sendable, Equatable {
        case active
        case grace          // offline within the grace window
        case expiredGrace = "expired_grace"   // window over: dark
        case free
    }

    public let kind: Kind
    public let tier: ProductTier
    /// Remaining grace SECONDS (nil outside grace). Reported, never
    /// used as a gate by itself - the kind IS the gate.
    public let graceRemainingSeconds: Int?

    public init(kind: Kind, tier: ProductTier,
                graceRemainingSeconds: Int? = nil) {
        self.kind = kind
        self.tier = tier
        self.graceRemainingSeconds = graceRemainingSeconds
    }

    public var effectiveTier: ProductTier {
        switch kind {
        case .active, .grace:
            return tier
        case .expiredGrace, .free:
            return .free
        }
    }

    public func unlocks(_ feature: ProductFeature) -> Bool {
        EntitlementMatrix.tier(effectiveTier, grants: feature)
    }
}

public enum LicenseGraceEngine {
    public static let graceWindowSeconds: TimeInterval
        = 14 * 24 * 3600       // 14 days, fixed product decision

    public enum Error: Swift.Error, Equatable {
        case notASubscription(ProductTier)
    }

    /// Compute the current license state from verification facts
    /// with an INJECTED clock (the two-flakes rule: the caller
    /// reports the clock, this function only decides).
    ///
    /// lastVerifiedAt  = when the App Store receipt last verified
    /// now             = the caller's current time
    public static func state(
        tier: ProductTier,
        lastVerifiedAt: Date,
        now: Date) -> LicenseState {
        switch tier {
        case .free:
            return LicenseState(kind: .free, tier: .free)
        case .pro, .team:
            let age = now.timeIntervalSince(lastVerifiedAt)
            let remaining = graceWindowSeconds - age
            if remaining >= 0 {
                // still inside the window: FULL features (the user
                // PAID; offline is not their fault)
                return LicenseState(
                    kind: age <= 0 ? .active : .grace,
                    tier: tier,
                    graceRemainingSeconds: age <= 0
                        ? nil
                        : Int(remaining.rounded(.down)))
            }
            // window over: features dark, DATA intact (read-only
            // free tier - nothing is ever deleted)
            return LicenseState(kind: .expiredGrace, tier: tier)
        }
    }

    /// The user-facing honesty string for a grace/expired state
    /// (English UI rule).
    public static func statusCopy(
        _ state: LicenseState) -> String {
        switch state.kind {
        case .active:
            return "WorkCam Pro is active."
        case .free:
            return ""
        case .grace:
            let days = max(1, (state.graceRemainingSeconds ?? 0)
                            / 86400)
            return "Offline - verifying your subscription when " +
                   "you reconnect. Full features continue for " +
                   "about \(days) more day(s)."
        case .expiredGrace:
            return "Your device has been offline longer than the " +
                   "verification window. Pro features are paused " +
                   "until you reconnect - your data is safe and " +
                   "fully readable."
        }
    }
}
