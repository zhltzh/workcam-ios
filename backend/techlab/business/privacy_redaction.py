"""M03-C09: privacy redaction (derivative-only, human-driven).

What this is:
  - MANUAL region redaction (rectangles the human picks): pixelate,
    blur, solid-fill. Output is a NEW derivative image.
  - The redaction RECORD (regions + algorithm + version) travels with
    the derivative, so a viewer can see WHAT was hidden and HOW -
    hiding is never silent.
  - Face / license-plate DETECTION is a candidate-proposal helper
    only: detectors suggest rectangles, a human confirms, then the
    confirmed set redacts. No detector output is auto-applied.

What this is NOT (honesty rules):
  - the ORIGINAL is never modified (immutable by the storage layer;
    this module only ever reads it)
  - no detector claims to find ALL sensitive content (missed faces
    and plates remain possible - the caller states this in the UI)
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from enum import Enum

from PIL import Image, ImageFilter

REDACTION_ALGORITHM_VERSION = "privacy-redaction/1.0"


class RedactionMethod(str, Enum):
    pixelate = "pixelate"
    blur = "blur"
    solid_fill = "solid_fill"


class RedactionError(ValueError):
    pass


@dataclass(frozen=True)
class RedactionRegion:
    """Normalized rect (0..1, top-left origin, upright image)."""
    x: float
    y: float
    w: float
    h: float

    def __post_init__(self):
        _EPS = 1e-9
        for v in (self.x, self.y, self.w, self.h):
            if not 0.0 <= v <= 1.0:
                raise RedactionError(
                    f"redaction rect out of the normalized frame: "
                    f"({self.x}, {self.y}, {self.w}, {self.h})")
        if self.w <= 0 or self.h <= 0:
            raise RedactionError("redaction rect needs positive size")
        if self.x + self.w > 1.0 + _EPS \
                or self.y + self.h > 1.0 + _EPS:
            raise RedactionError(
                f"redaction rect exceeds the frame: x+w="
                f"{self.x + self.w:.3f}, y+h={self.y + self.h:.3f}")

    def to_pixels(self, width: int, height: int
                  ) -> tuple[int, int, int, int]:
        return (int(self.x * width), int(self.y * height),
                max(1, int(self.w * width)),
                max(1, int(self.h * height)))


@dataclass(frozen=True)
class DetectorCandidate:
    """A detector's SUGGESTION - never auto-applied."""
    region: RedactionRegion
    kind: str            # 'face' | 'license_plate' | 'other'
    confidence: float


@dataclass
class RedactionRecord:
    """The audit trail shipped beside the derivative."""
    source_photo_sha256: str
    derivative_sha256: str = ""
    method: RedactionMethod = RedactionMethod.pixelate
    regions: list[RedactionRegion] = field(default_factory=list)
    confirmed_by: str = ""           # the human who confirmed
    detector_candidates_used: list[DetectorCandidate] = \
        field(default_factory=list)
    algorithm_version: str = REDACTION_ALGORITHM_VERSION

    def to_json(self) -> str:
        return json.dumps({
            "source_photo_sha256": self.source_photo_sha256,
            "derivative_sha256": self.derivative_sha256,
            "method": self.method.value,
            "regions": [[r.x, r.y, r.w, r.h] for r in self.regions],
            "confirmed_by": self.confirmed_by,
            "detector_candidates_used": [
                {"region": [c.region.x, c.region.y, c.region.w,
                            c.region.h], "kind": c.kind,
                 "confidence": c.confidence}
                for c in self.detector_candidates_used],
            "algorithm_version": self.algorithm_version,
            "honesty": "regions are HUMAN-CONFIRMED; detection is "
                       "assist-only and NOT guaranteed to find all "
                       "sensitive content",
        }, indent=1, ensure_ascii=False)


def apply_redaction(original_bytes: bytes,
                    regions: list[RedactionRegion],
                    method: RedactionMethod =
                        RedactionMethod.pixelate,
                    pixelate_block: int = 16,
                    blur_radius: int = 12,
                    solid_color: tuple = (16, 16, 16),
                    source_sha256: str = "") -> tuple[bytes,
                                                      RedactionRecord]:
    """Render a redacted DERIVATIVE. The original bytes are only
    READ; the output is new bytes (JPEG). Returns (jpeg, record)."""
    if not regions:
        raise RedactionError(
            "redaction needs at least one region - a privacy "
            "derivative with nothing hidden is a claim we refuse "
            "to make implicitly")
    try:
        img = Image.open(io.BytesIO(original_bytes))
        img.load()
    except Exception as e:
        raise RedactionError(f"cannot decode original: {e}") from e
    if img.mode != "RGB":
        img = img.convert("RGB")
    out = img.copy()
    w, h = out.size
    for region in regions:
        px, py, pw, ph = region.to_pixels(w, h)
        box = (min(px, w - 1), min(py, h - 1),
               min(px + pw, w), min(py + ph, h))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        crop = out.crop(box)
        if method == RedactionMethod.pixelate:
            small = crop.resize(
                (max(1, crop.width // pixelate_block),
                 max(1, crop.height // pixelate_block)),
                Image.Resampling.LANCZOS)
            crop = small.resize((crop.width, crop.height),
                                Image.Resampling.NEAREST)
        elif method == RedactionMethod.blur:
            crop = crop.filter(ImageFilter.GaussianBlur(blur_radius))
        else:
            solid = Image.new("RGB", crop.size, solid_color)
            crop = solid
        out.paste(crop, (box[0], box[1]))

    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=90)
    derivative = buf.getvalue()
    record = RedactionRecord(
        source_photo_sha256=source_sha256 or hashlib.sha256(
            original_bytes).hexdigest(),
        derivative_sha256=hashlib.sha256(derivative).hexdigest(),
        method=method, regions=regions)
    return derivative, record


def confirm_candidates(candidates: list[DetectorCandidate],
                       accepted: list[int], actor: str
                       ) -> list[RedactionRegion]:
    """Detector output becomes redaction regions ONLY through this
    explicit human confirmation step (indices into candidates)."""
    out: list[RedactionRegion] = []
    for i in accepted:
        if not 0 <= i < len(candidates):
            raise RedactionError(
                f"candidate index {i} out of range - confirmation "
                f"must reference real suggestions")
        out.append(candidates[i].region)
    if not out:
        raise RedactionError(
            "no candidates confirmed - redaction requires at least "
            "one HUMAN-accepted region")
    _ = actor          # recorded by the caller in the record
    return out
