// TM-04 B03 tests: the three-way field diff. These RUN FOR REAL on
// the Windows Swift 6.4.3 toolchain (first time a WorkCam Swift rule
// suite executes on this machine) and on Mac via `swift test`.

import XCTest
import WorkCamSync
import WorkCamEvidence

final class FieldDiffTests: XCTestCase {

    private func obj(_ pairs: [String: JSONValue]) -> [String: JSONValue] {
        pairs
    }

    func test_identical_when_both_sides_make_the_same_edit() {
        let diffs = FieldDiffEngine.diff(
            base: obj(["note": .string("draft")]),
            local: obj(["note": .string("fixed")]),
            server: obj(["note": .string("fixed")]))
        XCTAssertEqual(diffs.count, 1)
        XCTAssertEqual(diffs[0].kind, .identical)
        let s = FieldDiffEngine.Summary(diffs: diffs)
        XCTAssertFalse(s.hasConflicts)
        XCTAssertEqual(s.identical.count, 1)
    }

    func test_independent_when_only_local_changed() {
        let diffs = FieldDiffEngine.diff(
            base: obj(["title": .string("old")]),
            local: obj(["title": .string("local new")]),
            server: obj(["title": .string("old")]))
        XCTAssertEqual(diffs[0].kind, .independent)
    }

    func test_independent_when_only_server_changed() {
        let diffs = FieldDiffEngine.diff(
            base: obj(["title": .string("old")]),
            local: obj(["title": .string("old")]),
            server: obj(["title": .string("server new")]))
        XCTAssertEqual(diffs[0].kind, .independent)
    }

    func test_conflicting_when_all_three_differ() {
        let diffs = FieldDiffEngine.diff(
            base: obj(["severity": .string("low")]),
            local: obj(["severity": .string("high")]),
            server: obj(["severity": .string("medium")]))
        XCTAssertEqual(diffs[0].kind, .conflicting)
        let s = FieldDiffEngine.Summary(diffs: diffs)
        XCTAssertTrue(s.hasConflicts)
        XCTAssertEqual(s.conflicting.map(\.field), ["severity"])
    }

    func test_missing_key_counts_as_null_and_can_conflict() {
        // local added a field, server removed it -> base had it:
        // base=present, local=present(new), server=absent(null)
        let diffs = FieldDiffEngine.diff(
            base: obj(["x": .number(1)]),
            local: obj(["x": .number(2)]),
            server: obj([:]))
        XCTAssertEqual(diffs[0].kind, .conflicting)
    }

    func test_field_union_is_sorted_and_complete() {
        let diffs = FieldDiffEngine.diff(
            base: obj(["a": .number(1), "c": .number(3)]),
            local: obj(["b": .number(2), "c": .number(3)]),
            server: obj(["a": .number(1), "d": .number(4)]))
        XCTAssertEqual(diffs.map(\.field), ["a", "b", "c", "d"])
    }

    func test_merged_value_local_wins_on_local_only_change() {
        let diffs = FieldDiffEngine.diff(
            base: obj(["a": .number(1), "b": .number(1)]),
            local: obj(["a": .number(2), "b": .number(1)]),
            server: obj(["a": .number(1), "b": .number(9)]))
        let merged = FieldDiffEngine.mergedValue(
            diffs: diffs, resolving: [:])
        XCTAssertEqual(merged["a"], .number(2))   // local-only change
        XCTAssertEqual(merged["b"], .number(9))   // server-only change
    }

    func test_merged_value_conflict_uses_explicit_resolution() {
        let diffs = FieldDiffEngine.diff(
            base: obj(["sev": .string("low")]),
            local: obj(["sev": .string("high")]),
            server: obj(["sev": .string("medium")]))
        let merged = FieldDiffEngine.mergedValue(
            diffs: diffs,
            resolving: ["sev": .string("critical")])
        XCTAssertEqual(merged["sev"], .string("critical"))
    }
}
