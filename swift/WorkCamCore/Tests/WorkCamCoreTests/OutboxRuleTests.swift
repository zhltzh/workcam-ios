// TM67/TM70 rule tests - the same honesty contracts as the Python
// suites (TM33/TM32): restart recovery, dependency ordering, typed
// conflicts never silently retried, violations never patched.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import XCTest
@testable import WorkCamSync

final class OutboxRuleTests: XCTestCase {

    func makeEngine() -> OutboxEngine {
        OutboxEngine(store: InMemoryOutboxStore(),
                     backoff: ExponentialBackoff(baseSeconds: 1,
                                                 maxSeconds: 2,
                                                 jitterRatio: 0),
                     clock: FixedClock())
    }

    /// Scripted transport: returns preset receipts per call.
    final class ScriptedTransport: SyncTransport {
        var batches: [[OutboxOperation]] = []
        var scriptedResults: [[SyncReceipt]]
        init(scriptedResults: [[SyncReceipt]]) {
            self.scriptedResults = scriptedResults
        }
        func sendBatch(_ operations: [OutboxOperation]) async throws
                -> [SyncReceipt] {
            batches.append(operations)
            if scriptedResults.isEmpty {
                return operations.map {
                    SyncReceipt(operationID: $0.operationID,
                                status: "applied")
                }
            }
            return scriptedResults.removeFirst()
        }
    }

    func test_duplicate_operation_id_refused() throws {
        let engine = makeEngine()
        try engine.enqueue(OutboxOperation(operationID: "op-1",
                                           op: "project_create",
                                           payloadJSON: "{}"))
        XCTAssertThrowsError(try engine.enqueue(
            OutboxOperation(operationID: "op-1", op: "project_create",
                            payloadJSON: "{}"))) { err in
            guard case OutboxError.duplicateOperationID = err else {
                return XCTFail("expected duplicateOperationID")
            }
        }
    }

    func test_missing_dependency_refused() throws {
        let engine = makeEngine()
        XCTAssertThrowsError(try engine.enqueue(
            OutboxOperation(operationID: "op-2", op: "annotation_create",
                            payloadJSON: "{}",
                            dependsOn: ["op-ghost"]))) { err in
            guard case OutboxError.dependencyMissing = err else {
                return XCTFail("expected dependencyMissing")
            }
        }
    }

    func test_dependency_not_sent_before_ack() async throws {
        let engine = makeEngine()
        try engine.enqueue(OutboxOperation(
            operationID: "a", op: "photo_register",
            payloadJSON: "{}"))
        try engine.enqueue(OutboxOperation(
            operationID: "b", op: "annotation_create",
            payloadJSON: "{}", dependsOn: ["a"]))
        let transport = ScriptedTransport(
            scriptedResults: [[SyncReceipt(
                operationID: "a", status: "rejected",
                errorCode: "invalid_payload",
                errorMessage: "bad")]])
        // batch 1: 'b' is NOT in it (depends on unacked 'a')
        _ = try await engine.drain(transport: transport)
        XCTAssertEqual(transport.batches[0].count, 1)
        XCTAssertEqual(transport.batches[0][0].operationID, "a")
    }

    func test_conflict_is_surfaced_never_retried() async throws {
        let engine = makeEngine()
        try engine.enqueue(OutboxOperation(
            operationID: "c", op: "project_update",
            payloadJSON: "{}"))
        let transport = ScriptedTransport(
            scriptedResults: [[SyncReceipt(
                operationID: "c", status: "rejected",
                errorCode: "revision_conflict",
                errorMessage: "current 3")]])
        _ = try await engine.drain(transport: transport)
        let op = engine.store.loadAll().first!
        XCTAssertEqual(op.state, .conflict)
        XCTAssertEqual(op.lastErrorCode, "revision_conflict")
        // drain again: the conflicted op is NOT re-sent
        _ = try await engine.drain(transport: transport)
        XCTAssertEqual(transport.batches.count, 1)
        // manual resolution re-arms it under the same id
        try engine.resolveConflict(operationID: "c",
                                   newPayloadJSON: "{\"rev\":3}")
        let reArmed = engine.store.loadAll().first!
        XCTAssertEqual(reArmed.state, .pending)
        XCTAssertEqual(reArmed.attempts, 0)
    }

    func test_transport_failure_backs_off_and_retries() async throws {
        final class FailingOnce: SyncTransport {
            var calls = 0
            func sendBatch(_ operations: [OutboxOperation]) async
                    throws -> [SyncReceipt] {
                calls += 1
                if calls == 1 { throw NSError(domain: "net", code: 1) }
                return operations.map {
                    SyncReceipt(operationID: $0.operationID,
                                status: "applied")
                }
            }
        }
        let clock = MutableClock()
        let engine = OutboxEngine(
            store: InMemoryOutboxStore(),
            backoff: ExponentialBackoff(baseSeconds: 1, maxSeconds: 2,
                                        jitterRatio: 0),
            clock: clock)
        try engine.enqueue(OutboxOperation(
            operationID: "d", op: "project_create",
            payloadJSON: "{}"))
        let transport = FailingOnce()
        _ = try await engine.drain(transport: transport)
        var op = engine.store.loadAll().first!
        XCTAssertEqual(op.state, .pending)     // back in the queue
        XCTAssertEqual(op.attempts, 1)
        XCTAssertNotNil(op.nextAttemptAt)
        // advance past the backoff interval so the retry is DUE
        // (FixedClock never advances - see MutableClock's doc note)
        clock.advance(by: 2)
        _ = try await engine.drain(transport: transport)
        op = engine.store.loadAll().first!
        XCTAssertEqual(op.state, .acknowledged)
        XCTAssertEqual(transport.calls, 2)
    }

    func test_restart_recovers_sending_to_pending() async throws {
        let store = InMemoryOutboxStore()
        var mid = OutboxOperation(operationID: "e", op: "issue_create",
                                  payloadJSON: "{}")
        mid.state = .sending          // crash remnant
        try store.save(mid)
        let engine = OutboxEngine(store: store,
                                  backoff: ExponentialBackoff(),
                                  clock: FixedClock())
        XCTAssertEqual(try engine.recoverAfterRestart(), 1)
        XCTAssertEqual(engine.store.loadAll().first!.state, .pending)
    }

    func test_applied_and_duplicate_both_acknowledge() async throws {
        let engine = makeEngine()
        try engine.enqueue(OutboxOperation(
            operationID: "f1", op: "project_create",
            payloadJSON: "{}"))
        try engine.enqueue(OutboxOperation(
            operationID: "f2", op: "project_create",
            payloadJSON: "{}"))
        let transport = ScriptedTransport(
            scriptedResults: [[
                SyncReceipt(operationID: "f1", status: "applied"),
                SyncReceipt(operationID: "f2", status: "duplicate"),
            ]])
        let acked = try await engine.drain(transport: transport)
        XCTAssertEqual(acked, 2)
        XCTAssertEqual(
            engine.store.loadAll().filter { $0.state == .acknowledged }
                .count, 2)
    }
}

final class RecoverySMRuleTests: XCTestCase {

    func test_night04_shape_is_violation_never_patched() {
        // reference-before-evidence: a binding/link claim without the
        // bytes under it
        let f = PipelineFacts(metadataRow: true,
                              originalBytesOnDisk: false,
                              bindingRow: true)
        let plan = planRecovery(f)
        XCTAssertEqual(plan.state, .violation)
        XCTAssertEqual(plan.action, .reportViolation)
    }

    func test_verified_without_link_is_violation() {
        let f = PipelineFacts(metadataRow: true,
                              originalBytesOnDisk: true,
                              bindingRow: true,
                              evidenceLinkRow: false,
                              linkVerified: true)
        XCTAssertEqual(deriveState(f), .violation)
    }

    func test_ladder_states() {
        XCTAssertEqual(deriveState(PipelineFacts()), .empty)
        XCTAssertEqual(deriveState(
            PipelineFacts(metadataRow: true)), .registered)
        XCTAssertEqual(deriveState(
            PipelineFacts(originalBytesOnDisk: true)), .orphanBytes)
        XCTAssertEqual(deriveState(
            PipelineFacts(metadataRow: true,
                          originalBytesOnDisk: true)),
            .storedUnbound)
        XCTAssertEqual(deriveState(
            PipelineFacts(metadataRow: true,
                          originalBytesOnDisk: true,
                          bindingRow: true)), .bound)
        XCTAssertEqual(deriveState(
            PipelineFacts(metadataRow: true,
                          originalBytesOnDisk: true,
                          bindingRow: true,
                          evidenceLinkRow: true)),
            .evidencePending)
        XCTAssertEqual(deriveState(
            PipelineFacts(metadataRow: true,
                          originalBytesOnDisk: true,
                          bindingRow: true,
                          evidenceLinkRow: true,
                          linkFailed: true)), .evidenceFailed)
        XCTAssertEqual(deriveState(
            PipelineFacts(metadataRow: true,
                          originalBytesOnDisk: true,
                          bindingRow: true,
                          evidenceLinkRow: true,
                          linkVerified: true)), .trustedSigned)
    }

    func test_no_plan_is_destructive() {
        for f in [PipelineFacts(), PipelineFacts(metadataRow: true),
                  PipelineFacts(originalBytesOnDisk: true),
                  PipelineFacts(linkVerified: true)] {
            XCTAssertFalse(planRecovery(f).isDestructive)
        }
    }

    func test_capture_marker_completeness() {
        XCTAssertTrue(CaptureTransaction.isComplete(
            markerExists: true, originalHashesMatch: true,
            manifestParses: true))
        XCTAssertFalse(CaptureTransaction.isComplete(
            markerExists: false, originalHashesMatch: true,
            manifestParses: true))     // markerless = never evidence
    }
}
