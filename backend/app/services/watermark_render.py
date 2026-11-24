"""WC-W011 P0/P1 - engineering watermark data model & render engine.

THE PRODUCT RULE this module enforces in code:
  - UNVERIFIED DATA IS NEVER MARKED "Verified". The evidence-status
    line renders exactly what the server/evidence says - 'UNVERIFIED'
    stays 'UNVERIFIED' until a server-side verification says otherwise.
  - Client-claimed times are labeled as device time (untrusted);
    only server-anchored times may carry the '[server-anchored]' tag.
  - Manual GPS locations are labeled MANUAL - never presented as
    verified coordinates.
  - The watermark IMAGE is a DERIVATIVE, never the trusted original
    (W007 immutability); rendering never touches the original bytes.

Five English templates: Minimal / Construction / Inspection /
Corporate / Evidence. Real pixel rendering via Pillow (JPEG in/out,
EXIF orientation applied, luminance-adaptive colors, word wrap,
logo block).
"""

from __future__ import annotations

import hashlib
import json
import math
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ------------------------------------------------------------- P0: data model


class GpsSource(str, Enum):
    GPS = "gps"                # GNSS fix (evidence-grade claims possible)
    MANUAL = "manual"          # user-typed coordinates - NEVER "verified"
    NONE = "none"


class TimeTrust(str, Enum):
    DEVICE = "device"          # client-claimed only - untrusted
    SERVER_ANCHORED = "server_anchored"  # server receipt backs this time


class WatermarkFields(BaseModel):
    """Every field independently togglable (show_* flag per field). The
    value objects carry their OWN trust metadata so the renderer can
    label honestly without guessing."""

    project_name: str | None = Field(default=None, max_length=200)
    show_project_name: bool = False

    site_address: str | None = Field(default=None, max_length=300)
    show_site_address: bool = False

    captured_time: str | None = Field(default=None, max_length=64)
    captured_time_trust: TimeTrust = TimeTrust.DEVICE
    show_captured_time: bool = False

    server_anchored_time: str | None = Field(default=None, max_length=64)
    show_server_anchored_time: bool = False

    gps_latitude: float | None = Field(default=None, ge=-90, le=90)
    gps_longitude: float | None = Field(default=None, ge=-180, le=180)
    gps_source: GpsSource = GpsSource.NONE
    show_gps: bool = False

    gps_accuracy_m: float | None = Field(default=None, ge=0, le=100000)
    show_gps_accuracy: bool = False

    photo_id: str | None = Field(default=None, max_length=128)
    show_photo_id: bool = False

    operator: str | None = Field(default=None, max_length=128)
    show_operator: bool = False

    company_name: str | None = Field(default=None, max_length=200)
    show_company_name: bool = False

    company_logo: bytes | None = None      # decoded by the renderer
    show_company_logo: bool = False

    notes: str | None = Field(default=None, max_length=1000)
    show_notes: bool = False

    # evidence_status: the ONLY honest sources are the W006 evidence
    # states. A client may not type "VERIFIED" here and see it render.
    evidence_status: Literal[
        "unverified", "signature_valid", "cryptographically_valid",
        "verification_failed", "server_anchored", "no_evidence"
    ] = "no_evidence"
    show_evidence_status: bool = False

    @field_validator("gps_latitude", "gps_longitude", "gps_accuracy_m")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("NaN/Infinity not allowed")
        return v

    @model_validator(mode="after")
    def _gps_consistency(self) -> "WatermarkFields":
        if self.show_gps and self.gps_source == GpsSource.NONE:
            raise ValueError("show_gps=True requires a gps_source")
        if self.show_gps and self.gps_source != GpsSource.NONE \
                and (self.gps_latitude is None or self.gps_longitude is None):
            raise ValueError("gps coordinates required when source is set")
        return self

    def render_params_hash(self) -> str:
        """Deterministic identity of (fields, toggles) - the idempotency
        key for derivative generation. Logo bytes are hashed by content
        so identical logo => identical key."""
        payload = {
            "project_name": self.project_name if self.show_project_name else None,
            "site_address": self.site_address if self.show_site_address else None,
            "captured_time": self.captured_time if self.show_captured_time else None,
            "captured_time_trust": self.captured_time_trust.value if self.show_captured_time else None,
            "server_anchored_time": self.server_anchored_time if self.show_server_anchored_time else None,
            "gps": [self.gps_latitude, self.gps_longitude, self.gps_source.value]
                if self.show_gps else None,
            "gps_accuracy": self.gps_accuracy_m if self.show_gps_accuracy else None,
            "photo_id": self.photo_id if self.show_photo_id else None,
            "operator": self.operator if self.show_operator else None,
            "company_name": self.company_name if self.show_company_name else None,
            "logo_sha": hashlib.sha256(self.company_logo).hexdigest()[:16]
                if (self.show_company_logo and self.company_logo) else None,
            "notes": self.notes if self.show_notes else None,
            "evidence_status": self.evidence_status if self.show_evidence_status else None,
        }
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# --------------------------------------------------------- honest formatters


_EVIDENCE_LABELS = {
    "unverified": "UNVERIFIED",
    "signature_valid": "SIGNATURE VALID (key not registered)",
    "cryptographically_valid": "CRYPTOGRAPHICALLY VERIFIED",
    "verification_failed": "VERIFICATION FAILED",
    "server_anchored": "SERVER ANCHORED",
    "no_evidence": "NO EVIDENCE LINKED",
}


def format_time_line(fields: WatermarkFields) -> str | None:
    """Honest time rendering: device time is always labeled as such;
    server-anchored time is the only one carrying the trust tag."""
    lines: list[str] = []
    if fields.show_captured_time and fields.captured_time:
        if fields.captured_time_trust == TimeTrust.SERVER_ANCHORED:
            lines.append(f"{fields.captured_time} [server-anchored]")
        else:
            lines.append(f"{fields.captured_time} [device time, unverified]")
    if fields.show_server_anchored_time and fields.server_anchored_time:
        lines.append(f"{fields.server_anchored_time} [server-anchored]")
    return " | ".join(lines) if lines else None


def format_gps_line(fields: WatermarkFields) -> str | None:
    if not fields.show_gps:
        return None
    if fields.gps_source == GpsSource.MANUAL:
        return (f"GPS: MANUAL ENTRY "
                f"({fields.gps_latitude:.6f}, {fields.gps_longitude:.6f})")
    if fields.gps_source == GpsSource.GPS:
        acc = (f" +/-{fields.gps_accuracy_m:.0f}m"
               if fields.show_gps_accuracy and fields.gps_accuracy_m is not None
               else "")
        return (f"GPS: {fields.gps_latitude:.6f}, "
                f"{fields.gps_longitude:.6f}{acc}")
    return None


def format_evidence_line(fields: WatermarkFields) -> str | None:
    """The anti-forgery core: the label ALWAYS mirrors the actual
    evidence state. 'VERIFIED' can only appear when the state really is
    cryptographically_valid / server_anchored."""
    if not fields.show_evidence_status:
        return None
    return f"Evidence: {_EVIDENCE_LABELS[fields.evidence_status]}"


# ------------------------------------------------------------- P1: templates


class TemplateName(str, Enum):
    MINIMAL = "minimal"
    CONSTRUCTION = "construction"
    INSPECTION = "inspection"
    CORPORATE = "corporate"
    EVIDENCE = "evidence"


# Render-budget guard: refuse pathological inputs BEFORE decoding
# (decompression-bomb class). ~89 MP is Pillow's own default bomb
# threshold; we adopt a slightly stricter explicit product limit.
MAX_RENDER_PIXELS = 80_000_000


class TemplateSpec(BaseModel):
    """A template defines WHICH lines render and the block placement.
    The renderer computes geometry from the actual image size."""
    name: TemplateName
    version: str = "1"
    position: Literal["bottom_left", "bottom_right",
                      "top_left", "top_right"] = "bottom_right"
    # scale factor relative to image width (font size = factor * width)
    font_scale: float = Field(gt=0.005, le=0.1)
    # background block opacity (0 invisible .. 1 solid)
    panel_opacity: float = Field(ge=0.0, le=1.0)
    show_panel: bool = True
    include_logo: bool = False
    max_block_height_ratio: float = Field(gt=0.05, le=0.6)


_TEMPLATES: dict[TemplateName, TemplateSpec] = {
    TemplateName.MINIMAL: TemplateSpec(
        name=TemplateName.MINIMAL, position="bottom_right",
        font_scale=0.018, panel_opacity=0.45, max_block_height_ratio=0.18),
    TemplateName.CONSTRUCTION: TemplateSpec(
        name=TemplateName.CONSTRUCTION, position="bottom_left",
        font_scale=0.022, panel_opacity=0.60, include_logo=False,
        max_block_height_ratio=0.30),
    TemplateName.INSPECTION: TemplateSpec(
        name=TemplateName.INSPECTION, position="bottom_right",
        font_scale=0.020, panel_opacity=0.55, max_block_height_ratio=0.30),
    TemplateName.CORPORATE: TemplateSpec(
        name=TemplateName.CORPORATE, position="bottom_left",
        font_scale=0.024, panel_opacity=0.65, include_logo=True,
        max_block_height_ratio=0.35),
    TemplateName.EVIDENCE: TemplateSpec(
        name=TemplateName.EVIDENCE, position="bottom_right",
        font_scale=0.022, panel_opacity=0.70, include_logo=True,
        max_block_height_ratio=0.40),
}


def get_template(name: str) -> TemplateSpec:
    try:
        return _TEMPLATES[TemplateName(name)]
    except ValueError:
        raise ValueError(
            f"unknown template '{name}'; available: "
            f"{[t.value for t in TemplateName]}")


def template_lines(template: TemplateSpec,
                   fields: WatermarkFields) -> list[tuple[str, bool]]:
    """Ordered (text, bold) lines per template. Templates differ in
    which fields they surface; the FIELD TOGGLES always win - a line
    renders only when both the template includes it AND show_* is on.
    All text is pre-formatted (honest labels already applied)."""
    candidates: list[tuple[str, bool, str]] = []   # (key, bold, text)
    tl = format_time_line(fields)
    gl = format_gps_line(fields)
    el = format_evidence_line(fields)

    if template.name == TemplateName.MINIMAL:
        candidates = [
            ("photo_id", False, f"Photo: {fields.photo_id}"
             if fields.show_photo_id and fields.photo_id else None),
            ("time", False, tl),
            ("evidence", True, el),
        ]
    elif template.name == TemplateName.CONSTRUCTION:
        candidates = [
            ("project", True, fields.project_name
             if fields.show_project_name else None),
            ("site", False, fields.site_address
             if fields.show_site_address else None),
            ("time", False, tl),
            ("gps", False, gl),
            ("photo_id", False, f"Photo: {fields.photo_id}"
             if fields.show_photo_id and fields.photo_id else None),
            ("operator", False, f"Operator: {fields.operator}"
             if fields.show_operator and fields.operator else None),
        ]
    elif template.name == TemplateName.INSPECTION:
        candidates = [
            ("project", True, f"INSPECTION - {fields.project_name}"
             if fields.show_project_name else None),
            ("photo_id", False, f"Photo: {fields.photo_id}"
             if fields.show_photo_id and fields.photo_id else None),
            ("time", False, tl),
            ("gps", False, gl),
            ("operator", False, f"Inspector: {fields.operator}"
             if fields.show_operator and fields.operator else None),
            ("notes", False, fields.notes
             if fields.show_notes and fields.notes else None),
        ]
    elif template.name == TemplateName.CORPORATE:
        candidates = [
            ("company", True, fields.company_name
             if fields.show_company_name else None),
            ("project", False, fields.project_name
             if fields.show_project_name else None),
            ("site", False, fields.site_address
             if fields.show_site_address else None),
            ("time", False, tl),
            ("photo_id", False, f"ID: {fields.photo_id}"
             if fields.show_photo_id and fields.photo_id else None),
        ]
    else:   # EVIDENCE - the most complete, carries the trust lines
        candidates = [
            ("company", True, fields.company_name
             if fields.show_company_name else None),
            ("project", False, fields.project_name
             if fields.show_project_name else None),
            ("site", False, fields.site_address
             if fields.show_site_address else None),
            ("time", True, tl),
            ("gps", False, gl),
            ("photo_id", False, f"Photo: {fields.photo_id}"
             if fields.show_photo_id and fields.photo_id else None),
            ("operator", False, f"Operator: {fields.operator}"
             if fields.show_operator and fields.operator else None),
            ("notes", False, fields.notes
             if fields.show_notes and fields.notes else None),
            ("evidence", True, el),
        ]
    return [(text, bold) for _key, bold, text in candidates if text]


# --------------------------------------------------------- the renderer


def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont
    candidates = (["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"]
                  if bold else
                  ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf",
                   "helvetica.ttf"])
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf"
                                  if bold else "C:/Windows/Fonts/arial.ttf",
                                  size)
    except OSError:
        return ImageFont.load_default()


def _region_luminance(img, box) -> float:
    """Mean luminance 0..1 of a region (decides adaptive colors)."""
    from PIL import ImageStat
    region = img.crop(box).convert("L")
    stat = ImageStat.Stat(region)
    return stat.mean[0] / 255.0


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    """Greedy word wrap by measured width."""
    from PIL import ImageFont
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _apply_exif_orientation(img):
    """Transpose per EXIF tag 0x0112 so the watermark lands upright in
    the VISUALLY upright image. Returns (image, orientation_int)."""
    orientation = 1
    try:
        exif = img.getexif()
        orientation = exif.get(0x0112, 1) or 1
    except Exception:
        pass
    transposes = {2: ["FLIP_LEFT_RIGHT"], 3: ["ROTATE_180"],
                  4: ["FLIP_TOP_BOTTOM"], 5: ["FLIP_LEFT_RIGHT", "TRANSPOSE"],
                  6: ["ROTATE_270"], 7: ["FLIP_LEFT_RIGHT", "TRANSVERSE"],
                  8: ["ROTATE_90"]}
    for op in transposes.get(orientation, []):
        img = img.transpose(getattr(img, op) if hasattr(img, op)
                            else __import__("PIL.Image", fromlist=["Image"])
                            .Transpose.__dict__[op])
    return img, orientation


def render_watermark(original_bytes: bytes,
                     fields: WatermarkFields,
                     template_name: str) -> dict:
    """Render ONE derivative. Returns:
      image_bytes (JPEG), derivative_sha256, geometry (the cross-platform
    layout contract iOS reproduces), original untouched (guaranteed:
    the original bytes are only ever READ).
    """
    import io
    from PIL import Image, ImageDraw, ImageFont

    spec = get_template(template_name)
    img = Image.open(io.BytesIO(original_bytes))
    # oversize guard BEFORE any pixel work (decompression-bomb class)
    if img.width * img.height > MAX_RENDER_PIXELS:
        raise ValueError(
            f"image too large to render: {img.width}x{img.height} "
            f"({img.width * img.height} pixels exceeds the "
            f"{MAX_RENDER_PIXELS}-pixel render limit)")
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    img, exif_orientation = _apply_exif_orientation(img)
    width, height = img.size

    # scale from the SHORTER edge so portrait/landscape both work
    base = min(width, height)
    font_size = max(10, int(spec.font_scale * base))
    font = _load_font(font_size)
    bold_font = _load_font(font_size, bold=True)
    line_gap = max(2, font_size // 5)
    margin = max(8, int(base * 0.015))

    # build the wrapped lines
    max_text_width = int(width * 0.62)
    lines: list[tuple[str, bool]] = []      # (wrapped-text, bold)
    for text, bold in template_lines(spec, fields):
        use_font = bold_font if bold else font
        for wrapped in _wrap_text(text, use_font, max_text_width):
            lines.append((wrapped, bold))

    logo_h = 0
    logo_img = None
    if spec.include_logo and fields.show_company_logo and fields.company_logo:
        logo_img = Image.open(io.BytesIO(fields.company_logo)).convert("RGBA")
        logo_h = int(base * 0.05)
        ratio = logo_img.width / logo_img.height
        logo_img = logo_img.resize((int(logo_h * ratio), logo_h))
        max_text_width = min(max_text_width, width - logo_img.width
                              - 3 * margin)

    if not lines and logo_img is None:
        raise ValueError(
            "nothing to render: all field toggles are off for this "
            "template - enable at least one field")

    block_h = margin * 2 + logo_h + (line_gap if logo_img else 0)
    for _text, bold in lines:
        block_h += font_size + line_gap
    block_h = min(block_h, int(height * spec.max_block_height_ratio))

    # measure the actual widest line (post-wrap)
    text_w = 0
    for text, bold in lines:
        use_font = bold_font if bold else font
        text_w = max(text_w, int(use_font.getlength(text)))
    if logo_img is not None:
        text_w = max(text_w, logo_img.width)
    block_w = min(width - 2 * margin, text_w + 2 * margin)

    # placement
    if spec.position == "bottom_left":
        px, py = margin, height - block_h - margin
    elif spec.position == "bottom_right":
        px, py = width - block_w - margin, height - block_h - margin
    elif spec.position == "top_left":
        px, py = margin, margin
    else:
        px, py = width - block_w - margin, margin

    # adaptive colors: sample where the block will sit
    sx = max(0, min(width - 1, px))
    sy = max(0, min(height - 1, py))
    sample_box = (sx, sy,
                  max(sx + 1, min(width, sx + block_w)),
                  max(sy + 1, min(height, sy + block_h)))
    lum = _region_luminance(img, sample_box)
    dark_background = lum > 0.5
    # dark bg -> light panel + white text; light bg -> dark panel
    if dark_background:
        panel_rgb = (20, 20, 20)
        text_rgb = (255, 255, 255)
    else:
        panel_rgb = (245, 245, 245)
        text_rgb = (20, 20, 20)

    # draw panel
    out = img.copy()
    draw = ImageDraw.Draw(out, "RGBA")
    if spec.show_panel:
        panel = Image.new("RGBA", (block_w, block_h),
                          (*panel_rgb, int(spec.panel_opacity * 255)))
        out.paste(panel, (px, py), panel)

    # draw logo + text
    cx = px + margin
    cy = py + margin
    if logo_img is not None:
        out.paste(logo_img, (cx, cy), logo_img)
        cy += logo_h + line_gap
    for text, bold in lines:
        use_font = bold_font if bold else font
        draw.text((cx, cy), text, font=use_font,
                  fill=(*text_rgb, 255))
        cy += font_size + line_gap

    # serialize derivative (JPEG, quality 90, no EXIF copy - the
    # derivative is a NEW artifact; the original keeps its own bytes)
    buf = io.BytesIO()
    out.convert("RGB").save(buf, format="JPEG", quality=90)
    derivative = buf.getvalue()
    d_sha = hashlib.sha256(derivative).hexdigest()
    o_sha = hashlib.sha256(original_bytes).hexdigest()

    geometry = {
        "template": spec.name.value,
        "template_version": spec.version,
        "image_width": width,
        "image_height": height,
        "exif_orientation_applied": exif_orientation,
        "font_size": font_size,
        "font_scale": spec.font_scale,
        "panel": {"x": px, "y": py, "w": block_w, "h": block_h,
                  "opacity": spec.panel_opacity,
                  "color_rgb": list(panel_rgb)},
        "text_color_rgb": list(text_rgb),
        "background_dark": dark_background,
        "region_luminance": round(lum, 4),
        "line_count": len(lines),
        "logo": {"present": logo_img is not None,
                 "height": logo_h} if logo_img is not None else None,
        "position": spec.position,
        "margin": margin,
    }
    return {"image_bytes": derivative,
            "derivative_sha256": d_sha,
            "original_sha256": o_sha,
            "geometry": geometry}
