"""TM-04 D07: location-hierarchy parity tests - the Python mirror
of LocationModelTests.swift. Every case has a Swift twin so the
contract cannot drift between languages."""

from __future__ import annotations

import pytest

from techlab.business.location_model import (
    LocationError,
    LocationPath,
    group_by_area,
    location_matches,
)


class TestStructure:
    def test_four_levels_parse_and_access(self):
        p = LocationPath.parse("Building A/Floor 3/Room 12/Panel P4")
        assert p.area == "Building A"
        assert p.floor == "Floor 3"
        assert p.room == "Room 12"
        assert p.depth == 4
        assert p.display == "Building A/Floor 3/Room 12/Panel P4"

    def test_partial_paths_are_legal(self):
        p = LocationPath("Roof")
        assert p.floor is None and p.room is None and p.depth == 1

    def test_whitespace_collapses_within_segments(self):
        p = LocationPath("  Building   A ", " Floor  3 ")
        assert p.area == "Building A" and p.floor == "Floor 3"


class TestRefusals:
    def test_empty_segment_is_refused_never_dropped(self):
        with pytest.raises(LocationError) as ei:
            LocationPath.parse("A//Room")
        assert ei.value.reason == "empty_segment"
        with pytest.raises(LocationError):
            LocationPath("A", "  ")

    def test_deeper_than_four_levels_refused(self):
        with pytest.raises(LocationError) as ei:
            LocationPath("a", "b", "c", "d", "e")
        assert ei.value.reason == "too_deep"

    def test_path_separator_characters_refused(self):
        for hostile in ["../etc", "a\\b", "x\x01y"]:
            with pytest.raises(LocationError) as ei:
                LocationPath(hostile)
            assert ei.value.reason == "unsafe_segment"

    def test_long_segment_refused(self):
        with pytest.raises(LocationError):
            LocationPath("x" * 65)

    def test_no_segments_at_all_refused(self):
        with pytest.raises(LocationError) as ei:
            LocationPath()
        assert ei.value.reason == "empty_path"


class TestMatching:
    def test_equality_case_and_whitespace_insensitive(self):
        a = LocationPath("Building A", "Floor 3")
        b = LocationPath.parse("building  a/floor  3")
        assert a == b
        assert hash(a) == hash(b)

    def test_descendant_matching_segment_aligned(self):
        building = LocationPath("Building A")
        floor = LocationPath.parse("Building A/Floor 3")
        room = LocationPath.parse("Building A/Floor 3/Room 12")
        other = LocationPath.parse("Building AB")

        assert floor.is_descendant_of(building)
        assert room.is_descendant_of(building)
        assert room.is_descendant_of(floor)
        assert building.is_descendant_of(building)
        assert not other.is_descendant_of(building)   # a/b vs a/bc
        assert not floor.is_descendant_of(room)

    def test_query_matches_self_and_descendants_only(self):
        query = LocationPath.parse("Building A/Floor 3")
        assert location_matches(query, query)
        assert location_matches(
            query, LocationPath.parse("Building A/Floor 3/Room 12"))
        assert not location_matches(
            query, LocationPath.parse("Building A/Floor 2"))
        assert not location_matches(
            query, LocationPath("Building A"))
        assert location_matches(None, LocationPath("Roof"))


class TestGrouping:
    def test_group_by_area_preserves_insertion_order(self):
        paths = [
            LocationPath.parse("B/R1"),
            LocationPath.parse("A/R1"),
            LocationPath.parse("b/R2"),
            LocationPath.parse("A/R2"),
        ]
        groups = group_by_area(paths)
        assert list(groups.keys()) == ["b", "a"]
        assert len(groups["b"]) == 2
        assert [p.display for p in groups["a"]] == ["A/R1", "A/R2"]
