"""M03-C01/C02: full EXIF orientations + extreme watermark layouts.

C02: EXIF defines EIGHT orientations (1..8). TM15/TM63 covered the
     four rotations (1,3,6,8); the mirrored variants (2,4,5,7) are
     rare in the wild but REAL (some scanners/edited files). This
     module completes the set with pixel maps + shared golden
     vectors (TM88 extension).

C01: extreme watermark layout cases for TM64: ultra-wide/tall
     aspects, tiny images, extreme long text (wrap-budget refusal
     proven), multi-language measured widths (CJK surrogate-safe
     widths are the CALLER's measurement - the engine sees pixels).
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ==================================================== C02: EXIF 1..8

# EXIF orientation numbers and their standard meanings:
# 1 = normal                        (0deg,   no flip)
# 2 = flip horizontal
# 3 = rotate 180
# 4 = flip vertical (aka rotate 180 + flip h)
# 5 = transpose (flip h + rotate 90 CW)   -> upright is H x W
# 6 = rotate 90 CW                       -> upright is H x W
# 7 = transverse (flip h + rotate 90 CCW)
# 8 = rotate 90 CCW                      -> upright is H x W
#
# Mapping contract: raw sensor buffer is W x H with origin at the
# TOP-LEFT of the RAW data. The upright (display) image may be H x W.
# The maps below return the upright pixel for a raw pixel (u, v).

_EXIF_ALIASES = {
    "up": 1, "down": 3, "left": 8, "right": 6,      # legacy names
    "portrait": 6, "portraitUpsideDown": 8,
    "landscapeLeft": 1, "landscapeRight": 3,
}


class ExifOrientationError(ValueError):
    pass


def exif_oriented_pixel(u: float, v: float, width: int, height: int,
                        exif: int) -> tuple[float, float]:
    """Raw-buffer pixel -> upright-image pixel for EXIF 1..8.

    Semantics (each case first applies the geometric transform to the
    RAW image, then we read out the pixel position):
      1: identity                    upright (W, H)
      2: mirror horizontally         upright (W, H)
      3: rotate 180                  upright (W, H)
      4: mirror vertically           upright (W, H)
      5: transpose (mirror + 90cw)   upright (H, W)
      6: rotate 90 CW                upright (H, W)
      7: transverse (mirror + 90ccw) upright (H, W)
      8: rotate 90 CCW               upright (H, W)
    """
    if exif not in (1, 2, 3, 4, 5, 6, 7, 8):
        raise ExifOrientationError(f"EXIF orientation must be 1..8, "
                                   f"got {exif}")
    if width <= 0 or height <= 0:
        raise ExifOrientationError("non-positive dimensions")
    if not (math.isfinite(u) and math.isfinite(v)):
        raise ExifOrientationError("non-finite pixel")
    w = float(width - 1)
    h = float(height - 1)
    if exif == 1:
        return u, v
    if exif == 2:
        return w - u, v
    if exif == 3:
        return w - u, h - v
    if exif == 4:
        return u, h - v
    if exif == 5:
        # transpose: (u, v) -> (v, u) on an HxW upright grid
        return v, u
    if exif == 6:
        # EXIF 6 official: 0th row = visual RIGHT side, 0th column =
        # visual TOP -> row v -> u' = H-1-v; col u -> v' = u
        return h - v, u
    if exif == 7:
        # EXIF 7 (transverse): 0th row = RIGHT, 0th col = BOTTOM
        # -> (H-1-v, W-1-u)
        return h - v, w - u
    # exif == 8:
    # EXIF 8 official: 0th row = visual LEFT side, 0th column =
    # visual BOTTOM -> (v, W-1-u)
    return v, w - u


def exif_upright_dimensions(width: int, height: int,
                            exif: int) -> tuple[int, int]:
    if exif not in (1, 2, 3, 4, 5, 6, 7, 8):
        raise ExifOrientationError(f"EXIF orientation must be 1..8")
    if exif in (5, 6, 7, 8):
        return height, width
    return width, height


def exif_from_name(name: str) -> int:
    n = name.lower()
    if n.isdigit() and int(n) in (1, 2, 3, 4, 5, 6, 7, 8):
        return int(n)
    if n in _EXIF_ALIASES:
        return _EXIF_ALIASES[n]
    raise ExifOrientationError(f"unknown orientation name {name!r}")


# ================================================ C01: extreme layouts

from techlab.geometry.watermark_layout import (  # noqa: E402
    LayoutError, MeasuredLine, compute_layout)


@dataclass(frozen=True)
class ExtremeCase:
    name: str
    image_size: tuple[int, int]
    corner: str
    margin: float
    gap: float
    lines: list[MeasuredLine]
    logo: tuple[float, float] | None
    expect: str            # 'ok' | 'refuse:<substr>'
    note: str = ""


def extreme_cases() -> list[ExtremeCase]:
    """The C01 case book: every extreme the task lists, with the
    expected behavior (a layout result or a NAMED refusal)."""
    cases: list[ExtremeCase] = []

    def line(text: str, w: float, h: float) -> MeasuredLine:
        return MeasuredLine(text, False, w, h)

    # ultra-wide panorama: bottom-right corner must stay inside
    cases.append(ExtremeCase(
        "ultrawide-4000x800-bottomright",
        image_size=(4000, 800), corner="bottom_right",
        margin=30, gap=8, logo=None,
        lines=[line("12 River Rd", 260, 40),
               line("2026-11-27 06:30", 200, 36)],
        expect="ok",
        note="extreme aspect 5:1"))

    # ultra-tall portrait
    cases.append(ExtremeCase(
        "ultratall-800x4000-topleft",
        image_size=(800, 4000), corner="top_left",
        margin=24, gap=8, logo=None,
        lines=[line("Site A", 120, 36)],
        expect="ok",
        note="extreme aspect 1:5"))

    # tiny image: 240px stamp - margin must not eat the frame
    cases.append(ExtremeCase(
        "tiny-240x160",
        image_size=(240, 160), corner="bottom_left",
        margin=6, gap=2, logo=None,
        lines=[line("X", 30, 12)],
        expect="ok",
        note="sub-300px image"))

    # tiny image + margin exceeding half the frame -> refuse, the
    # caller must shrink (never a silent clamp into invisibility)
    cases.append(ExtremeCase(
        "tiny-margin-overflow-refused",
        image_size=(200, 100), corner="bottom_left",
        margin=90, gap=4, logo=None,
        lines=[line("X", 20, 10)],
        expect="refuse:positive",        # width - 2*margin <= 0
        note="margin budget exceeds frame"))

    # extreme long text: wider than the wrap budget -> caller wraps
    cases.append(ExtremeCase(
        "longtext-nowrap-refused",
        image_size=(1920, 1080), corner="bottom_left",
        margin=16, gap=5, logo=None,
        lines=[line("x" * 200, 3800, 24)],   # measured 3800px
        expect="refuse:wrap the text",
        note="caller must wrap before layout"))

    # many lines exceeding the height budget -> named refusal
    cases.append(ExtremeCase(
        "many-lines-height-refused",
        image_size=(600, 400), corner="bottom_left",
        margin=10, gap=4, logo=None,
        lines=[line(f"L{i}", 200, 40) for i in range(20)],
        expect="refuse:never a silent clip",
        note="20 lines x 44px = 880px > 55% of 400"))

    # logo + text where the logo starves the text budget
    cases.append(ExtremeCase(
        "logo-starves-text-refused",
        image_size=(800, 600), corner="top_right",
        margin=12, gap=6, logo=(700, 200),
        lines=[line("note", 120, 24)],
        expect="refuse:wrap the text",
        note="800 - 700 - 36 = 64px text budget < 120px line"))

    # CJK widths: the engine sees PIXELS (the caller measured with
    # the CJK-aware font); a 6-CJK-char line measured at 6*48=288px
    cases.append(ExtremeCase(
        "cjk-1920x1080",
        image_size=(1920, 1080), corner="bottom_left",
        margin=16, gap=5, logo=None,
        lines=[line("施工区域通道", 288, 44),
               line("2026-11-27", 200, 36)],
        expect="ok",
        note="CJK measured widths are caller-supplied pixels"))

    # logo fits with tight text
    cases.append(ExtremeCase(
        "logo-plus-text-ok",
        image_size=(1920, 1080), corner="top_left",
        margin=16, gap=6, logo=(180, 80),
        lines=[line("Project A", 240, 40),
               line("27 Nov 2026", 210, 36)],
        expect="ok",
        note="healthy layout with logo"))
    return cases


def run_extreme_cases() -> list[dict]:
    """Execute the case book; each entry becomes a golden-vector
    record with either the layout rects or the refusal reason."""
    out: list[dict] = []
    for c in extreme_cases():
        entry = {"name": c.name, "note": c.note,
                 "inputs": {
                     "image_size": list(c.image_size),
                     "corner": c.corner, "margin": c.margin,
                     "gap": c.gap,
                     "lines": [[ln.text, ln.width_px, ln.height_px]
                               for ln in c.lines],
                     "logo": list(c.logo) if c.logo else None},
                 }
        try:
            result = compute_layout(
                image_size=c.image_size, corner=c.corner,
                margin_px=c.margin, line_gap_px=c.gap,
                lines=c.lines, logo_size=c.logo)
            entry["expect"] = "ok"
            entry["block"] = [result.block_rect.x,
                              result.block_rect.y,
                              result.block_rect.w,
                              result.block_rect.h]
            entry["logo_rect"] = None if result.logo_rect is None \
                else [result.logo_rect.x, result.logo_rect.y,
                      result.logo_rect.w, result.logo_rect.h]
            entry["line_rects"] = [[r.x, r.y, r.w, r.h]
                                   for r in result.line_rects]
        except (LayoutError, ValueError) as e:
            entry["expect"] = "refuse"
            entry["refusal"] = str(e)
        out.append(entry)
    return out


# -------------------------------------------------- vector generation

VEC_OUT = (Path(__file__).resolve().parents[2]
           / "test-vectors" / "geometry"
           / "exif-watermark-extreme-vectors.json")


def generate_vectors() -> Path:
    """Emit the C01/C02 shared golden vectors (TM88 extension)."""
    exif_cases = []
    w, h = 1920, 1080
    probes = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
              (960, 540)]
    for exif in range(1, 9):
        for (u, v) in probes:
            ou, ov = exif_oriented_pixel(u, v, w, h, exif)
            exif_cases.append({
                "name": f"exif{exif}-{u}-{v}",
                "exif": exif, "width": w, "height": h,
                "u": u, "v": v,
                "out_u": ou, "out_v": ov,
                "upright_w": exif_upright_dimensions(w, h,
                                                     exif)[0],
                "upright_h": exif_upright_dimensions(w, h,
                                                     exif)[1]})
    doc = {
        "version": "exif-watermark-extreme/1.0",
        "generator": "backend/tools/m03_c01_c02_gen_vectors.py",
        "swift_status": "CROSS_PLATFORM_PENDING",
        "exif_cases": exif_cases,
        "watermark_extremes": run_extreme_cases(),
    }
    VEC_OUT.parent.mkdir(parents=True, exist_ok=True)
    VEC_OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                       encoding="utf-8")
    return VEC_OUT


if __name__ == "__main__":
    print(f"{len(generate_vectors().read_text(encoding='utf-8'))} "
          f"bytes written")
