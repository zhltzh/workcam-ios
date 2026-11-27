// TM-04 E08 tests: compare/alignment session state machine.
// Runs for real on Windows Swift 6.4.3 and on Mac.

import XCTest
import WorkCamModels

final class CompareLogicTests: XCTestCase {

    func test_fresh_session_is_unproposed_and_has_no_offset() {
        let s = AlignmentSession()
        XCTAssertEqual(s.stage, .unproposed)
        XCTAssertNil(s.activeOffset)
        XCTAssertFalse(s.hasPendingProposal)
    }

    func test_proposal_is_pending_but_not_active() {
        let s = AlignmentSession().receiveProposal(
            dx: 3.2, dy: -1.5, sharpness: 40, ncc: 0.9)
        XCTAssertTrue(s.hasPendingProposal)
        // RED LINE: the auto estimate is NOT in effect yet.
        XCTAssertNil(s.activeOffset)
        XCTAssertEqual(
            s.stage,
            .proposed(dx: 3.2, dy: -1.5, sharpness: 40, ncc: 0.9))
    }

    func test_accept_activates_the_proposed_offset() {
        let s = AlignmentSession().receiveProposal(
            dx: 3.2, dy: -1.5, sharpness: 40, ncc: 0.9)
            .acceptProposal()
        XCTAssertFalse(s.hasPendingProposal)
        let off = s.activeOffset
        XCTAssertNotNil(off)
        XCTAssertEqual(off?.dx ?? 0, 3.2, accuracy: 1e-12)
        XCTAssertEqual(off?.dy ?? 0, -1.5, accuracy: 1e-12)
    }

    func test_adjust_adds_user_delta_to_the_proposal() {
        let s = AlignmentSession().receiveProposal(
            dx: 3.0, dy: 1.0, sharpness: 40, ncc: 0.9)
            .adjustProposal(deltaX: -1.5, deltaY: 0.25)
        let off = s.activeOffset
        XCTAssertEqual(off?.dx ?? 0, 1.5, accuracy: 1e-12)
        XCTAssertEqual(off?.dy ?? 0, 1.25, accuracy: 1e-12)
    }

    func test_reject_leaves_the_pair_unaligned_with_no_offset() {
        let s = AlignmentSession().receiveProposal(
            dx: 9.0, dy: 9.0, sharpness: 40, ncc: 0.9)
            .rejectProposal()
        XCTAssertEqual(s.stage, .rejected)
        XCTAssertNil(s.activeOffset)   // no "helpful" fallback nudge
        XCTAssertFalse(s.hasPendingProposal)
    }

    func test_accept_and_adjust_only_work_on_a_pending_proposal() {
        let base = AlignmentSession()
        // unproposed: no-op
        XCTAssertEqual(base.acceptProposal().stage, .unproposed)
        XCTAssertEqual(base.adjustProposal(deltaX: 5, deltaY: 5)
            .stage, .unproposed)
        // rejected: also no-op (no resurrection)
        let rejected = base.rejectProposal()
        XCTAssertEqual(rejected.acceptProposal().stage, .rejected)
    }

    func test_manual_alignment_without_proposal_works_directly() {
        let s = AlignmentSession().setManualAlignment(dx: -2, dy: 4)
        let off = s.activeOffset
        XCTAssertEqual(off?.dx ?? 0, -2, accuracy: 1e-12)
        XCTAssertEqual(off?.dy ?? 0, 4, accuracy: 1e-12)
    }

    func test_sessions_are_values_originals_never_mutated() {
        let original = AlignmentSession().receiveProposal(
            dx: 1, dy: 2, sharpness: 30, ncc: 0.8)
        _ = original.acceptProposal()
        _ = original.rejectProposal()
        // the original value is untouched (value semantics; there is
        // no in-place mutation API on the session)
        XCTAssertTrue(original.hasPendingProposal)
    }

    func test_all_four_compare_modes_exist() {
        XCTAssertEqual(
            CompareMode.allCases.map(\.rawValue),
            ["side_by_side", "slider", "opacity", "manual_align"])
    }
}
