"""TM64: watermark layout engine tests - pure placement math, named
refusals, W011-semantics parity, all four corners, logo/text mix."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.geometry.watermark_layout import (  # noqa: E402
    LayoutError, MeasuredLine, compute_layout, luminance_panel_choice)

LINES = [MeasuredLine("12 River Rd Site", True, 180.0, 24.0),
         MeasuredLine("2026-11-26 15:40", False, 140.0, 22.0),
         MeasuredLine("31.2304N 121.4737E", False, 150.0, 22.0)]


def layout(**kw):
    base = dict(image_size=(1920, 1080), corner="bottom_left",
                margin_px=16, line_gap_px=5, lines=LINES)
    base.update(kw)
    return compute_layout(**base)


class TestBasics:
    def test_bottom_left_anchor(self):
        r = layout()
        assert r.block_rect.x == 16
        assert r.block_rect.y + r.block_rect.h == 1080 - 16
        assert r.corner == "bottom_left"
        assert r.order == ["panel", "logo", "lines"]

    def test_all_four_corners(self):
        for corner in ("bottom_left", "bottom_right",
                       "top_left", "top_right"):
            r = layout(corner=corner)
            b = r.block_rect
            assert b.x >= 0 and b.y >= 0
            assert b.x + b.w <= 1920 and b.y + b.h <= 1080
            if corner.startswith("bottom"):
                assert b.y + b.h == 1080 - 16
            else:
                assert b.y == 16
            if corner.endswith("right"):
                assert b.x + b.w == 1920 - 16
            else:
                assert b.x == 16

    def test_portrait_and_landscape(self):
        for w, h in ((1080, 1920), (1920, 1080), (750, 1334)):
            r = layout(image_size=(w, h))
            b = r.block_rect
            assert b.x >= 0 and b.y >= 0
            assert b.x + b.w <= w and b.y + b.h <= h

    def test_line_rects_stack_with_gap(self):
        r = layout()
        assert len(r.line_rects) == 3
        y0 = r.line_rects[0].y
        # each next line starts after height + gap
        assert r.line_rects[1].y == y0 + 24.0 + 5.0
        assert r.line_rects[2].y == y0 + (24.0 + 5.0) + (22.0 + 5.0)
        # all lines share the text origin x
        assert all(lr.x == r.text_origin[0] for lr in r.line_rects)


class TestLogo:
    def test_logo_rect_and_text_shift(self):
        """Bottom-anchored: the LINES keep their distance from the
        block's bottom edge, so adding a logo GROWS the block upward
        (py shrinks) and places the logo ABOVE the text - the invariant
        is logo_rect.bottom <= text_origin.y, plus block growth."""
        without = layout()
        with_logo = layout(logo_size=(120.0, 54.0))
        assert with_logo.logo_rect is not None
        assert with_logo.logo_rect.y + with_logo.logo_rect.h \
            <= with_logo.text_origin[1]
        # block grew by logo height + gap, anchored bottom -> py up
        expect_h = without.block_rect.h + 54.0 + 5.0
        assert with_logo.block_rect.h == pytest.approx(expect_h)
        assert with_logo.block_rect.y < without.block_rect.y
        # and the bottom edges still align (bottom-anchored)
        assert with_logo.block_rect.y + with_logo.block_rect.h == \
            pytest.approx(without.block_rect.y
                           + without.block_rect.h)

    def test_logo_narrows_text_budget(self):
        # base budget: 1920*0.62 = 1190.4px; logo trims to
        # min(budget, width - logo_w - 3*margin)
        big_logo = (1500.0, 200.0)
        budget_with_logo = min(1190.4, 1920 - 1500 - 48)
        assert budget_with_logo < 1190.4     # logo DOES narrow it
        wide = [MeasuredLine("x" * 60, False, 300.0, 22.0)]
        with pytest.raises(LayoutError):
            layout(lines=wide, logo_size=big_logo)
        # without the logo the wider budget fits
        r = layout(lines=wide)
        assert r.block_rect.w > 0


class TestRefusals:
    def test_nothing_to_render(self):
        with pytest.raises(LayoutError):
            layout(lines=[], logo_size=None)

    def test_oversized_line_named(self):
        huge = [MeasuredLine("way too long", False, 5000.0, 22.0)]
        with pytest.raises(LayoutError) as ei:
            layout(lines=huge)
        assert "wrap the text" in str(ei.value)

    def test_block_height_budget_named(self):
        many = [MeasuredLine(f"line {i}", False, 100.0, 60.0)
                for i in range(30)]
        with pytest.raises(LayoutError) as ei:
            layout(lines=many)
        assert "never a silent clip" in str(ei.value)

    def test_bad_params(self):
        with pytest.raises(LayoutError):
            layout(corner="middle")
        with pytest.raises(LayoutError):
            layout(image_size=(0, 100))
        with pytest.raises(LayoutError):
            layout(margin_px=-1)


class TestPanelChoice:
    def test_luminance_threshold(self):
        dark = luminance_panel_choice(0.8)
        light = luminance_panel_choice(0.2)
        assert dark["dark_background"] is True
        assert dark["panel_rgb"] == (20, 20, 20)
        assert dark["text_rgb"] == (255, 255, 255)
        assert light["dark_background"] is False
        assert light["panel_rgb"] == (245, 245, 245)
        assert light["text_rgb"] == (20, 20, 20)

    def test_luminance_range(self):
        with pytest.raises(LayoutError):
            luminance_panel_choice(1.5)
        with pytest.raises(LayoutError):
            luminance_panel_choice(float("nan"))

class TestW011Parity:
    def test_margin_semantics_match_w011(self):
        """W011: margin = max(8, base*0.015), block_w = min(width -
        2*margin, text_w + 2*margin). The engine consumes the margin
        as an input; this pins the block_w formula."""
        r = layout()
        text_w = max(ln.width_px for ln in LINES)
        assert r.block_rect.w == pytest.approx(
            min(1920 - 32, text_w + 32))

    def test_clamp_keeps_block_inside_image(self):
        # narrow image, text within budget: the clamp must still hold
        r = layout(image_size=(400, 300),
                   lines=[MeasuredLine("abc", False, 200.0, 22.0)])
        b = r.block_rect
        assert b.x >= 0 and b.x + b.w <= 400
        assert b.y >= 0 and b.y + b.h <= 300
