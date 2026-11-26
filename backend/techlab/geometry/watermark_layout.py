"""TM64: watermark LAYOUT engine (rendering-free).

Extracts the W011 placement math into a PURE function so Windows
(Python) and Mac (Swift) can compute IDENTICAL layout rectangles
without either side owning a font engine:

  Inputs  (all measured by the CALLER):
    image_size (w, h)      upright image pixels
    corner                 bottom_left / bottom_right / top_left /
                           top_right (the W011 spec vocabulary)
    margin_px, line_gap_px  block padding
    lines                  [(text, bold, measured_width_px,
                            line_height_px)]  -- fonts MEASURED by
                            the caller (PIL / CoreText whichever)
    logo_size (w, h)       or None
    max_block_height_ratio, max_text_width_ratio (W011 semantics)

  Outputs:
    block_rect, logo_rect, text_origin, per-line rects, panel color
    decision inputs (from a caller-sampled luminance), render order,
    safe margins, and a NAMED refusal when the layout cannot fit.

The W011 geometry contract stays authoritative: this module must not
drift from app/services/watermark_render.py - the tests pin the
shared cases. Cross-platform 1px equality is claimed only after Mac
runs the Swift port against the same vectors (CROSS_PLATFORM_PENDING
rule of TM24: geometry targets vs pixel rendering are evaluated
separately)."""

from __future__ import annotations

from dataclasses import dataclass, field

from techlab.geometry.camera import _require_finite


class LayoutError(ValueError):
    """Named refusal - the caller surfaces it (shrink text / drop
    logo / pick another corner), never a silent clip."""


@dataclass(frozen=True)
class MeasuredLine:
    text: str
    bold: bool
    width_px: float
    height_px: float


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class LayoutResult:
    block_rect: Rect
    logo_rect: Rect | None
    line_rects: list[Rect]
    text_origin: tuple[float, float]
    order: list[str] = field(default_factory=lambda: [
        "panel", "logo", "lines"])     # W011 draw order
    safe_margin_px: float = 0.0
    panel_for_dark_background: tuple[int, int, int] = (20, 20, 20)
    panel_for_light_background: tuple[int, int, int] = (245, 245, 245)
    corner: str = "bottom_left"


CORNERS = ("bottom_left", "bottom_right", "top_left", "top_right")


def compute_layout(image_size: tuple[int, int],
                   corner: str,
                   margin_px: float,
                   line_gap_px: float,
                   lines: list[MeasuredLine],
                   logo_size: tuple[float, float] | None = None,
                   max_block_height_ratio: float = 0.55,
                   max_text_width_ratio: float = 0.62,
                   clamp_to_image: bool = True) -> LayoutResult:
    """Pure placement math (W011 semantics, rendering-free)."""
    width, height = image_size
    if width <= 0 or height <= 0:
        raise LayoutError("image dimensions must be positive")
    if corner not in CORNERS:
        raise LayoutError(f"unknown corner {corner!r}")
    if margin_px < 0 or line_gap_px < 0:
        raise LayoutError("margins must be non-negative")
    if not 0 < max_block_height_ratio <= 1:
        raise LayoutError("max_block_height_ratio out of range")
    if not 0 < max_text_width_ratio <= 1:
        raise LayoutError("max_text_width_ratio out of range")
    for ln in lines:
        _require_finite(ln.width_px, ln.height_px)
        if ln.width_px < 0 or ln.height_px < 0:
            raise LayoutError("measured line sizes must be >= 0")
    if not lines and logo_size is None:
        raise LayoutError(
            "nothing to render: no lines and no logo - enable at "
            "least one field (W011 all-toggles-off rule)")

    logo_w = logo_h = 0.0
    if logo_size is not None:
        logo_w, logo_h = logo_size
        if logo_w <= 0 or logo_h <= 0:
            raise LayoutError("logo size must be positive")

    # ---- text block width (W011: widest measured line vs logo) ----
    max_text_width = width * max_text_width_ratio
    if logo_size is not None:
        # logo narrows the text area (W011: width - logo - 3*margin)
        max_text_width = min(max_text_width,
                             width - logo_w - 3 * margin_px)
    text_w = max((ln.width_px for ln in lines), default=0.0)
    if logo_size is not None:
        text_w = max(text_w, logo_w)
    if text_w > max_text_width:
        raise LayoutError(
            f"widest measured line {text_w:.0f}px exceeds the "
            f"{max_text_width:.0f}px budget - wrap the text before "
            f"laying out (caller's wrap step)")
    block_w = min(width - 2 * margin_px, text_w + 2 * margin_px)

    # ---- block height ---------------------------------------------
    logo_gap = line_gap_px if logo_size is not None else 0.0
    block_h = (margin_px * 2 + logo_h + logo_gap
               + sum(ln.height_px + line_gap_px for ln in lines))
    if block_h > height * max_block_height_ratio:
        raise LayoutError(
            f"block height {block_h:.0f}px exceeds the "
            f"{max_block_height_ratio:.0%} budget - reduce lines or "
            f"shrink the font (never a silent clip)")

    # ---- corner placement ------------------------------------------
    if corner == "bottom_left":
        px, py = margin_px, height - block_h - margin_px
    elif corner == "bottom_right":
        px, py = width - block_w - margin_px, \
            height - block_h - margin_px
    elif corner == "top_left":
        px, py = margin_px, margin_px
    else:                                   # top_right
        px, py = width - block_w - margin_px, margin_px
    if clamp_to_image:
        px = max(0.0, min(px, width - block_w))
        py = max(0.0, min(py, height - block_h))

    # ---- inner rects -------------------------------------------------
    logo_rect = None
    cx = px + margin_px
    cy = py + margin_px
    if logo_size is not None:
        logo_rect = Rect(cx, cy, logo_w, logo_h)
        cy += logo_h + line_gap_px
    text_origin = (cx, cy)
    line_rects: list[Rect] = []
    for ln in lines:
        line_rects.append(Rect(cx, cy, ln.width_px, ln.height_px))
        cy += ln.height_px + line_gap_px

    return LayoutResult(
        block_rect=Rect(px, py, block_w, block_h),
        logo_rect=logo_rect,
        line_rects=line_rects,
        text_origin=text_origin,
        safe_margin_px=margin_px,
        corner=corner)


def luminance_panel_choice(sample_luminance: float) -> dict:
    """W011 adaptive panel rule, rendering-free: the CALLER samples
    the region luminance (0..1, background brightness); the choice
    is deterministic on both platforms."""
    if not isinstance(sample_luminance, (int, float)) \
            or not 0.0 <= float(sample_luminance) <= 1.0 \
            or not float(sample_luminance) == float(sample_luminance):
        raise LayoutError("luminance must be a finite number within "
                          "[0, 1]")
    dark_bg = sample_luminance > 0.5
    return {
        "dark_background": dark_bg,
        "panel_rgb": (20, 20, 20) if dark_bg else (245, 245, 245),
        "text_rgb": (255, 255, 255) if dark_bg else (20, 20, 20),
        "rule": "dark background -> light panel + white text; "
                "light background -> dark panel + dark text",
    }
