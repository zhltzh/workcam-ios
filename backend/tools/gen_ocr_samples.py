#!/usr/bin/env python
"""TM-04 E09: generate the LEGITIMATE OCR sample set.

Every image is rendered from synthetic programmatic text with
Pillow - no scraped photos, no third-party content, no personal
data (the "legal sample set" requirement). The ground-truth
manifest maps every sample to its type/text/transform so:

  - the Mac/Vision device pass measures REAL recognition accuracy
    per taxonomy field (serial/plate/gauge/location/date/barcode),
  - the fixture doubles as OCRFieldCandidate schema demo data,
  - regen is deterministic (fixed seeds, byte-stable output).

Usage (from backend/):
    python tools/gen_ocr_samples.py [--out ../test-vectors/ocr]
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "test-vectors" / "ocr"

W, H = 480, 240

# synthetic vocabularies - deliberately non-real serials
SERIALS = [
    "SN-4021-A", "SN-7734-C", "SN-2026-118", "SN-9001-Z",
    "WC-TM04-77",
]
PLATES = [
    ["MODEL CT-500", "INPUT 230V", "SER. SN-4021-A"],
    ["PUMP UNIT P2", "MAX 6.5 BAR", "MADE 2026"],
    ["PANEL B-12", "RATED 32A", "IEC CLASS I"],
]
GAUGES = ["12.5 bar", "48.2 psi", "23.1 kPa", "96.4 dB", "0.75 kW"]
LOCATIONS = ["LEVEL 3 - ROOM 12", "ZONE B / PANEL 4",
             "ROOF - NORTH SIDE", "BASEMENT P2", "STAIR 5"]
DATES = ["2026-03", "2026-11-28", "WK 48 / 2026", "03-2026"]
BARCODES = ["4021044", "7734012", "2026118", "9001001", "1204800"]

FONT_SIZES = {"title": 34, "body": 26, "small": 22}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("consola.ttf", "arial.ttf", "DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _base(color_bg=(244, 244, 240), color_fg=(16, 16, 16)):
    img = Image.new("RGB", (W, H), color_bg)
    return img, ImageDraw.Draw(img)


def _apply_transform(img, angle_deg: float, brightness: float,
                     noise_seed: int):
    if angle_deg:
        img = img.rotate(angle_deg, expand=False,
                         fillcolor=(244, 244, 240))
    if brightness != 1.0:
        from PIL import ImageEnhance
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if noise_seed:
        # deterministic speckle
        import random
        px = img.load()
        rng = random.Random(noise_seed)
        for _ in range(400):
            x, y = rng.randrange(img.width), rng.randrange(img.height)
            r, g, b = px[x, y]
            d = rng.randint(-18, 18)
            px[x, y] = (max(0, min(255, r + d)),
                        max(0, min(255, g + d)),
                        max(0, min(255, b + d)))
    return img


def _render_serial(text: str) -> Image.Image:
    img, d = _base()
    f = _font(FONT_SIZES["title"])
    d.rectangle([8, 8, W - 8, H - 8], outline=(70, 70, 70), width=2)
    d.text((24, H // 2 - 22), text, fill=(16, 16, 16), font=f)
    d.line([24, H // 2 + 26, W - 24, H // 2 + 26],
           fill=(70, 70, 70), width=2)
    return img


def _render_plate(lines: list[str]) -> Image.Image:
    img, d = _base((210, 214, 220))
    d.rectangle([6, 6, W - 6, H - 6], outline=(40, 40, 40), width=3)
    y = 26
    for i, ln in enumerate(lines):
        f = _font(FONT_SIZES["title"] if i == 0 else FONT_SIZES["body"])
        d.text((20, y), ln, fill=(20, 20, 20), font=f)
        y += 56
    return img


def _render_gauge(text: str) -> Image.Image:
    img, d = _base((250, 250, 250), (30, 30, 30))
    d.ellipse([24, 24, 176, 176], outline=(30, 30, 30), width=4)
    # needle at a plausible angle for the reading
    import random
    rng = random.Random(text)
    ang = rng.uniform(0.35, 0.85) * 2 * math.pi
    cx, cy = 100, 100
    d.line([cx, cy, cx + 62 * math.cos(ang), cy + 62 * math.sin(ang)],
           fill=(180, 30, 30), width=5)
    f = _font(FONT_SIZES["title"])
    d.text((196, 86), text, fill=(16, 16, 16), font=f)
    return img


def _render_location(text: str) -> Image.Image:
    img, d = _base((250, 210, 40))
    d.rectangle([4, 4, W - 4, H - 4], outline=(20, 20, 20), width=4)
    d.polygon([(28, 58), (60, 22), (60, 58)], fill=(20, 20, 20))
    f = _font(FONT_SIZES["body"])
    d.text((76, 28), text, fill=(20, 20, 20), font=f)
    return img


def _render_date(text: str) -> Image.Image:
    img, d = _base((255, 255, 255), (10, 10, 10))
    f = _font(FONT_SIZES["title"])
    d.ellipse([W // 2 - 70, 34, W // 2 + 70, 174],
              outline=(60, 60, 60), width=3)
    d.text((W // 2 - 46, 90), text, fill=(10, 10, 10), font=f)
    return img


def _render_barcode(text: str) -> Image.Image:
    img, d = _base()
    import random
    rng = random.Random(text)
    x = 36
    while x < W - 60:
        w = rng.choice((2, 4, 6))
        if rng.random() < 0.6:
            d.rectangle([x, 36, x + w, 150], fill=(10, 10, 10))
        x += w + rng.choice((2, 3))
    f = _font(FONT_SIZES["body"])
    d.text((36, 158), text, fill=(10, 10, 10), font=f)
    return img


RENDERERS = {
    "serial_number": (SERIALS, _render_serial),
    "model_plate": (PLATES, _render_plate),
    "measurement_reading": (GAUGES, _render_gauge),
    "location_label": (LOCATIONS, _render_location),
    "date_code": (DATES, _render_date),
    "barcode_text": (BARCODES, _render_barcode),
}

TRANSFORMS = [
    {"angle": 0.0, "brightness": 1.0, "noise_seed": 0},
    {"angle": 2.5, "brightness": 1.0, "noise_seed": 11},
    {"angle": -3.0, "brightness": 0.88, "noise_seed": 22},
    {"angle": 4.5, "brightness": 1.12, "noise_seed": 33},
]


def normalize(s: str) -> str:
    return " ".join(s.upper().split())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    out = Path(args.out)
    samples_dir = out / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": "ocr-samples/1.0",
        "license_note": "synthetic programmatic renders; no "
                        "third-party or scraped content",
        "image_size": [W, H],
        "bbox_convention": "u,v,w,h normalized, v from TOP",
        "recognition_languages_expected": ["en-US"],
        "samples": [],
    }

    idx = 0
    for ftype, (texts, render) in RENDERERS.items():
        for text in texts:
            for t in TRANSFORMS:
                img = _apply_transform(render(text), t["angle"],
                                       t["brightness"],
                                       t["noise_seed"])
                name = f"{idx:03d}_{ftype}.png"
                img.save(samples_dir / name, optimize=True)
                manifest["samples"].append({
                    "file": f"samples/{name}",
                    "field_type": ftype,
                    # ground-truth text: for plates the first line is
                    # the searchable key; the full text list follows
                    "text": text if ftype != "model_plate"
                            else " ".join(text),
                    "text_lines": text if isinstance(text, list)
                                  else [text],
                    "transform": t,
                    "expected_normalized":
                        normalize(" ".join(text)
                                  if isinstance(text, list) else text),
                })
                idx += 1

    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"ocr samples -> {out} ({len(manifest['samples'])} samples,"
          f" {len(RENDERERS)} field types)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
