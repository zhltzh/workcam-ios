// TM-04 I01/I02 tests: entitlement matrix + offline grace engine.
// REAL Windows execution.

import XCTest
import WorkCamModels

final class EntitlementsTests: XCTestCase {

    // MARK: I01 matrix

    func test_free_tier_keeps_capture_and_basic_reports_only() {
        XCTAssertEqual(Set(EntitlementMatrix.grants(for: .free)),
                       [.capturePhotos, .professionalReports])
        XCTAssertFalse(EntitlementMatrix.tier(
            .free, grants: .evidenceExport))
        XCTAssertFalse(EntitlementMatrix.tier(
            .free, grants: .teamWorkspace))
    }

    func test_pro_tier_unlocks_everything_but_team() {
        XCTAssertEqual(Set(EntitlementMatrix.grants(for: .pro)),
                       Set(ProductFeature.allCases
                        .filter { $0 != .teamWorkspace }))
    }

    func test_team_tier_unlocks_everything() {
        XCTAssertEqual(Set(EntitlementMatrix.grants(for: .team)),
                       Set(ProductFeature.allCases))
    }

    func test_locked_reason_names_the_unlocking_tier() {
        XCTAssertTrue(EntitlementMatrix.lockedReason(.ocrLabeling)
            .contains("Pro"))
        XCTAssertTrue(EntitlementMatrix.lockedReason(.teamWorkspace)
            .contains("Team"))
    }

    // MARK: I02 grace engine

    private let verifiedAt = Date(timeIntervalSince1970: 1_000_000)

    func test_recent_verification_is_active_no_grace_counter() {
        let s = LicenseGraceEngine.state(
            tier: .pro, lastVerifiedAt: verifiedAt,
            now: verifiedAt.addingTimeInterval(-10))
        XCTAssertEqual(s.kind, .active)
        XCTAssertEqual(s.effectiveTier, .pro)
        XCTAssertNil(s.graceRemainingSeconds)
        XCTAssertTrue(s.unlocks(.sixReportTypes))
    }

    func test_offline_within_window_is_grace_with_full_features() {
        let s = LicenseGraceEngine.state(
            tier: .pro, lastVerifiedAt: verifiedAt,
            now: verifiedAt.addingTimeInterval(2 * 86400))
        XCTAssertEqual(s.kind, .grace)
        XCTAssertEqual(s.effectiveTier, .pro)   // paid user, offline
        XCTAssertEqual(s.graceRemainingSeconds,
                       12 * 86400)              // 14 - 2 days
        XCTAssertTrue(s.unlocks(.evidenceExport))
    }

    func test_window_end_is_expiredGrace_data_intact() {
        let s = LicenseGraceEngine.state(
            tier: .pro, lastVerifiedAt: verifiedAt,
            now: verifiedAt.addingTimeInterval(
                LicenseGraceEngine.graceWindowSeconds + 1))
        XCTAssertEqual(s.kind, .expiredGrace)
        XCTAssertEqual(s.effectiveTier, .free)  // dark, not deleted
        XCTAssertFalse(s.unlocks(.sixReportTypes))
        XCTAssertTrue(s.unlocks(.capturePhotos))  // free tier intact
    }

    func test_free_tier_has_no_grace_machinery() {
        let s = LicenseGraceEngine.state(
            tier: .free, lastVerifiedAt: verifiedAt,
            now: verifiedAt.addingTimeInterval(999_999))
        XCTAssertEqual(s.kind, .free)
        XCTAssertEqual(s.effectiveTier, .free)
    }

    func test_team_grace_behaves_like_pro_grace() {
        let s = LicenseGraceEngine.state(
            tier: .team, lastVerifiedAt: verifiedAt,
            now: verifiedAt.addingTimeInterval(86400))
        XCTAssertEqual(s.kind, .grace)
        XCTAssertTrue(s.unlocks(.teamWorkspace))
        XCTAssertEqual(s.graceRemainingSeconds, 13 * 86400)
    }

    func test_status_copy_is_honest_and_english() {
        let active = LicenseState(kind: .active, tier: .pro)
        XCTAssertTrue(LicenseGraceEngine.statusCopy(active)
            .contains("active"))
        let grace = LicenseGraceEngine.state(
            tier: .pro, lastVerifiedAt: verifiedAt,
            now: verifiedAt.addingTimeInterval(3 * 86400))
        XCTAssertTrue(LicenseGraceEngine.statusCopy(grace)
            .contains("Offline"))
        let expired = LicenseState(kind: .expiredGrace, tier: .pro)
        let copy = LicenseGraceEngine.statusCopy(expired)
        XCTAssertTrue(copy.contains("data is safe"))
        XCTAssertTrue(copy.contains("reconnect"))
    }

    func test_grace_counter_counts_down_monotonically() {
        var prev: Int?
        for day in 0...14 {
            let s = LicenseGraceEngine.state(
                tier: .pro, lastVerifiedAt: verifiedAt,
                now: verifiedAt.addingTimeInterval(
                    TimeInterval(day) * 86400))
            if let remaining = s.graceRemainingSeconds {
                if let p = prev {
                    XCTAssertLessThan(remaining, p)
                }
                prev = remaining
            }
        }
    }
}
