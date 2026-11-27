// TM-04 B02 tests: the four conflict decisions and their outbox
// semantics. Runs for real on Windows Swift 6.4.3 and on Mac.

import XCTest
import WorkCamSync
import WorkCamEvidence

final class ConflictResolutionTests: XCTestCase {

    private func conflictedOp() -> OutboxOperation {
        var op = OutboxOperation(
            operationID: "op-1", op: "issue_update",
            payloadJSON: #"{"title":"my local edit"}"#)
        op.state = .conflict
        return op
    }

    func test_keep_local_stamps_base_revision_and_requeues() throws {
        let clock = FixedClock(start: Date(timeIntervalSince1970: 100))
        let resolved = try ConflictResolution.apply(
            .keepLocal, to: conflictedOp(), serverRevision: 7,
            clock: clock)
        XCTAssertEqual(resolved.state, .pending)
        XCTAssertEqual(resolved.attempts, 0)
        XCTAssertEqual(resolved.nextAttemptAt, clock.now())
        // payload now carries base_revision for the server's CAS
        let data = resolved.payloadJSON.data(using: .utf8)!
        let obj = try JSONDecoder().decode(
            [String: JSONValue].self, from: data)
        XCTAssertEqual(obj["base_revision"], .number(7))
        XCTAssertEqual(obj["title"], .string("my local edit"))
    }

    func test_keep_local_without_server_revision_refused() {
        XCTAssertThrowsError(
            try ConflictResolution.apply(
                .keepLocal, to: conflictedOp(), serverRevision: nil,
                clock: FixedClock(start: Date(timeIntervalSince1970: 0)))
        ) { error in
            guard case ConflictResolutionError.missingServerRevision
                    = error else {
                return XCTFail("unexpected error \(error)")
            }
        }
    }

    func test_take_server_acknowledges_and_keeps_the_local_payload() throws {
        let op = conflictedOp()
        let before = op.payloadJSON
        let resolved = try ConflictResolution.apply(
            .takeServer, to: op, serverRevision: 7,
            clock: FixedClock(start: Date(timeIntervalSince1970: 0)))
        XCTAssertEqual(resolved.state, .acknowledged)
        XCTAssertNil(resolved.nextAttemptAt)
        // audit red line: the local edit is NOT deleted
        XCTAssertEqual(resolved.payloadJSON, before)
        XCTAssertTrue(resolved.lastErrorMessage?
            .contains("take_server") ?? false)
    }

    func test_manual_merge_requires_a_user_payload() {
        XCTAssertThrowsError(
            try ConflictResolution.apply(
                .manualMerge, to: conflictedOp(), serverRevision: 3,
                mergedPayloadJSON: nil,
                clock: FixedClock(start: Date(timeIntervalSince1970: 0)))
        ) { error in
            guard case ConflictResolutionError.missingMergedPayload
                    = error else {
                return XCTFail("unexpected error \(error)")
            }
        }
    }

    func test_manual_merge_stamps_and_requeues() throws {
        let resolved = try ConflictResolution.apply(
            .manualMerge, to: conflictedOp(), serverRevision: 3,
            mergedPayloadJSON: #"{"title":"user merged it"}"#,
            clock: FixedClock(start: Date(timeIntervalSince1970: 0)))
        XCTAssertEqual(resolved.state, .pending)
        let data = resolved.payloadJSON.data(using: .utf8)!
        let obj = try JSONDecoder().decode(
            [String: JSONValue].self, from: data)
        XCTAssertEqual(obj["title"], .string("user merged it"))
        XCTAssertEqual(obj["base_revision"], .number(3))
    }

    func test_deferred_stays_conflict_with_no_automatic_retry() throws {
        var op = conflictedOp()
        op.nextAttemptAt = Date(timeIntervalSince1970: 50)
        let resolved = try ConflictResolution.apply(
            .deferred, to: op, serverRevision: 7,
            clock: FixedClock(start: Date(timeIntervalSince1970: 0)))
        XCTAssertEqual(resolved.state, .conflict)
        XCTAssertNil(resolved.nextAttemptAt)   // drain must not pick it
        XCTAssertEqual(resolved.lastErrorCode, "deferred")
    }

    func test_non_conflicted_operation_is_refused() {
        let pending = OutboxOperation(
            operationID: "op-2", op: "project_update",
            payloadJSON: #"{"name":"x"}"#)
        XCTAssertThrowsError(
            try ConflictResolution.apply(
                .keepLocal, to: pending, serverRevision: 1,
                clock: FixedClock(start: Date(timeIntervalSince1970: 0)))
        ) { error in
            guard case ConflictResolutionError.notInConflict("op-2")
                    = error else {
                return XCTFail("unexpected error \(error)")
            }
        }
    }
}
