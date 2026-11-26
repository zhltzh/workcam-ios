// M03-B12 rule tests - the same honesty contracts as the Python
// suites (B01-B11), Swift side.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import XCTest
@testable import WorkCamModels

final class BusinessRuleTests: XCTestCase {

    // ---- B02: checklist honesty ---------------------------------

    func test_unchecked_never_auto_passes() {
        let run = ChecklistRun(templateKind: .construction,
                               projectID: "p1", sections: [
            ChecklistSection(sectionID: "s", title: "S", items: [
                ChecklistItem(itemID: "i1", text: "access clear",
                              requiredEvidence: [.photo]),
            ])])
        let s = run.summary()
        XCTAssertFalse(s.complete)
        XCTAssertEqual(s.blockers, ["i1"])
    }

    func test_evidence_requirement_blocks_pass() {
        let run = ChecklistRun(templateKind: .construction,
                               projectID: "p1", sections: [
            ChecklistSection(sectionID: "s", title: "S", items: [
                ChecklistItem(itemID: "i1", text: "t",
                              requiredEvidence: [.photo]),
            ])])
        XCTAssertThrowsError(try run.setStatus("i1", .passed)) { e in
            guard case BusinessError.named(let msg) = e else {
                return XCTFail("wrong error \(e)")
            }
            XCTAssertTrue(msg.contains("requires a photo"))
        }
        XCTAssertNoThrow(try run.setStatus("i1", .passed,
                                          photoIDs: ["ph1"]))
        XCTAssertTrue(run.summary().complete)
    }

    // ---- B04: lifecycle guard -----------------------------------

    func test_illegal_jump_refused() {
        let issue = IssueLifecycle(issueID: "i-1")
        XCTAssertThrowsError(
            try issue.transition(to: .resolved, actor: "a"))
        XCTAssertNoThrow(
            try issue.transition(to: .inProgress, actor: "a"))
        XCTAssertNoThrow(
            try issue.transition(to: .resolved, actor: "a"))
        XCTAssertNoThrow(
            try issue.transition(to: .open, actor: "b",
                                 note: "reopened"))
        XCTAssertEqual(issue.status, .open)
        XCTAssertEqual(issue.history.count, 3)
    }

    func test_closed_is_terminal() {
        let issue = IssueLifecycle(issueID: "i-2")
        try? issue.transition(to: .inProgress, actor: "a")
        try? issue.transition(to: .resolved, actor: "a")
        try? issue.transition(to: .closed, actor: "a")
        XCTAssertThrowsError(
            try issue.transition(to: .open, actor: "a"))
    }

    // ---- B05: roles ----------------------------------------------

    func test_cross_project_refused_and_never_auto_complete() {
        var links = IssuePhotoLinks(issueID: "iss-1",
                                    projectID: "proj-1")
        XCTAssertThrowsError(
            try links.link(photoID: "ph-x", projectID: "proj-2",
                           role: .before))
        XCTAssertNoThrow(
            try links.link(photoID: "ph-1", projectID: "proj-1",
                           role: .before))
        XCTAssertNoThrow(
            try links.link(photoID: "ph-2", projectID: "proj-1",
                           role: .after))
        let c = links.completeness
        XCTAssertTrue(c.hasPair)
        XCTAssertFalse(c.autoComplete)
        XCTAssertTrue(c.note.contains("HUMAN"))
    }

    // ---- B06: naming ----------------------------------------------

    func test_sanitize_and_reserved() throws {
        XCTAssertEqual(try Naming.sanitizeComponent("a<b>c:d"), "a_b_c_d")
        XCTAssertThrowsError(try Naming.sanitizeComponent("CON"))
        XCTAssertThrowsError(try Naming.sanitizeComponent("..."))
    }

    func test_format_resolves_and_refuses() throws {
        let out = try Naming.format(
            template: "{project}_{floor}_{date}",
            context: ["project": "PRJ-1", "floor": "2",
                      "date": "20261127"])
        XCTAssertEqual(out, "PRJ-1_2_20261127")
        XCTAssertThrowsError(
            try Naming.format(template: "{missing}",
                              context: ["a": "b"]))
    }

    // ---- B10: measurements ----------------------------------------

    func test_manual_device_structural_split() {
        XCTAssertThrowsError(try ManagedMeasurement(
            measurementID: "m1", type: "distance_3d", value: 1.0,
            unit: "m", source: .deviceEstimate))   // no mode
        XCTAssertThrowsError(try ManagedMeasurement(
            measurementID: "m2", type: "distance_3d", value: 1.0,
            unit: "m", source: .manualInput,
            measurementMode: "scene_depth"))       // forbidden combo
        XCTAssertThrowsError(try ManagedMeasurement(
            measurementID: "m3", type: "volume", value: 5.0,
            unit: "m3", source: .deviceEstimate,
            measurementMode: "scene_depth"))       // no experimental note
        XCTAssertNoThrow(try ManagedMeasurement(
            measurementID: "m4", type: "volume", value: 5.0,
            unit: "m3", source: .deviceEstimate,
            measurementMode: "scene_depth",
            notes: "experimental bounding box"))
    }

    func test_honest_labels() throws {
        let manual = try ManagedMeasurement(
            measurementID: "m", type: "area", value: 12.5, unit: "m2",
            source: .manualInput)
        XCTAssertTrue(manual.honestLabel.contains("human-entered"))
        let device = try ManagedMeasurement(
            measurementID: "m2", type: "distance_3d", value: 2.0,
            unit: "m", source: .deviceEstimate,
            measurementMode: "scene_depth")
        XCTAssertTrue(device.honestLabel.contains("uncalibrated"))
    }

    // ---- B11: daily log -------------------------------------------

    func test_daily_log_never_fabricates() throws {
        var log = try DailyLog(projectID: "p1", date: "2026-11-27")
        log.photosCaptured = 8
        let md = log.summaryMarkdown
        XCTAssertTrue(md.contains("_(not filled)_"))  // honest empty
        XCTAssertThrowsError(try DailyLog(projectID: "p1",
                                          date: "2026/11/27"))
        log.weather = "sunny"        // USER fills it
        XCTAssertTrue(log.summaryMarkdown.contains("sunny"))
    }

    // ---- B01: templates --------------------------------------------

    func test_template_watermark_snake_case() {
        XCTAssertThrowsError(try ProjectTemplate(
            kind: .hvac, displayName: "Bad",
            photoCategories: [], issueCategories: [],
            sections: [ChecklistSection(sectionID: "s", title: "S",
                                        items: [
                ChecklistItem(itemID: "i", text: "t")])],
            watermarkFields: ["Bad-Field"],
            reportStructure: []))
    }
}
