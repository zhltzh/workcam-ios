#!/usr/bin/env python3
"""Generate REAL rendered watermark sample images for all five templates
in landscape and portrait, plus the cross-platform geometry vectors.

Outputs:
  docs/mvp/w011-samples/<template>_<orientation>.jpg   (10 sample images)
  test-vectors/tech-lab/w011-geometry-vectors.json     (iOS parity data)

All inputs are SYNTHETIC test images - no real photography. The samples
are committed so the reviewer SEES the actual rendering.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

import io

from PIL import Image, ImageDraw

from app.services.watermark_render import (
    GpsSource, TimeTrust, WatermarkFields, TemplateName, get_template,
    render_watermark)

SAMPLES = REPO / "docs" / "mvp" / "w011-samples"
VECTORS = REPO / "test-vectors" / "tech-lab"

# tiny built-in logo (SYNTHETIC): a simple badge, so Corporate/Evidence
# samples show the logo block for real
def _make_logo() -> bytes:
    logo = Image.new("RGB", (120, 60), (200, 30, 30))
    d = ImageDraw.Draw(logo)
    d.rectangle([6, 6, 113, 53], outline=(255, 255, 255), width=4)
    d.text((30, 18), "LOGO", fill=(255, 255, 255))
    buf = io.BytesIO()
    logo.save(buf, format="PNG")
    return buf.getvalue()


def _make_photo(width: int, height: int, bright: bool) -> bytes:
    """Synthetic site-ish photo: gradient + blocks - varied luminance so
    the adaptive-color path is visible across samples."""
    img = Image.new("RGB", (width, height))
    d = ImageDraw.Draw(img)
    base = 210 if bright else 40
    other = 40 if bright else 200
    for y in range(height):
        shade = int(base * (0.7 + 0.3 * y / height))
        d.line([(0, y), (width, y)], fill=(shade, shade, min(255, shade + 8)))
    for i, (bx, by) in enumerate(((60, 60), (width - 160, height // 3),
                                  (width // 3, height - 200))):
        d.rectangle([bx, by, bx + 90, by + 60],
                    fill=(other, other // 2, 60 if bright else other),
                    outline=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def fields_for(template: str) -> WatermarkFields:
    common = dict(
        project_name="Highway N-12 Bridge Widening",
        site_address="Km 34+200, Jiangsu",
        photo_id="IMG_20261124_0123",
        operator="J. Wang",
        company_name="CMSS Engineering",
        notes="Hairline crack at pier 2 west face; monitor per spec.",
        captured_time="2026-11-24 10:00",
        gps_latitude=32.0603, gps_longitude=118.7969,
        gps_source=GpsSource.GPS, gps_accuracy_m=4.0,
    )
    if template == "minimal":
        f = WatermarkFields(**common,
                           show_photo_id=True, show_captured_time=True,
                           captured_time_trust=TimeTrust.SERVER_ANCHORED,
                           show_evidence_status=True,
                           evidence_status="cryptographically_valid")
    elif template == "construction":
        f = WatermarkFields(**common,
                           show_project_name=True, show_site_address=True,
                           show_captured_time=True,
                           captured_time_trust=TimeTrust.DEVICE,
                           show_gps=True, show_gps_accuracy=True,
                           show_photo_id=True, show_operator=True)
    elif template == "inspection":
        f = WatermarkFields(**common,
                           show_project_name=True, show_photo_id=True,
                           show_captured_time=True,
                           captured_time_trust=TimeTrust.DEVICE,
                           show_gps=True, show_operator=True,
                           show_notes=True)
    elif template == "corporate":
        f = WatermarkFields(**common,
                           show_company_name=True, show_project_name=True,
                           show_site_address=True, show_captured_time=True,
                           captured_time_trust=TimeTrust.SERVER_ANCHORED,
                           show_photo_id=True,
                           show_company_logo=True)
    else:   # evidence
        f = WatermarkFields(**common,
                           show_company_name=True, show_project_name=True,
                           show_site_address=True, show_captured_time=True,
                           captured_time_trust=TimeTrust.SERVER_ANCHORED,
                           show_gps=True, show_gps_accuracy=True,
                           show_photo_id=True, show_operator=True,
                           show_notes=True, show_evidence_status=True,
                           evidence_status="cryptographically_valid",
                           show_company_logo=True)
    return f


def main() -> int:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    VECTORS.mkdir(parents=True, exist_ok=True)

    logo = _make_logo()
    vector_cases = []
    written: list[Path] = []

    for template in [t.value for t in TemplateName]:
        for orientation, (w, h) in (("landscape", (1600, 1200)),
                                    ("portrait", (1200, 1600))):
            bright = (template in ("construction", "corporate"))
            photo = _make_photo(w, h, bright=bright)
            f = fields_for(template)
            logo_b64_or_none = None
            if f.show_company_logo:
                f.company_logo = logo
            result = render_watermark(photo, f, template)
            out = SAMPLES / f"{template}_{orientation}.jpg"
            out.write_bytes(result["image_bytes"])
            written.append(out)
            print(f"written: {out.name} "
                  f"({len(result['image_bytes'])} bytes)")
            vector_cases.append({
                "case": f"{template}_{orientation}",
                "template": template,
                "image_width": w, "image_height": h,
                "original_sha256": result["original_sha256"],
                "derivative_sha256": result["derivative_sha256"],
                "expected_geometry": result["geometry"],
            })

    doc = {
        "_description": "WC-W011 cross-platform watermark geometry "
                        "vectors: identical inputs must yield identical "
                        "geometry on iOS (font metrics aside, the panel "
                        "box/colors/scale are the parity contract). The "
                        "committed sample images let a reviewer SEE the "
                        "rendering.",
        "_usage": "iOS: reproduce the panel box, colors, font_scale, "
                  "line order from expected_geometry for the same input "
                  "size/template; pixel identity is NOT required (fonts "
                  "differ) but geometry must match to 1px.",
        "cases": vector_cases,
    }
    vec_path = VECTORS / "w011-geometry-vectors.json"
    vec_path.write_text(json.dumps(doc, indent=2, allow_nan=False),
                        encoding="utf-8")
    print(f"written: {vec_path.relative_to(REPO)} ({len(vector_cases)} cases)")
    total = sum(p.stat().st_size for p in written)
    print(f"total sample bytes: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
