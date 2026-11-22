"""T15 - AI-assisted defect detection: algorithm skeleton PoC (Wave 4).

RESEARCH CONCLUSION FIRST (honesty before code):
  Real-world crack/rust/leak detection NEEDS trained models (Core ML /
  OpenCV DNN / published datasets). This environment has NO numpy, NO
  OpenCV, NO pre-trained models and downloading model weights or
  datasets is outside the task boundaries (external resources). What
  CAN be honestly built here:

  1. a SYNTHETIC crack-image generator with pixel-level ground truth
     (the evaluation substrate any future model must beat);
  2. a classical baseline detector (dark-elongated-structure +
     connected components) that runs on pure stdlib;
  3. a pixel-level METRIC suite (precision / recall / false-positive
     rate / miss rate) + object-level detection scoring;
  4. the PROTOCOL the iOS side will implement against real models.

  AI results are ADVISORY ONLY: the protocol forbids any automatic
  'compliant with engineering standard' claim, and detector output is
  always labeled with its provenance (algorithm, version, thresholds).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field, field_validator


# ------------------------------------------------------------ synthetic data


class SyntheticImage:
    """128x128 grayscale image as a flat list of ints (0-255).
    SYNTHETIC ONLY - every generated image carries ground-truth cracks
    so metrics are exact, never eyeballed."""

    SIZE = 128

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.pixels: list[int] = self._background()
        self.crack_mask: list[bool] = [False] * (self.SIZE * self.SIZE)

    def _background(self) -> list[int]:
        # concrete-ish noisy background 140-180
        return [self.rng.randint(140, 180) for _ in range(self.SIZE * self.SIZE)]

    def draw_crack(self, start=(64, 8), direction=(1, 2), length=90,
                   width=2, darkness=60) -> None:
        """Walk the polyline and darken it; record the exact
        ground-truth pixels for metric computation.

        Crack pixels are set to an ABSOLUTE dark value (a crack in
        concrete is a dark feature, not a relative dip) so the
        detector's threshold strategy is meaningful on synthetic data.
        Jitter is a SLOW orthogonal wander, not a per-step increment:
        jittering the increment (the first draft) produced steps of up
        to (2,3) px which exceed 8-connectivity and split the crack
        into sub-min_pixels fragments the detector correctly ignored.
        A slow wander keeps consecutive centers within 8-connectivity
        - like a real continuous crack."""
        x, y = start
        dx, dy = direction
        wander = 0.0
        for _ in range(length):
            wander += self.rng.uniform(-0.4, 0.4)
            wander = max(-3.0, min(3.0, wander))
            cy = y + int(round(wander))
            for ox in range(width):
                for oy in range(width):
                    px, py = x + ox, cy + oy
                    if 0 <= px < self.SIZE and 0 <= py < self.SIZE:
                        idx = py * self.SIZE + px
                        self.pixels[idx] = min(self.pixels[idx],
                                               35 + self.rng.randint(0, 25))
                        self.crack_mask[idx] = True
            x += dx
            y += dy

    def add_noise_blobs(self, count=8, size=5, darkness=50) -> None:
        """Dark speckle noise (stains, shadows) - NOT cracks. A detector
        that fires on these produces false positives; ground truth
        stays False, keeping the metric honest."""
        for _ in range(count):
            x = self.rng.randrange(self.SIZE)
            y = self.rng.randrange(self.SIZE)
            for ox in range(size):
                for oy in range(size):
                    px, py = x + ox, y + oy
                    if 0 <= px < self.SIZE and 0 <= py < self.SIZE:
                        idx = py * self.SIZE + px
                        self.pixels[idx] = max(0, self.pixels[idx] - darkness)


# ------------------------------------------------------------- baseline det.


@dataclass
class Detection:
    box: tuple[int, int, int, int]      # x0, y0, x1, y1
    pixel_count: int
    elongation: float                    # thinness = bbox_diag^2 / pixels
    pixel_indices: set = field(default_factory=set)   # actual component px


class ClassicalCrackDetector:
    """Baseline: threshold dark pixels, keep THIN connected components.
    The first draft used bbox-side elongation (long/short side), which
    the synthetic test immediately broke: a DIAGONAL crack has a nearly
    square bbox and was rejected. The thinness metric
    (bbox_diagonal^2 / pixel_count) is orientation-proof: a diagonal
    229-px crack scores ~98 while a 36-px round stain scores ~2. This is
    exactly the kind of finding synthetic ground truth exists to catch.
    Pure stdlib BFS labeling. NOT an AI model - it is the reference
    baseline the future Core ML model must beat."""

    def __init__(self, dark_threshold: int = 100, min_thinness: float = 8.0,
                 min_pixels: int = 25):
        self.dark_threshold = dark_threshold
        self.min_thinness = min_thinness
        self.min_pixels = min_pixels

    def detect(self, image: SyntheticImage) -> list[Detection]:
        size = image.SIZE
        dark = [p < self.dark_threshold for p in image.pixels]
        visited = [False] * (size * size)
        detections: list[Detection] = []
        for idx in range(size * size):
            if not dark[idx] or visited[idx]:
                continue
            # BFS the component
            stack = [idx]
            visited[idx] = True
            xs, ys, count = [], [], 0
            while stack:
                cur = stack.pop()
                cx, cy = cur % size, cur // size
                xs.append(cx); ys.append(cy); count += 1
                for nx in (cx - 1, cx, cx + 1):
                    for ny in (cy - 1, cy, cy + 1):
                        if 0 <= nx < size and 0 <= ny < size:
                            nidx = ny * size + nx
                            if dark[nidx] and not visited[nidx]:
                                visited[nidx] = True
                                stack.append(nidx)
            w = max(xs) - min(xs) + 1
            h = max(ys) - min(ys) + 1
            diag_sq = w * w + h * h
            thinness = diag_sq / max(1, count)
            if count >= self.min_pixels and thinness >= self.min_thinness:
                detections.append(Detection(
                    box=(min(xs), min(ys), max(xs), max(ys)),
                    pixel_count=count, elongation=thinness,
                    pixel_indices={ys[i] * size + xs[i] for i in range(count)}))
        return detections


# ---------------------------------------------------------------- metrics


class PixelMetrics(BaseModel):
    """Pixel-level + object-level scoring. Ground truth comes from the
    generator's exact mask - never from a human eyeball."""

    tp_pixels: int
    fp_pixels: int
    fn_pixels: int
    precision: float | None = None      # None when no predictions (honest)
    recall: float | None = None         # None when no cracks exist
    fp_rate_per_megapixel: float = 0.0
    objects_detected: int = 0
    objects_total: int = 0

    @field_validator("precision", "recall")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("metrics must be within [0, 1]")
        return v


def evaluate(detector: ClassicalCrackDetector, image: SyntheticImage,
             object_hit_ratio: float = 0.2) -> PixelMetrics:
    """Score a detection run against the synthetic ground truth.

    Pixel-level predictions are the detection COMPONENT pixels (not the
    bounding box - a diagonal crack's bbox is mostly empty space, which
    the first draft counted as false positives and crushed precision)."""
    size = image.SIZE
    detections = detector.detect(image)

    predicted = [False] * (size * size)
    for d in detections:
        for idx in d.pixel_indices:
            predicted[idx] = True

    tp = fp = fn = 0
    for idx in range(size * size):
        if predicted[idx] and image.crack_mask[idx]:
            tp += 1
        elif predicted[idx] and not image.crack_mask[idx]:
            fp += 1
        elif not predicted[idx] and image.crack_mask[idx]:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    objects_total = 1 if any(image.crack_mask) else 0
    objects_detected = 0
    if objects_total:
        gt_pixels = sum(image.crack_mask)
        for d in detections:
            overlap = sum(1 for idx in d.pixel_indices
                          if image.crack_mask[idx])
            if gt_pixels and overlap / gt_pixels >= object_hit_ratio:
                objects_detected = 1
                break

    return PixelMetrics(
        tp_pixels=tp, fp_pixels=fp, fn_pixels=fn,
        precision=round(precision, 4) if precision is not None else None,
        recall=round(recall, 4) if recall is not None else None,
        fp_rate_per_megapixel=round(fp / (size * size / 1_000_000), 1),
        objects_detected=objects_detected, objects_total=objects_total)


# ---------------------------------------------------------------- protocol


class AdvisoryOnly(str, Enum):
    AI_ADVISORY = "ai_advisory"
    # there is NO 'compliant' verdict in this protocol - by design.


class DefectFinding(BaseModel):
    """The record a detector (baseline or future model) emits.
    advisory_only is forced True: the field exists so downstream UI
    cannot 'forget' to label AI output as advisory."""

    finding_id: str
    detector: str                     # e.g. 'classical-baseline-v1'
    detector_version: str = "1"
    kind: str                         # crack / rust / leak / unknown
    box: tuple[int, int, int, int]
    confidence: float = Field(ge=0.0, le=1.0)
    advisory_only: bool = True        # may never be False

    @field_validator("advisory_only")
    @classmethod
    def _always_advisory(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("defect findings are ADVISORY ONLY - an "
                             "automatic engineering-standard verdict is "
                             "forbidden by protocol")
        return v

    def human_label(self) -> str:
        return (f"{self.kind} (candidate) - AI advisory, "
                f"requires human confirmation; not a compliance verdict")
