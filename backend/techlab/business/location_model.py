"""TM-04 D07: engineering location hierarchy (area / floor / room /
spot) - the structured location model behind the free-text
`location_description`.

Contract (mirrored EXACTLY by WorkCamBusiness/LocationModel.swift):

  - a path is up to four named levels; deeper nesting is refused
  - normalization: strip per-segment whitespace, collapse inner
    runs to single spaces, empty segments are REFUSED (a path must
    be explicit - "A//Room" never silently becomes "A/Room")
  - the DISPLAY key keeps original casing; the INDEX key is the
    casefolded, whitespace-normalized join - searches are
    case-insensitive, storage is not
  - prefix matching: a query ancestor path matches all descendants
    (area "Building A" matches every floor/room under it)
  - path safety: segments refuse path separators and control
    characters so a location can never smuggle a traversal string

Pure functions, no I/O. Wall-clock free.
"""

from __future__ import annotations

import re

MAX_LEVELS = 4
LEVEL_NAMES = ("area", "floor", "room", "spot")

_FORBIDDEN = re.compile(r"[/\\\x00-\x1f\x7f]")


class LocationError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _normalize_segment(segment: str, level: str) -> str:
    if segment is None:
        raise LocationError("empty_segment", level)
    collapsed = " ".join(segment.split())
    if not collapsed:
        raise LocationError("empty_segment", level)
    if _FORBIDDEN.search(collapsed):
        raise LocationError("unsafe_segment", collapsed)
    if len(collapsed) > 64:
        raise LocationError("segment_too_long", f"{level} >64 chars")
    return collapsed


class LocationPath:
    """One structured location. Immutable."""

    __slots__ = ("_segments", "_index_key")

    def __init__(self, *segments: str):
        if not segments:
            raise LocationError("empty_path",
                                "at least the area is required")
        if len(segments) > MAX_LEVELS:
            raise LocationError(
                "too_deep",
                f"{len(segments)} levels > {MAX_LEVELS}")
        self._segments = tuple(
            _normalize_segment(s, LEVEL_NAMES[i])
            for i, s in enumerate(segments))
        self._index_key = "/".join(
            s.casefold() for s in self._segments)

    @classmethod
    def parse(cls, joined: str) -> "LocationPath":
        """Parse a stored/display key. Empty MIDDLE segments are a
        hard refusal (explicitness rule)."""
        if joined is None:
            raise LocationError("empty_path", "None")
        parts = joined.split("/")
        return cls(*parts)

    @property
    def segments(self) -> tuple[str, ...]:
        return self._segments

    @property
    def area(self) -> str:
        return self._segments[0]

    @property
    def floor(self) -> str | None:
        return self._segments[1] if len(self._segments) > 1 else None

    @property
    def room(self) -> str | None:
        return self._segments[2] if len(self._segments) > 2 else None

    @property
    def depth(self) -> int:
        return len(self._segments)

    @property
    def index_key(self) -> str:
        return self._index_key

    @property
    def display(self) -> str:
        return "/".join(self._segments)

    def is_descendant_of(self, ancestor: "LocationPath") -> bool:
        """Prefix match, segment-aligned and case-insensitive
        (never a raw string prefix: 'a/b' must not match 'a/bc')."""
        if ancestor.depth > self.depth:
            return False
        return all(
            a.casefold() == b.casefold()
            for a, b in zip(self._segments, ancestor.segments))

    def __eq__(self, other) -> bool:
        return isinstance(other, LocationPath) and \
            other._index_key == self._index_key

    def __hash__(self) -> int:
        return hash(self._index_key)

    def __repr__(self) -> str:
        return f"LocationPath({self.display!r})"


def location_matches(query: LocationPath | None,
                     candidate: LocationPath) -> bool:
    """Index predicate: no query matches everything; a query
    matches the candidate itself and every descendant."""
    if query is None:
        return True
    if query == candidate:
        return True
    return candidate.is_descendant_of(query)


def group_by_area(paths: list[LocationPath]) -> dict[str, list[LocationPath]]:
    """Report grouping: paths grouped by their casefolded area key,
    insertion order preserved within groups."""
    out: dict[str, list[LocationPath]] = {}
    for p in paths:
        out.setdefault(p.area.casefold(), []).append(p)
    return out
