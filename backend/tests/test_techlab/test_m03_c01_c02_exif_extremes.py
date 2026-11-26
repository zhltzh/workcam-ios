"""M03-C01/C02: golden-vector consumer tests (EXIF 1..8 + extreme
watermark layouts). Python side; the Swift side runs the same file
on Mac (CROSS_PLATFORM_PENDING)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.geometry.watermark_layout import (  # noqa: E402
    LayoutError, MeasuredLine, compute_layout)
from tools.m03_c01_c02_gen_vectors import (  # noqa: E402
    exif_oriented_pixel, exif_upright_dimensions)

VEC = (Path(__file__).resolve().parents[3] / "test-vectors"
       / "geometry" / "exif-watermark-extreme-vectors.json")


def _load():
    doc = json.loads(VEC.read_text(encoding="utf-8"))
    assert doc["version"] == "exif-watermark-extreme/1.0"
    return doc


@pytest.mark.parametrize("case", _load()["exif_cases"],
                         ids=lambda c: c["name"])
def test_exif_case(case):
    ou, ov = exif_oriented_pixel(
        case["u"], case["v"], case["width"], case["height"],
        case["exif"])
    assert ou == pytest.approx(case["out_u"], abs=1e-9)
    assert ov == pytest.approx(case["out_v"], abs=1e-9)
    uw, uh = exif_upright_dimensions(
        case["width"], case["height"], case["exif"])
    assert (uw, uh) == (case["upright_w"], case["upright_h"])


class TestExifSemantics:
    def test_exif6_official_definition(self):
        """EXIF 6 official: the 0th RAW ROW forms the upright image's
        RIGHT side; the 0th RAW COLUMN forms the TOP. So raw
        top-right (W-1, 0) lands at the upright BOTTOM-right."""
        ou, ov = exif_oriented_pixel(1919, 0, 1920, 1080, 6)
        assert (ou, ov) == (1079, 1919)      # (H-1, W-1)
        # raw top-left (0, 0): 0th row -> right side, 0th col -> top
        ou2, ov2 = exif_oriented_pixel(0, 0, 1920, 1080, 6)
        assert (ou2, ov2) == (1079, 0)       # (H-1, 0)

    def test_exif8_official_definition(self):
        """EXIF 8 official: 0th row = LEFT side, 0th column = BOTTOM.
        raw top-left (0,0) lands at the upright LEFT column, and the
        0th column forms the bottom row."""
        ou, ov = exif_oriented_pixel(0, 0, 1920, 1080, 8)
        assert (ou, ov) == (0, 1919)

    def test_mirror2_flips_x_only(self):
        ou, ov = exif_oriented_pixel(0, 5, 100, 50, 2)
        assert ou == 99 and ov == 5

    def test_transpose5_swaps(self):
        ou, ov = exif_oriented_pixel(10, 20, 100, 50, 5)
        assert (ou, ov) == (20, 10)

    def test_tm15_name_to_exif_cross_reference(self):
        """REAL FINDING (C02 comparison): TM15's legacy orientation
        NAMES are TRANSPOSED vs standard EXIF numbers - its 'left'
        behavior implements EXIF 6 and its 'right' implements EXIF 8
        (the BEHAVIOR was consistent Python<->Swift, but the label
        would mislead a client reading EXIF tags). This test pins the
        ACTUAL mapping so the Mac side never wires an EXIF tag to
        the wrong name."""
        from techlab.geometry.camera import \
            oriented_pixel_to_upright as tm15
        w, h = 1920, 1080
        # TM15 'left' == standard EXIF 6
        for (u, v) in ((0, 0), (1919, 0), (960, 540), (0, 1079)):
            assert tm15(u, v, w, h, "left") == \
                exif_oriented_pixel(u, v, w, h, 6)
        # TM15 'right' == standard EXIF 8
        for (u, v) in ((0, 0), (1919, 0), (960, 540), (0, 1079)):
            assert tm15(u, v, w, h, "right") == \
                exif_oriented_pixel(u, v, w, h, 8)

    def test_bad_exif_refused(self):
        with pytest.raises(Exception):
            exif_oriented_pixel(0, 0, 10, 10, 9)
        with pytest.raises(Exception):
            exif_upright_dimensions(10, 10, 0)


@pytest.mark.parametrize("case", _load()["watermark_extremes"],
                         ids=lambda c: c["name"])
def test_extreme_case(case):
    lines = [MeasuredLine(t, False, w, h)
             for t, w, h in case["inputs"]["lines"]]
    logo = tuple(case["inputs"]["logo"]) \
        if case["inputs"]["logo"] else None
    if case["expect"] == "ok":
        result = compute_layout(
            image_size=tuple(case["inputs"]["image_size"]),
            corner=case["inputs"]["corner"],
            margin_px=case["inputs"]["margin"],
            line_gap_px=case["inputs"]["gap"],
            lines=lines, logo_size=logo)
        b = result.block_rect
        assert [b.x, b.y, b.w, b.h] == pytest.approx(
            case["block"], abs=1e-9)
        # every extreme-ok layout must stay inside the frame
        iw, ih = case["inputs"]["image_size"]
        assert 0 <= b.x and b.x + b.w <= iw
        assert 0 <= b.y and b.y + b.h <= ih
    else:
        with pytest.raises(LayoutError):
            compute_layout(
                image_size=tuple(case["inputs"]["image_size"]),
                corner=case["inputs"]["corner"],
                margin_px=case["inputs"]["margin"],
                line_gap_px=case["inputs"]["gap"],
                lines=lines, logo_size=logo)


def test_refusals_are_the_named_ones():
    doc = _load()
    refusals = {c["name"]: c.get("refusal", "")
                for c in doc["watermark_extremes"]
                if c["expect"] == "refuse"}
    assert "wrap the text" in refusals["longtext-nowrap-refused"]
    assert "never a silent clip" in \
        refusals["many-lines-height-refused"]
    assert "wrap the text" in refusals["logo-starves-text-refused"]
    assert "silent clip" in refusals["tiny-margin-overflow-refused"]
