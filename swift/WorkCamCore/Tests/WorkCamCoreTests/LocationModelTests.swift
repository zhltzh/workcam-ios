// TM-04 D07 tests: location hierarchy. REAL Windows execution.

import XCTest
import WorkCamModels

final class LocationModelTests: XCTestCase {

    func test_four_levels_parse_and_access() throws {
        let p = try LocationPath.parse("Building A/Floor 3/Room 12/Panel P4")
        XCTAssertEqual(p.area, "Building A")
        XCTAssertEqual(p.floor, "Floor 3")
        XCTAssertEqual(p.room, "Room 12")
        XCTAssertEqual(p.depth, 4)
        XCTAssertEqual(p.display, "Building A/Floor 3/Room 12/Panel P4")
    }

    func test_partial_paths_are_legal() throws {
        let p = try LocationPath("Roof")
        XCTAssertNil(p.floor)
        XCTAssertNil(p.room)
        XCTAssertEqual(p.depth, 1)
    }

    func test_whitespace_collapses_within_segments() throws {
        let p = try LocationPath("  Building   A ", " Floor  3 ")
        XCTAssertEqual(p.area, "Building A")
        XCTAssertEqual(p.floor, "Floor 3")
    }

    func test_empty_segment_is_refused_never_silently_dropped() {
        XCTAssertThrowsError(try LocationPath.parse("A//Room")) {
            error in
            guard case LocationModelError.emptySegment("floor")
                    = error else {
                return XCTFail("unexpected \(error)")
            }
        }
        XCTAssertThrowsError(
            try LocationPath("A", "  ")) { error in
            XCTAssertEqual(
                error as? LocationModelError,
                .emptySegment(level: "floor"))
        }
    }

    func test_deeper_than_four_levels_refused() {
        XCTAssertThrowsError(
            try LocationPath("a", "b", "c", "d", "e")) { error in
            XCTAssertEqual(
                error as? LocationModelError,
                .tooDeep(got: 5, max: 4))
        }
    }

    func test_path_separator_characters_refused_by_name() {
        for hostile in ["../etc", "a\\b", "x\u{01}y"] {
            XCTAssertThrowsError(
                try LocationPath(hostile)) { error in
                if case LocationModelError.unsafeSegment = error {} else {
                    XCTFail("expected unsafeSegment for \(hostile)")
                }
            }
        }
    }

    func test_equality_is_case_and_whitespace_insensitive() throws {
        let a = try LocationPath("Building A", "Floor 3")
        let b = try LocationPath.parse("building  a/floor  3")
        XCTAssertEqual(a, b)
        XCTAssertEqual(a.hashValue, b.hashValue)
    }

    func test_descendant_matching_is_segment_aligned() throws {
        let building = try LocationPath("Building A")
        let floor = try LocationPath.parse("Building A/Floor 3")
        let room = try LocationPath.parse("Building A/Floor 3/Room 12")
        let otherBuilding = try LocationPath.parse("Building AB")

        XCTAssertTrue(floor.isDescendant(of: building))
        XCTAssertTrue(room.isDescendant(of: building))
        XCTAssertTrue(room.isDescendant(of: floor))
        XCTAssertTrue(building.isDescendant(of: building))
        // 'a/b' never matches 'a/bc' (string-prefix trap)
        XCTAssertFalse(otherBuilding.isDescendant(of: building))
        XCTAssertFalse(floor.isDescendant(of: room))
    }

    func test_query_matches_self_and_descendants_only() throws {
        let query = try LocationPath.parse("Building A/Floor 3")
        XCTAssertTrue(LocationQuery.matches(query, candidate: query))
        XCTAssertTrue(LocationQuery.matches(
            query,
            candidate: try LocationPath.parse(
                "Building A/Floor 3/Room 12")))
        XCTAssertFalse(LocationQuery.matches(
            query,
            candidate: try LocationPath.parse("Building A/Floor 2")))
        XCTAssertFalse(LocationQuery.matches(
            query,
            candidate: try LocationPath.parse("Building A")))
        // nil query = unfiltered listing
        XCTAssertTrue(LocationQuery.matches(
            nil, candidate: try LocationPath("Roof")))
    }

    func test_group_by_area_preserves_insertion_order() throws {
        let paths = try [
            LocationPath.parse("B/R1"),
            LocationPath.parse("A/R1"),
            LocationPath.parse("b/R2"),
            LocationPath.parse("A/R2"),
        ]
        let groups = LocationQuery.groupByArea(paths)
        XCTAssertEqual(groups.count, 2)
        XCTAssertEqual(groups[0].area, "b")
        XCTAssertEqual(groups[0].paths.count, 2)
        XCTAssertEqual(groups[1].area, "a")
        XCTAssertEqual(groups[1].paths.map(\.display),
                       ["A/R1", "A/R2"])
    }

    func test_long_segment_refused() {
        let long = String(repeating: "x", count: 65)
        XCTAssertThrowsError(try LocationPath(long)) { error in
            if case LocationModelError.segmentTooLong = error {} else {
                XCTFail("expected segmentTooLong")
            }
        }
    }
}
