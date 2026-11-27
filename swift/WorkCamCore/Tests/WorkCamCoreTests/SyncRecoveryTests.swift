// TM-04 B11/B12 tests: the HTTP recovery decision table and the
// Retry-After clamp. Runs for real on Windows Swift 6.4.3 and on Mac.

import XCTest
import WorkCamSync

final class SyncRecoveryTests: XCTestCase {

    func test_401_reauthenticates() {
        XCTAssertEqual(
            SyncRecovery.decision(status: 401, attemptCount: 1),
            .reauthenticate)
    }

    func test_403_escalates_permission_never_retries() {
        XCTAssertEqual(
            SyncRecovery.decision(status: 403, attemptCount: 1),
            .escalatePermission)
    }

    func test_409_routes_to_conflict_rebase() {
        XCTAssertEqual(
            SyncRecovery.decision(status: 409, attemptCount: 1),
            .rebaseConflict)
    }

    func test_429_honors_server_retry_after_hint() {
        XCTAssertEqual(
            SyncRecovery.decision(status: 429, attemptCount: 2,
                                  retryAfterSeconds: 30),
            .retryAfter(hintSeconds: 30))
    }

    func test_429_without_hint_uses_backoff_channel() {
        XCTAssertEqual(
            SyncRecovery.decision(status: 429, attemptCount: 2),
            .retryAfter(hintSeconds: nil))
    }

    func test_5xx_transient_backoff() {
        for s in [500, 502, 503] {
            XCTAssertEqual(
                SyncRecovery.decision(status: s, attemptCount: 1),
                .retryAfter(hintSeconds: nil))
        }
    }

    func test_other_4xx_fails_fast_instead_of_retrying() {
        XCTAssertEqual(
            SyncRecovery.decision(status: 422, attemptCount: 1),
            .failFast)
    }

    func test_exhausted_attempts_dead_letter() {
        if case .deadLetter = SyncRecovery.decision(
            status: 500, attemptCount: SyncRecovery.maxAttempts) {
            // expected
        } else {
            XCTFail("expected deadLetter at max attempts")
        }
    }

    func test_retry_after_clamped_to_envelope() {
        XCTAssertEqual(SyncRecovery.clampedRetryAfter(9999), 300)
        XCTAssertEqual(SyncRecovery.clampedRetryAfter(-5), 0)
        XCTAssertNil(SyncRecovery.clampedRetryAfter(nil))
        XCTAssertNil(SyncRecovery.clampedRetryAfter(.nan))
        XCTAssertEqual(SyncRecovery.clampedRetryAfter(12), 12)
    }
}
