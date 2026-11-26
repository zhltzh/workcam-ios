// TM58: ten-dimension trust engine Swift-side rules tests. The SAME
// honesty contracts as the Python suite (test_tm07_trust_engine) -
// these are rule tests, not vector tests: they pin the refusal
// behaviors that keep the client honest.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import XCTest
@testable import WorkCamModels

final class TrustLevelRuleTests: XCTestCase {

    func testTenDimensionsAlwaysPresent() {
        var inputs = TrustInputs()
        inputs.verificationEnvironment = "desktop"
        let report = computeTrust(inputs)
        XCTAssertEqual(report.dimensions.count, 10)
        XCTAssertEqual(Set(report.dimensions.map(\.name)),
                       Set(TrustDimension.allCases))
    }

    func testMissingEverythingIsUnknownNotFailed() {
        var inputs = TrustInputs()
        inputs.verificationEnvironment = "device"
        let r = computeTrust(inputs)
        XCTAssertEqual(r.dimension(.fileIntegrity)?.status, .unknown)
        XCTAssertEqual(r.dimension(.signatureValidity)?.status,
                       .unverified)
        XCTAssertEqual(r.dimension(.hardwareAttestation)?.status,
                       .notAttested)
        XCTAssertFalse(r.anyFailed)
    }

    func testAppAttestWithoutBindingIsNotAttested() {
        var inputs = TrustInputs()
        inputs.verificationEnvironment = "device"
        inputs.appAttest = .init(verified: true)
        let r = computeTrust(inputs)
        XCTAssertEqual(r.dimension(.hardwareAttestation)?.status,
                       .notAttested)
        XCTAssertTrue(r.dimension(.hardwareAttestation)?
            .reason.contains("NOT bound") ?? false)
    }

    func testAppAttestWithFailedBindingFails() {
        var inputs = TrustInputs()
        inputs.verificationEnvironment = "device"
        inputs.appAttest = .init(verified: true)
        inputs.photoKeyBinding = .init(verified: false)
        let r = computeTrust(inputs)
        XCTAssertEqual(r.dimension(.hardwareAttestation)?.status,
                       .failed)
    }

    func testRotatedOutKeyIsUnverifiedNotFailed() {
        var inputs = TrustInputs()
        inputs.verificationEnvironment = "device"
        inputs.keyRegistration = .init(enrollmentStatus: "rotated_out")
        let r = computeTrust(inputs)
        let d = r.dimension(.deviceRegistration)
        XCTAssertEqual(d?.status, .unverified)
        XCTAssertTrue(d?.reason.contains("historical") ?? false)
    }

    func testRevokedKeyFails() {
        var inputs = TrustInputs()
        inputs.verificationEnvironment = "device"
        inputs.keyRegistration = .init(enrollmentStatus: "revoked")
        let r = computeTrust(inputs)
        XCTAssertEqual(r.dimension(.deviceRegistration)?.status,
                       .failed)
    }

    func testExtrapolatedTimeNeverVerified() {
        var inputs = TrustInputs()
        inputs.verificationEnvironment = "device"
        inputs.timeAnchor = .init(state: "extrapolated")
        let r = computeTrust(inputs)
        let d = r.dimension(.timeSource)
        XCTAssertEqual(d?.status, .unverified)
        XCTAssertTrue(d?.reason
            .contains("NOT a server-certified") ?? false)
    }

    func testGnssLocationNeverVerified() {
        var inputs = TrustInputs()
        inputs.verificationEnvironment = "device"
        inputs.location = .init(source: "gnss", spoofRisk: "low")
        let r = computeTrust(inputs)
        XCTAssertEqual(r.dimension(.locationSource)?.status,
                       .unverified)
    }

    func testNoCompositeScore() {
        var inputs = TrustInputs()
        inputs.verificationEnvironment = "desktop"
        let r = computeTrust(inputs)
        XCTAssertTrue(r.notes.contains(TrustReport.noScoreNote))
        // and the Codable form carries no score field either
        let data = try! JSONEncoder().encode(r)
        let s = String(data: data, encoding: .utf8)!
        XCTAssertFalse(s.contains("\"score\""))
    }

    func testDesktopEnvironmentIsUnverified() {
        var inputs = TrustInputs()
        inputs.verificationEnvironment = "desktop"
        let r = computeTrust(inputs)
        XCTAssertEqual(r.dimension(.verificationEnvironment)?.status,
                       .unverified)
    }

    func testMissingEnvironmentIsUnknown() {
        var inputs = TrustInputs()     // environment deliberately unset
        let r = computeTrust(inputs)
        XCTAssertEqual(r.dimension(.verificationEnvironment)?.status,
                       .unknown)
    }
}
