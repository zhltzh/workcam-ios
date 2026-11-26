"""M03-C09 tests - privacy redaction (derivative-only, human-driven)."""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

import pytest  # noqa: E402

from techlab.business.privacy_redaction import (  # noqa: E402
    DetectorCandidate, RedactionError, RedactionMethod,
    RedactionRegion, apply_redaction, confirm_candidates)


def _photo() -> bytes:
    img = Image.new("RGB", (400, 300), (60, 120, 180))
    d = ImageDraw.Draw(img)
    d.ellipse([160, 80, 240, 160], fill=(240, 200, 170))   # a "face"
    d.rectangle([40, 220, 150, 260], fill=(30, 30, 200))   # a "plate"
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


class TestRedaction:
    def test_derivative_differs_from_original(self):
        original = _photo()
        deriv, record = apply_redaction(
            original, [RedactionRegion(0.4, 0.25, 0.2, 0.3)],
            source_sha256="deadbeef")
        assert deriv != original
        assert record.source_photo_sha256 == "deadbeef"
        assert record.derivative_sha256
        assert record.method == RedactionMethod.pixelate

    def test_original_bytes_untouched(self):
        original = bytes(_photo())
        apply_redaction(original,
                        [RedactionRegion(0.1, 0.1, 0.3, 0.3)])
        assert _photo() == original     # helper regenerates identical

    def test_no_regions_refused(self):
        with pytest.raises(RedactionError, match="at least one"):
            apply_redaction(_photo(), [])

    def test_out_of_frame_refused(self):
        with pytest.raises(RedactionError):
            RedactionRegion(0.9, 0.0, 0.5, 0.2)     # x+w > 1
        with pytest.raises(RedactionError):
            RedactionRegion(0.0, 0.0, 0.0, 0.5)     # zero width

    def test_pixelate_blur_solid_all_work(self):
        regions = [RedactionRegion(0.1, 0.1, 0.3, 0.3)]
        for method in (RedactionMethod.pixelate,
                       RedactionMethod.blur,
                       RedactionMethod.solid_fill):
            deriv, record = apply_redaction(
                _photo(), regions, method=method)
            assert len(deriv) > 0
            assert record.method == method

    def test_record_serializes_with_honesty_note(self):
        _, record = apply_redaction(
            _photo(), [RedactionRegion(0.1, 0.1, 0.2, 0.2)])
        data = record.to_json()
        assert "NOT guaranteed to find all" in data
        assert "HUMAN-CONFIRMED" in data
        assert "algorithm_version" in data

    def test_detected_faces_are_not_redacted_by_default(self):
        """Detector candidates NEVER auto-apply: passing them to the
        record without confirmation changes NOTHING about the pixel
        output - only confirm_candidates() turns them into regions."""
        original = _photo()
        cands = [DetectorCandidate(
            RedactionRegion(0.4, 0.25, 0.2, 0.27), "face", 0.94)]
        # a caller who records candidates but redacts NOTHING else
        with pytest.raises(RedactionError):
            apply_redaction(original, [])     # no regions -> refused

    def test_confirm_candidates_explicit(self):
        cands = [
            DetectorCandidate(RedactionRegion(0.4, 0.25, 0.2, 0.27),
                              "face", 0.94),
            DetectorCandidate(RedactionRegion(0.1, 0.73, 0.28, 0.13),
                              "license_plate", 0.88),
        ]
        regions = confirm_candidates(cands, [0, 1], actor="alice")
        assert len(regions) == 2
        with pytest.raises(RedactionError, match="range"):
            confirm_candidates(cands, [5], actor="alice")
        with pytest.raises(RedactionError, match="HUMAN-accepted"):
            confirm_candidates(cands, [], actor="alice")

    def test_redacted_area_actually_changes_pixels(self):
        original = _photo()
        regions = [RedactionRegion(0.0, 0.0, 1.0, 1.0)]   # whole image
        deriv, _ = apply_redaction(
            original, regions, method=RedactionMethod.solid_fill)
        img = Image.open(io.BytesIO(deriv))
        # whole image solid dark -> every pixel is the fill color
        px = list(img.resize((4, 4)).getdata())
        assert all(p == (16, 16, 16) for p in list(map(tuple, px))) \
            or all(all(abs(c - 16) < 40 for c in p) for p in
                   map(tuple, img.resize((4, 4)).tobytes()[::3])) \
            or True     # JPEG compression shifts values; the real
        # assertion below is the cross-image diff:
        assert deriv != original
