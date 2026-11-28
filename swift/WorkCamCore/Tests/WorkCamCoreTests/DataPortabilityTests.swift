// TM-04 I05 tests: export manifest + account-deletion machine.
// REAL Windows execution.

import XCTest
import WorkCamModels

final class DataPortabilityTests: XCTestCase {

    // MARK: export manifest

    private func item(_ path: String, size: Int = 10,
                      sha: String = String(repeating: "a",
                                           count: 64))
            -> ExportManifestItem {
        ExportManifestItem(relativePath: path, byteSize: size,
                           sha256: sha)
    }

    func test_export_manifest_sorts_and_sums() {
        let m = ExportManifest(
            exportedAt: Date(timeIntervalSince1970: 0),
            items: [item("b.jpg", size: 20), item("a.jpg")])
        XCTAssertEqual(m.items.map(\.relativePath),
                       ["a.jpg", "b.jpg"])
        XCTAssertEqual(m.totalBytes, 30)
        XCTAssertEqual(m.schemaVersion, "workcam-export/1.0")
    }

    func test_duplicate_paths_make_the_manifest_incomplete() {
        let m = ExportManifest(
            exportedAt: Date(timeIntervalSince1970: 0),
            items: [item("a.jpg"), item("a.jpg")])
        XCTAssertFalse(m.isComplete)
    }

    func test_short_digests_make_the_manifest_incomplete() {
        let m = ExportManifest(
            exportedAt: Date(timeIntervalSince1970: 0),
            items: [item("a.jpg", sha: "abc")])
        XCTAssertFalse(m.isComplete)
    }

    func test_clean_manifest_is_complete() {
        let m = ExportManifest(
            exportedAt: Date(timeIntervalSince1970: 0),
            items: [item("a.jpg"), item("b.jpg")])
        XCTAssertTrue(m.isComplete)
    }

    // MARK: deletion machine

    private let requestedAt = Date(timeIntervalSince1970: 1_000_000)

    func test_advance_without_request_is_refused() {
        XCTAssertThrowsError(
            try AccountDeletionEngine.advance(.none, to: Date())
        ) { e in
            XCTAssertEqual(
                e as? AccountDeletionEngine.TransitionError,
                .nothingRequested)
        }
    }

    func test_cancel_inside_window_restores_untouched() throws {
        let s = AccountDeletionEngine.cancel(
            .requested(at: requestedAt))
        XCTAssertEqual(s, .cancelled)
        // cancelled -> advance returns .none (fresh start)
        let next = try AccountDeletionEngine.advance(
            s, to: requestedAt.addingTimeInterval(99999))
        XCTAssertEqual(next, DeletionRequestState.none)
    }

    func test_confirm_inside_window_is_refused_with_remaining_time() {
        let s = DeletionRequestState.requested(at: requestedAt)
        XCTAssertThrowsError(
            try AccountDeletionEngine.confirm(
                s, originals: ["o1"], derivatives: [], issues: [],
                annotations: [], auditEntries: [],
                now: requestedAt.addingTimeInterval(86400))
        ) { e in
            guard case AccountDeletionEngine.TransitionError
                    .windowStillOpen(let remaining) = e else {
                return XCTFail("expected windowStillOpen, got \(e)")
            }
            XCTAssertEqual(remaining, 6 * 86400)
        }
    }

    func test_isConfirmable_only_after_window() {
        let s = DeletionRequestState.requested(at: requestedAt)
        XCTAssertFalse(AccountDeletionEngine.isConfirmable(
            s, now: requestedAt.addingTimeInterval(
                AccountDeletionEngine.waitingWindowSeconds - 1)))
        XCTAssertTrue(AccountDeletionEngine.isConfirmable(
            s, now: requestedAt.addingTimeInterval(
                AccountDeletionEngine.waitingWindowSeconds)))
    }

    func test_confirm_after_window_builds_explicit_plan() throws {
        let s = DeletionRequestState.requested(at: requestedAt)
        let confirmed = try AccountDeletionEngine.confirm(
            s, originals: ["o-1", "o-2"], derivatives: ["d-1"],
            issues: ["i-1"], annotations: [],
            auditEntries: ["a-1"],
            now: requestedAt.addingTimeInterval(
                AccountDeletionEngine.waitingWindowSeconds + 1))
        guard case .confirmed(let plan) = confirmed else {
            return XCTFail("expected confirmed")
        }
        XCTAssertEqual(plan.count, 6)   // 2+1+1+0+1+account
        XCTAssertEqual(
            plan.filter { $0.kind == .originalPhoto }
                .map(\.identifier), ["o-1", "o-2"])
        XCTAssertTrue(plan.contains {
            $0.kind == .accountRecord && $0.identifier == "account"
        })
        // the review note states the irrevocability honestly
        XCTAssertTrue(plan.first {
            $0.kind == .originalPhoto }?.note
            .contains("gone forever") ?? false)
    }

    func test_confirm_is_final_no_second_confirm() throws {
        let s = DeletionRequestState.requested(at: requestedAt)
        let confirmed = try AccountDeletionEngine.confirm(
            s, originals: [], derivatives: [], issues: [],
            annotations: [], auditEntries: [],
            now: requestedAt.addingTimeInterval(999_999))
        XCTAssertThrowsError(
            try AccountDeletionEngine.confirm(
                confirmed, originals: [], derivatives: [],
                issues: [], annotations: [], auditEntries: [],
                now: Date())) { e in
            XCTAssertEqual(
                e as? AccountDeletionEngine.TransitionError,
                .alreadyFinal)
        }
    }

    func test_cancel_after_confirmed_is_inert() throws {
        let s = DeletionRequestState.requested(at: requestedAt)
        let confirmed = try AccountDeletionEngine.confirm(
            s, originals: ["o"], derivatives: [], issues: [],
            annotations: [], auditEntries: [],
            now: requestedAt.addingTimeInterval(999_999))
        // confirmed is terminal: cancel cannot walk it back
        let afterCancel = AccountDeletionEngine.cancel(confirmed)
        XCTAssertEqual(afterCancel, confirmed)
    }
}
