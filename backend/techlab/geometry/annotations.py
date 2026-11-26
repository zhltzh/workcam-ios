"""TM63: annotation geometry engine (rendering-agnostic).

THE CONTRACT:
  - coordinates are NORMALIZED (0..1, top-left origin) so annotations
    survive any resolution/aspect changes; pixels are a VIEW concern
  - this module is PURE GEOMETRY: shapes, transforms, hit-testing and
    an undo/redo command model. UIKit/SwiftUI/CoreGraphics never
    appear here - the Mac drawing layer consumes the outputs
  - the ORIGINAL PHOTO is never modified: annotations are stored
    beside the photo and rendered into DERIVATIVES only (task rule)

Components:
  Annotation            - normalized shape model (all 9 kinds)
  transforms            - EXIF orientation, crop, scale round-trips
  hit-testing           - point-in-shape with a pixel-based slop
  AnnotationDocument    - z-ordered collection + undo/redo command
                          stack (add/update/soft-delete/resurrect)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from techlab.geometry.camera import (CropRect, _require_finite,
                                      oriented_pixel_to_upright,
                                      upright_dimensions)


class AnnotationKind(str, Enum):
    point = "point"
    arrow = "arrow"
    rectangle = "rectangle"
    ellipse = "ellipse"
    freehand = "freehand"
    text = "text"
    polygon = "polygon"
    measurement_line = "measurement_line"
    number_marker = "number_marker"


@dataclass(frozen=True)
class Annotation:
    annotation_id: str
    kind: AnnotationKind
    points: list[tuple[float, float]]      # normalized (u, v) 0..1
    color_hex: str = "#FF3B30"
    stroke_width: float = 4.0              # DEVICE PIXELS at render
    text_content: str | None = None
    marker_number: int | None = None
    z_order: int = 0

    def __post_init__(self):
        if not self.points:
            raise ValueError("annotation needs at least one point")
        for u, v in self.points:
            _require_finite(u, v)
            if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
                raise ValueError(
                    f"annotation point ({u}, {v}) outside the "
                    f"normalized frame - annotations are stored in "
                    f"normalized coordinates")
        if self.kind in (AnnotationKind.arrow,
                         AnnotationKind.measurement_line) \
                and len(self.points) != 2:
            raise ValueError(f"{self.kind.value} needs exactly "
                             f"2 points")
        if self.kind == AnnotationKind.rectangle \
                and len(self.points) != 2:
            raise ValueError("rectangle needs 2 corner points")
        if self.kind in (AnnotationKind.freehand, AnnotationKind.polygon) \
                and len(self.points) < 3:
            raise ValueError(f"{self.kind.value} needs >= 3 points")
        if self.kind == AnnotationKind.text \
                and not self.text_content:
            raise ValueError("text annotation needs text_content")

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """Normalized bounding box (min_u, min_v, max_u, max_v)."""
        us = [p[0] for p in self.points]
        vs = [p[1] for p in self.points]
        return min(us), min(vs), max(us), max(vs)


# ------------------------------------------------------------- transforms

def transform_annotation(a: Annotation, orientation: str,
                          crop: CropRect | None = None) -> Annotation:
    """Map an annotation through the photo's display pipeline:
    raw-buffer normalized coords -> upright -> crop -> normalized.

    The math moves through a canonical pixel grid so the TM15
    orientation maps (which swap axes at 90-degree rotations) apply
    exactly; point counts and kinds never change. orientation is the
    same vocabulary as oriented_pixel_to_upright (up/down/left/
    right/portrait/...)."""
    W = H = 1000            # canonical grid: precision is preserved
    # by staying in [0, 999] pixels then renormalizing
    px = [(u * (W - 1), v * (H - 1)) for u, v in a.points]
    oriented = [oriented_pixel_to_upright(u, v, W, H, orientation)
                for u, v in px]
    if crop is not None:
        out = []
        for u, v in oriented:
            nu = (u / (W - 1) - crop.x) / crop.w
            nv = (v / (H - 1) - crop.y) / crop.h
            if not (0.0 <= nu <= 1.0 and 0.0 <= nv <= 1.0):
                raise ValueError(
                    "annotation point falls outside the crop - the "
                    "caller decides whether to drop the annotation")
            out.append((nu, nv))
    else:
        # upright dimensions may swap for 90-degree orientations;
        # renormalize against the UPRIGHT grid
        uw, uh = upright_dimensions(W, H, orientation)
        out = [(u / (uw - 1), v / (uh - 1)) for u, v in oriented]
    return Annotation(annotation_id=a.annotation_id, kind=a.kind,
                      points=out, color_hex=a.color_hex,
                      stroke_width=a.stroke_width,
                      text_content=a.text_content,
                      marker_number=a.marker_number, z_order=a.z_order)


# ------------------------------------------------------------- hit testing

def _dist_point_segment(px, py, ax, ay, bx, by) -> float:
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab2 = abx * abx + aby * aby
    if ab2 < 1e-18:
        return math.hypot(apx, apy)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    return math.hypot(apx - t * abx, apy - t * aby)


def hit_test(a: Annotation, u: float, v: float,
             slop_px: float = 12.0,
             view_width: int = 1000,
             view_height: int = 1000) -> bool:
    """Is (u, v) (normalized) within slop_px of the annotation, at
    the given VIEW size (slop is a pixel-precision affordance, the
    finger-sized touch tolerance)."""
    _require_finite(u, v)
    su = slop_px / view_width
    sv = slop_px / view_height
    px, py = u, v
    if a.kind == AnnotationKind.point:
        x, y = a.points[0]
        return abs(px - x) <= su and abs(py - y) <= sv
    if a.kind in (AnnotationKind.arrow, AnnotationKind.measurement_line,
                  AnnotationKind.freehand):
        segs = list(zip(a.points, a.points[1:]))
        if a.kind == AnnotationKind.freehand and len(a.points) == 1:
            x, y = a.points[0]
            return abs(px - x) <= su and abs(py - y) <= sv
        for (x1, y1), (x2, y2) in segs:
            if _dist_point_segment(px, py, x1, y1, x2, y2) <= \
                    max(su, sv):
                return True
        return False
    if a.kind == AnnotationKind.rectangle:
        (x1, y1), (x2, y2) = a.points
        lo_u, hi_u = min(x1, x2), max(x1, x2)
        lo_v, hi_v = min(y1, y2), max(y1, y2)
        return lo_u - su <= px <= hi_u + su and \
            lo_v - sv <= py <= hi_v + sv
    if a.kind == AnnotationKind.ellipse:
        # axis-aligned ellipse inside the rectangle
        (x1, y1), (x2, y2) = a.points
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        ru, rv = abs(x2 - x1) / 2, abs(y2 - y1) / 2
        if ru < 1e-12 or rv < 1e-12:
            return False
        eu = (px - cx) / (ru + su)
        ev = (py - cy) / (rv + sv)
        return eu * eu + ev * ev <= 1.0
    if a.kind == AnnotationKind.polygon:
        return _point_in_polygon(px, py, a.points) or \
            _on_polygon_edge(px, py, a.points, su, sv)
    if a.kind in (AnnotationKind.text, AnnotationKind.number_marker):
        x, y = a.points[0]
        return abs(px - x) <= su and abs(py - y) <= sv
    return False


def _point_in_polygon(px, py, pts) -> bool:
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > py) != (yj > py):
            x_at = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_at:
                inside = not inside
        j = i
    return inside


def _on_polygon_edge(px, py, pts, su, sv) -> bool:
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + [pts[0]]):
        if _dist_point_segment(px, py, x1, y1, x2, y2) <= \
                max(su, sv):
            return True
    return False


# --------------------------------------------------------- document + undo

@dataclass(frozen=True)
class _Command:
    """Immutable command; the document replays/undoes them. Soft
    delete keeps the record (deleted annotations stay queryable for
    history - matching the server's soft-delete semantics)."""
    op: str                     # add / update / delete
    annotation: Annotation
    previous: Annotation | None = None


class AnnotationDocument:
    """Z-ordered annotation collection with a command-based undo/redo
    stack. The photo itself is NEVER touched here - this document is
    metadata rendered into derivatives only."""

    def __init__(self) -> None:
        self._items: dict[str, Annotation] = {}
        self._deleted: dict[str, Annotation] = {}
        self._undo: list[_Command] = []
        self._redo: list[_Command] = []

    # -- queries ------------------------------------------------
    def visible(self) -> list[Annotation]:
        return sorted(self._items.values(), key=lambda a: a.z_order)

    def get(self, annotation_id: str) -> Annotation | None:
        return self._items.get(annotation_id)

    def hit_topmost(self, u: float, v: float, slop_px: float = 12.0,
                    view_width: int = 1000,
                    view_height: int = 1000) -> Annotation | None:
        """Topmost-first hit test (z_order descending)."""
        for a in reversed(self.visible()):
            if hit_test(a, u, v, slop_px, view_width, view_height):
                return a
        return None

    # -- mutations (all command-tracked) ------------------------
    def add(self, a: Annotation) -> None:
        if a.annotation_id in self._items:
            raise ValueError("annotation_id already present")
        self._apply(_Command("add", a, None))

    def update(self, new_version: Annotation) -> None:
        old = self._items.get(new_version.annotation_id)
        if old is None:
            raise KeyError(new_version.annotation_id)
        self._apply(_Command("update", new_version, old))

    def delete(self, annotation_id: str) -> None:
        """SOFT delete: hidden from visible(), kept in _deleted for
        history (matches the server's soft-delete discipline)."""
        a = self._items.get(annotation_id)
        if a is None:
            raise KeyError(annotation_id)
        self._apply(_Command("delete", a, a))

    def undo(self) -> bool:
        if not self._undo:
            return False
        cmd = self._undo.pop()
        self._revert(cmd)
        self._redo.append(cmd)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        cmd = self._redo.pop()
        self._apply(cmd)
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    # -- internals -----------------------------------------------
    def _apply(self, cmd: _Command) -> None:
        a = cmd.annotation
        if cmd.op == "add":
            self._items[a.annotation_id] = a
        elif cmd.op == "update":
            self._items[a.annotation_id] = a
        elif cmd.op == "delete":
            self._deleted[a.annotation_id] = a
            del self._items[a.annotation_id]
        self._undo.append(cmd)
        self._redo.clear()          # a new action kills the redo branch

    def _revert(self, cmd: _Command) -> None:
        a = cmd.annotation
        if cmd.op == "add":
            del self._items[a.annotation_id]
        elif cmd.op == "update":
            self._items[a.annotation_id] = cmd.previous      # type: ignore
        elif cmd.op == "delete":
            self._items[a.annotation_id] = self._deleted.pop(
                a.annotation_id)
