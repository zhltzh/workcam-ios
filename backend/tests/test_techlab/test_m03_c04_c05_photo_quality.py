"""M03-C04/C05 tests - quality advice + duplicate candidates."""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

import pytest  # noqa: E402

from techlab.business.photo_quality import (  # noqa: E402
    DuplicateCandidate, QualityFlag, assess_quality, exact_duplicate_key,
    find_duplicates, hamming_distance, perceptual_hashes)


def _sharp_photo(size=(1280, 720)) -> bytes:
    img = Image.new("RGB", size, (110, 130, 150))
    d = ImageDraw.Draw(img)
    for i in range(0, size[0], 24):          # high-contrast edges
        d.line([(i, 0), (i + 12, size[1])], fill=(20, 20, 40), width=3)
        d.line([(i + 6, 0), (i + 18, size[1])], fill=(240, 240, 250),
               width=2)
    d.rectangle([size[0] // 3, size[1] // 3, 2 * size[0] // 3,
                 2 * size[1] // 3], fill=(250, 200, 60))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _blurry_photo() -> bytes:
    img = Image.new("RGB", (1280, 720), (110, 120, 130))
    img = img.filter(__import__("PIL.ImageFilter",
                                fromlist=["GaussianBlur"])
                     .GaussianBlur(18))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _solid(color: tuple) -> bytes:
    img = Image.new("RGB", (800, 600), color)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


class TestC04Quality:
    def test_sharp_photo_is_ok(self):
        rep = assess_quality(_sharp_photo())
        assert rep.ok, (rep.flags, rep.metrics)
        assert "evidentiary value" in rep.advice[-1]

    def test_blur_detected(self):
        rep = assess_quality(_blurry_photo())
        # Gaussian 18 on flat color -> very low edge energy
        # (it may also flag low contrast first - both are honest)
        assert QualityFlag.blurry in rep.flags or \
            QualityFlag.low_contrast in rep.flags

    def test_overexposed_detected(self):
        rep = assess_quality(_solid((252, 252, 252)))
        assert QualityFlag.overexposed in rep.flags

    def test_underexposed_detected(self):
        rep = assess_quality(_solid((3, 3, 3)))
        assert QualityFlag.underexposed in rep.flags

    def test_low_resolution_detected(self):
        img = Image.new("RGB", (320, 240), (120, 120, 120))
        d = ImageDraw.Draw(img)
        d.rectangle([40, 40, 280, 200], outline=(0, 0, 0), width=4)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        rep = assess_quality(buf.getvalue())
        assert QualityFlag.low_resolution in rep.flags

    def test_truncated_image_is_decode_warning(self):
        data = _sharp_photo()
        rep = assess_quality(data[:len(data) // 2])   # cut in half
        assert QualityFlag.decode_warning in rep.flags

    def test_advice_never_judges_evidence(self):
        for photo in (_sharp_photo(), _blurry_photo(),
                      _solid((3, 3, 3))):
            rep = assess_quality(photo)
            joined = " ".join(rep.advice).lower()
            assert "authentic" not in joined
            # the disclaimer is ALWAYS appended
            assert "evidentiary value" in joined


class TestC05Duplicates:
    def test_exact_duplicates_found(self):
        d1 = _sharp_photo()
        d2 = bytes(d1)                       # identical bytes
        out = find_duplicates([("a", d1), ("b", d2)])
        assert any(c.is_exact and {c.photo_a, c.photo_b} == {"a", "b"}
                   for c in out)

    def test_different_photos_not_near(self):
        d1 = _sharp_photo()
        img2 = Image.new("RGB", (1280, 720), (10, 200, 60))
        d = ImageDraw.Draw(img2)
        d.ellipse([100, 100, 1100, 620], fill=(240, 30, 30))
        buf = io.BytesIO()
        img2.save(buf, "JPEG", quality=90)
        out = find_duplicates([("a", d1), ("b", buf.getvalue())])
        assert not any(c.kind == "near" for c in out)

    def test_brightness_variant_is_near_candidate(self):
        base = _sharp_photo()
        img = Image.open(io.BytesIO(base)).convert("RGB")
        bright = Image.eval(img, lambda x: min(255, int(x * 1.25)))
        buf = io.BytesIO()
        bright.save(buf, "JPEG", quality=85)
        out = find_duplicates([("base", base), ("bright",
                                                buf.getvalue())])
        near = [c for c in out if c.kind == "near"]
        assert near, "brightness variant should be a review candidate"
        assert near[0].distance <= 10

    def test_near_is_candidate_not_same_evidence(self):
        """The API makes the distinction structural: kind='near'
        never equals kind='exact'; the caller reads the kind."""
        c = DuplicateCandidate("x", "y", "near", 4, "ahash")
        assert not c.is_exact

    def test_recompression_is_near(self):
        base = _sharp_photo()
        img = Image.open(io.BytesIO(base))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=35)      # heavy recompress
        out = find_duplicates([("q90", base), ("q35",
                                               buf.getvalue())])
        assert any(c.kind == "near" for c in out)

    def test_hamming_basic(self):
        assert hamming_distance(0b1010, 0b1010) == 0
        assert hamming_distance(0b1010, 0b0101) == 4
        assert hamming_distance(0, 0xFFFFFFFFFFFFFFFF) == 64

    def test_hashes_stable_for_identical_bytes(self):
        d = _sharp_photo()
        h1 = perceptual_hashes(d)
        h2 = perceptual_hashes(bytes(d))
        assert h1["ahash"] == h2["ahash"]
        assert h1["dhash"] == h2["dhash"]

    def test_exact_key_is_sha256(self):
        d = _sharp_photo()
        import hashlib
        assert exact_duplicate_key(d) == \
            hashlib.sha256(d).hexdigest()
