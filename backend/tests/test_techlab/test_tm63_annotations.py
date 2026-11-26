"""TM63: annotation geometry engine tests - model, transforms,
hit-testing, undo/redo."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.geometry.annotations import (  # noqa: E402
    Annotation, AnnotationDocument, AnnotationKind, hit_test,
    transform_annotation)
from techlab.geometry.camera import CropRect  # noqa: E402


def arrow() -> Annotation:
    return Annotation(annotation_id="a1", kind=AnnotationKind.arrow,
                      points=[(0.2, 0.2), (0.8, 0.6)],
                      text_content=None, z_order=1)


def pts_close(got, want, tol=1e-9):
    assert len(got) == len(want), (got, want)
    for (gu, gv), (wu, wv) in zip(got, want):
        assert abs(gu - wu) <= tol and abs(gv - wv) <= tol, \
            (got, want)


class TestModel:
    def test_all_kinds_construct(self):
        shapes = [
            Annotation("p", AnnotationKind.point, [(0.5, 0.5)]),
            arrow(),
            Annotation("r", AnnotationKind.rectangle,
                       [(0.1, 0.1), (0.9, 0.9)]),
            Annotation("e", AnnotationKind.ellipse,
                       [(0.2, 0.3), (0.7, 0.8)]),
            Annotation("f", AnnotationKind.freehand,
                       [(0, 0), (0.1, 0.5), (0.3, 0.7), (0.6, 0.4)]),
            Annotation("t", AnnotationKind.text, [(0.5, 0.5)],
                       text_content="crack"),
            Annotation("g", AnnotationKind.polygon,
                       [(0.1, 0.1), (0.5, 0.05), (0.6, 0.5),
                        (0.2, 0.8)]),
            Annotation("m", AnnotationKind.measurement_line,
                       [(0.2, 0.2), (0.8, 0.2)]),
            Annotation("n", AnnotationKind.number_marker, [(0.3, 0.3)],
                       marker_number=7),
        ]
        for s in shapes:
            assert s.bbox[2] >= s.bbox[0]      # bbox sanity

    def test_normalized_frame_enforced(self):
        with pytest.raises(ValueError):
            Annotation("bad", AnnotationKind.point, [(1.5, 0.5)])
        with pytest.raises(ValueError):
            Annotation("nan", AnnotationKind.point,
                       [(float("nan"), 0.5)])

    def test_kind_arity_enforced(self):
        with pytest.raises(ValueError):
            Annotation("a", AnnotationKind.arrow, [(0, 0)])      # 1
        with pytest.raises(ValueError):
            Annotation("a", AnnotationKind.polygon,
                       [(0, 0), (0.5, 0.5)])                     # 2
        with pytest.raises(ValueError):
            Annotation("a", AnnotationKind.text, [(0.5, 0.5)],
                       text_content=None)


class TestTransforms:
    def test_identity(self):
        a = arrow()
        out = transform_annotation(a, "up")
        assert out.points == [(0.2, 0.2), (0.8, 0.6)]

    def test_180(self):
        out = transform_annotation(arrow(), "down")
        pts_close(out.points, [(0.8, 0.8), (0.2, 0.4)])

    def test_90_rotation_swaps_axes(self):
        # EXIF right (90 CCW): (u, v) -> (v, 1 - u)
        out = transform_annotation(arrow(), "right")
        pts_close(out.points, [(0.2, 0.8), (0.6, 0.2)])

    def test_crop_transform(self):
        crop = CropRect(x=0.25, y=0.25, w=0.5, h=0.5)
        # (0.2, 0.2) is outside the crop -> refusal
        with pytest.raises(ValueError):
            transform_annotation(arrow(), "up",
                                 crop=crop)
        inside = Annotation("in", AnnotationKind.point, [(0.5, 0.5)])
        out2 = transform_annotation(inside, "up",
                                    crop=crop)
        pts_close(out2.points, [(0.5, 0.5)])

    def test_crop_maps_into_unit_frame(self):
        crop = CropRect(x=0.0, y=0.0, w=0.5, h=0.5)
        pt = Annotation("pt", AnnotationKind.point, [(0.25, 0.25)])
        out = transform_annotation(pt, "up", crop=crop)
        pts_close(out.points, [(0.5, 0.5)])


class TestHitTest:
    def test_arrow_hit_on_segment(self):
        a = arrow()
        assert hit_test(a, 0.5, 0.4, slop_px=12) is True   # on line
        assert hit_test(a, 0.05, 0.9, slop_px=12) is False

    def test_rectangle_interior_and_edges(self):
        r = Annotation("r", AnnotationKind.rectangle,
                       [(0.2, 0.2), (0.8, 0.8)])
        assert hit_test(r, 0.5, 0.5) is True
        assert hit_test(r, 0.19, 0.19) is True     # within slop
        assert hit_test(r, 0.05, 0.05) is False

    def test_ellipse_inside_outside(self):
        e = Annotation("e", AnnotationKind.ellipse,
                       [(0.2, 0.2), (0.8, 0.8)])
        assert hit_test(e, 0.5, 0.5) is True       # center
        assert hit_test(e, 0.21, 0.21) is False    # corner region

    def test_polygon_fill_and_edge(self):
        g = Annotation("g", AnnotationKind.polygon,
                       [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8),
                        (0.2, 0.8)])
        assert hit_test(g, 0.5, 0.5) is True
        assert hit_test(g, 0.1, 0.5) is False
        assert hit_test(g, 0.2, 0.2) is True       # vertex on edge

    def test_freehand_polyline(self):
        f = Annotation("f", AnnotationKind.freehand,
                       [(0.1, 0.1), (0.4, 0.4), (0.7, 0.2)])
        assert hit_test(f, 0.4, 0.4) is True
        assert hit_test(f, 0.95, 0.95) is False


class TestDocumentUndoRedo:
    def test_add_update_delete_undo_all(self):
        doc = AnnotationDocument()
        a = arrow()
        doc.add(a)
        assert len(doc.visible()) == 1
        a2 = Annotation("a1", AnnotationKind.arrow,
                        [(0.2, 0.2), (0.9, 0.9)], z_order=1)
        doc.update(a2)
        assert doc.get("a1").points[1] == (0.9, 0.9)
        doc.delete("a1")
        assert doc.visible() == []
        # full undo chain back to empty
        assert doc.undo() and doc.get("a1") is not None   # un-delete
        assert doc.undo() and doc.get("a1").points[1] == (0.8, 0.6)
        assert doc.undo() and doc.visible() == []
        assert doc.undo() is False                        # stack end

    def test_redo_branch_cleared_by_new_action(self):
        doc = AnnotationDocument()
        doc.add(arrow())
        doc.undo()                                   # now empty
        assert doc.can_redo
        b = Annotation("b", AnnotationKind.point, [(0.5, 0.5)])
        doc.add(b)                                   # new action
        assert not doc.can_redo                      # branch killed

    def test_z_order_topmost_hit(self):
        doc = AnnotationDocument()
        low = Annotation("low", AnnotationKind.rectangle,
                         [(0.1, 0.1), (0.9, 0.9)], z_order=0)
        high = Annotation("high", AnnotationKind.point, [(0.5, 0.5)],
                           z_order=5)
        doc.add(low)
        doc.add(high)
        assert doc.hit_topmost(0.5, 0.5).annotation_id == "high"
        assert doc.hit_topmost(0.15, 0.15).annotation_id == "low"
        assert doc.hit_topmost(0.95, 0.95) is None

    def test_delete_is_soft_keeps_history(self):
        doc = AnnotationDocument()
        doc.add(arrow())
        doc.delete("a1")
        doc.undo()
        # resurrected, and the earlier delete did NOT erase the shape
        assert doc.get("a1") is not None
