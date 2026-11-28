// TM-04 D04: required-evidence validation matrix - the Swift twin
// of test_tm04_d04_required_evidence.py. REAL Windows execution.

import XCTest
import WorkCamModels

final class ChecklistEvidenceMatrixTests: XCTestCase {

    private func runFor(_ reqs: [EvidenceRequired]) -> ChecklistRun {
        ChecklistRun(templateKind: .construction, projectID: "p1",
                     sections: [
            ChecklistSection(sectionID: "s", title: "S", items: [
                ChecklistItem(itemID: "i1", text: "t",
                              requiredEvidence: reqs),
            ])])
    }

    func test_pass_without_photo_refused_not_waivable() {
        let run = runFor([.photo])
        XCTAssertThrowsError(try run.setStatus("i1", .passed)) { e in
            guard case BusinessError.named(let msg) = e else {
                return XCTFail("wrong error \(e)")
            }
            XCTAssertTrue(msg.contains("requires a photo"))
            XCTAssertTrue(msg.contains("NOT waivable"))
        }
    }

    func test_empty_photo_list_is_not_a_photo() {
        let run = runFor([.photo])
        XCTAssertThrowsError(
            try run.setStatus("i1", .failed, photoIDs: []))
    }

    func test_whitespace_note_is_not_a_note() {
        let run = runFor([.note])
        XCTAssertThrowsError(
            try run.setStatus("i1", .passed, note: "   \n  "))
    }

    func test_nonfinite_measurement_never_passes_the_gate() {
        let run = runFor([.measurement])
        for bad in [Double.nan, .infinity, -.infinity] {
            XCTAssertThrowsError(
                try run.setStatus("i1", .passed,
                                 measurement: (bad, "mm"))) { e in
                guard case BusinessError.named(let msg) = e else {
                    return XCTFail("wrong error \(e)")
                }
                XCTAssertTrue(msg.contains("finite"))
            }
        }
    }

    func test_measurement_without_unit_refused() {
        let run = runFor([.measurement])
        XCTAssertThrowsError(
            try run.setStatus("i1", .passed, measurement: (1.5, "")))
    }

    func test_valid_measurement_passes() {
        let run = runFor([.measurement])
        XCTAssertNoThrow(try run.setStatus("i1", .passed,
                                          measurement: (2.4, "m")))
        XCTAssertTrue(run.summary().complete)
    }

    func test_partial_evidence_is_not_enough() {
        let run = runFor([.photo, .note])
        XCTAssertThrowsError(
            try run.setStatus("i1", .passed, note: "only note"))
        XCTAssertThrowsError(
            try run.setStatus("i1", .passed, photoIDs: ["ph-1"]))
        XCTAssertNoThrow(try run.setStatus(
            "i1", .passed, photoIDs: ["ph-1"], note: "ok"))
        XCTAssertTrue(run.summary().complete)
    }

    func test_all_three_required_all_three_checked() {
        let run = runFor([.photo, .note, .measurement])
        XCTAssertThrowsError(try run.setStatus(
            "i1", .passed, photoIDs: ["a"], note: "n"))
        XCTAssertNoThrow(try run.setStatus(
            "i1", .passed, photoIDs: ["a"], note: "n",
            measurement: (3.0, "m")))
        XCTAssertTrue(run.summary().complete)
    }

    func test_na_and_not_inspected_do_not_demand_evidence() {
        let run = runFor([.photo, .note, .measurement])
        XCTAssertNoThrow(try run.setStatus("i1", .notApplicable))
    }
}
