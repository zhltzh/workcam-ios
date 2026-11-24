"""Generate REAL annotation sample images (6 types + combination + before/
after comparison), committed for reviewer inspection.

Outputs (all SYNTHETIC inputs):
  docs/mvp/w013-samples/annotation_arrow.jpg
  docs/mvp/w013-samples/annotation_rectangle.jpg
  docs/mvp/w013-samples/annotation_circle.jpg
  docs/mvp/w013-samples/annotation_freehand.jpg
  docs/mvp/w013-samples/annotation_text.jpg
  docs/mvp/w013-samples/annotation_number_marker.jpg
  docs/mvp/w013-samples/annotation_watermark_combo.jpg
  docs/mvp/w013-samples/comparison_before_after.jpg
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

import io
from PIL import Image, ImageDraw

from app.services.annotation_render import render_annotated_derivative
from app.services.watermark_render import (
    GpsSource, TimeTrust, WatermarkFields, render_watermark)

SAMPLES = REPO / "docs" / "mvp" / "w013-samples"


def _photo(w=1600, h=1200, bright=True) -> bytes:
    img = Image.new("RGB", (w, h), (210 if bright else 40,
                                    200 if bright else 45,
                                    190 if bright else 50))
    d = ImageDraw.Draw(img)
    d.rectangle([100, 100, 500, 300], fill=(60, 90, 160))
    d.rectangle([700, 500, 1200, 800], fill=(120, 130, 140))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


ANN = {
    "arrow": [{"annotation_type": "arrow", "norm_x1": 0.2, "norm_y1": 0.3,
                "norm_x2": 0.55, "norm_y2": 0.25,
                "color_hex": "#FF3B30", "stroke_width": 6,
                "z_order": 0}],
    "rectangle": [{"annotation_type": "rectangle", "norm_x1": 0.15,
                    "norm_y1": 0.4, "norm_x2": 0.5, "norm_y2": 0.7,
                    "color_hex": "#FF9500", "stroke_width": 5,
                    "z_order": 0}],
    "circle": [{"annotation_type": "circle", "norm_x1": 0.65, "norm_y1": 0.5,
                 "norm_x2": 0.85, "norm_y2": 0.65,
                 "color_hex": "#007AFF", "stroke_width": 5,
                 "z_order": 0}],
    "freehand": [{"annotation_type": "freehand", "norm_x1": 0.1,
                   "norm_y1": 0.8, "norm_x2": 0.4, "norm_y2": 0.85,
                   "color_hex": "#34C759", "stroke_width": 4,
                   "z_order": 0}],
    "text": [{"annotation_type": "text", "norm_x1": 0.45, "norm_y1": 0.15,
               "text_content": "Hairline crack near pier 2 west face",
               "color_hex": "#FF3B30", "stroke_width": 4, "z_order": 0}],
    "number_marker": [{"annotation_type": "number_marker",
                         "norm_x1": 0.7, "norm_y1": 0.7,
                         "marker_number": 3, "color_hex": "#FF2D55",
                         "stroke_width": 5, "z_order": 0}],
}


def main() -> int:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    photo = _photo()

    for ann_type, anns in ANN.items():
        result = render_annotated_derivative(photo, anns)
        out = SAMPLES / f"annotation_{ann_type}.jpg"
        out.write_bytes(result["image_bytes"])
        print(f"annotation_{ann_type}.jpg: {len(result['image_bytes'])}B")

    # combined: all 6 on one image
    combo = []
    for key, anns in ANN.items():
        a = dict(anns[0])
        a["z_order"] = list(ANN).index(key)
        combo.append(a)
    result = render_annotated_derivative(photo, combo)
    (SAMPLES / "annotation_watermark_combo.jpg").write_bytes(
        result["image_bytes"])
    print(f"annotation_watermark_combo.jpg: "
          f"{len(result['image_bytes'])}B")

    # comparison
    from PIL import Image as PI
    b = _photo(1200, 900, bright=True)
    a = _photo(1200, 900, bright=False)
    r = _render_cmp(b, a, "IMG_before", "IMG_after")
    (SAMPLES / "comparison_before_after.jpg").write_bytes(r)
    print(f"comparison_before_after.jpg: {len(r)}B")
    return 0


def _render_cmp(before, after, bid, aid) -> bytes:
    from PIL import Image, ImageDraw
    b_img = Image.open(io.BytesIO(before)).convert("RGB")
    a_img = Image.open(io.BytesIO(after)).convert("RGB")
    half_w = 780
    def fit(img):
        r = half_w / img.width
        return img.resize((half_w, int(img.height * r)), Image.LANCZOS)
    b_img = fit(b_img); a_img = fit(a_img)
    half_h = max(b_img.height, a_img.height)
    canvas = Image.new("RGB", (half_w * 2 + 40, half_h + 60), (245, 245, 245))
    canvas.paste(b_img, (20, 50)); canvas.paste(a_img, (half_w + 20, 50))
    d = ImageDraw.Draw(canvas)
    d.text((20, 15), f"BEFORE - {bid}", fill=(20, 20, 20))
    d.text((half_w + 20, 15), f"AFTER - {aid}", fill=(20, 20, 20))
    d.text((20, half_h + 40), "Manual comparison", fill=(120, 120, 120))
    buf = io.BytesIO(); canvas.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


if __name__ == "__main__":
    sys.exit(main())
