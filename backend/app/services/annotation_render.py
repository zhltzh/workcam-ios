"""WC-W013 P2 - annotation rendering engine.

Renders annotations onto a W011-style DERIVATIVE (originals are never
modified). Reuses the W011 watermark engine for composition:
annotation render can be combined with a watermark in ONE derivative.

Normalized coordinates (0..1) refer to the EXIF-transposed display
frame - the same frame iOS draws on screen.
"""

from __future__ import annotations

import hashlib
import io

from app.services.watermark_render import (
    WatermarkFields, _apply_exif_orientation, _load_font,
    render_watermark, get_template,
)


def _denorm(norm, width, height):
    return norm * width, norm * height


def render_annotated_derivative(
        original_bytes: bytes,
        annotations: list[dict],
        watermark_fields: WatermarkFields | None = None,
        watermark_template: str | None = None) -> dict:
    """Render annotations (and optionally a watermark) onto a derivative.

    annotations: list of dicts matching the API's annotation rows
    (norm_x1/y1/x2/y2, annotation_type, color_hex, stroke_width,
    text_content, marker_number, z_order).

    Returns image_bytes, derivative_sha256, original_sha256, geometry.
    The ORIGINAL BYTES are only read - never written."""
    from PIL import Image, ImageDraw

    # W013 oversize guard (same discipline as the watermark engine)
    from app.services.watermark_render import MAX_RENDER_PIXELS
    probe = Image.open(io.BytesIO(original_bytes))
    if probe.width * probe.height > MAX_RENDER_PIXELS:
        raise ValueError(
            f"image too large to render: {probe.width}x{probe.height} "
            f"({probe.width * probe.height} pixels exceeds the "
            f"{MAX_RENDER_PIXELS}-pixel render limit)")
    del probe

    img = Image.open(io.BytesIO(original_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img, exif_orientation = _apply_exif_orientation(img)
    width, height = img.size

    out = img.copy()
    draw = ImageDraw.Draw(out, "RGBA")

    # scale stroke with image size (longest edge ~ reference 1600)
    scale = max(width, height) / 1600
    base_font = _load_font(max(12, int(16 * scale)))

    for ann in sorted(annotations, key=lambda a: a.get("z_order", 0)):
        t = ann["annotation_type"]
        color = ann.get("color_hex", "#FF3B30")
        sw = max(1, int(ann.get("stroke_width", 4) * scale))
        x1, y1 = _denorm(ann["norm_x1"], width, height)
        x2, y2 = _denorm(ann.get("norm_x2") or ann["norm_x1"],
                          width, height)

        if t == "arrow":
            # draw the shaft
            draw.line([(x1, y1), (x2, y2)], fill=color, width=sw)
            # arrowhead: rotate a triangle to the line direction
            import math
            angle = math.atan2(y2 - y1, x2 - x1)
            head = max(sw * 3, 10)
            for da in (2.6, -2.6):
                hx = x2 + head * math.cos(angle + da)
                hy = y2 + head * math.sin(angle + da)
                draw.line([(x2, y2), (hx, hy)], fill=color, width=sw)
        elif t == "rectangle":
            draw.rectangle([x1, y1, x2, y2], outline=color, width=sw)
        elif t == "circle":
            r = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r],
                         outline=color, width=sw)
        elif t == "freehand":
            # two-point simplification: the API stores the segment end
            draw.line([(x1, y1), (x2, y2)], fill=color, width=sw)
            draw.line([(x1, y1), (x1 + 4, y1 + 4)], fill=color,
                      width=sw)      # "hand-drawn" tail hint
        elif t == "text":
            text = ann.get("text_content") or ""
            # word wrap at 45% width
            words = text.split()
            lines, current = [], ""
            for w in words:
                cand = f"{current} {w}".strip()
                if base_font.getlength(cand) < width * 0.45 or not current:
                    current = cand
                else:
                    lines.append(current)
                    current = w
            if current:
                lines.append(current)
            # text background
            fh = int(16 * scale)
            block_h = len(lines) * (fh + 4) + 8
            draw.rectangle([x1 - 4, y1 - 4,
                           x1 + max(base_font.getlength(l) for l in lines)
                           + 8, y1 + block_h],
                          fill=(255, 255, 255, 200))
            cy = y1
            for l in lines:
                draw.text((x1, cy), l, font=base_font,
                          fill=color)
                cy += fh + 4
        elif t == "number_marker":
            num = ann.get("marker_number", 1)
            r = max(sw * 3, 14 * scale)
            draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r],
                         fill=color)
            label = str(num)
            lw = base_font.getlength(label)
            draw.text((x1 - lw / 2, y1 - 9 * scale), label,
                      font=base_font, fill=(255, 255, 255))

    # optional watermark COMPOSED on the same derivative
    watermark_applied = False
    if watermark_fields is not None and watermark_template:
        from PIL import Image as PILImage
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=90)
        wm = render_watermark(buf.getvalue(), watermark_fields,
                              watermark_template)
        out = PILImage.open(io.BytesIO(wm["image_bytes"]))
        watermark_applied = True

    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=90)
    derivative = buf.getvalue()

    return {
        "image_bytes": derivative,
        "derivative_sha256": hashlib.sha256(derivative).hexdigest(),
        "original_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "geometry": {
            "image_width": width, "image_height": height,
            "exif_orientation_applied": exif_orientation,
            "annotation_count": len(annotations),
            "watermark_applied": watermark_applied,
            "scale_factor": round(scale, 4),
        },
        "note": "derivative image - human-added annotations; NOT the "
                "trusted original evidence",
    }
