"""T17 - photo pair comparison & change detection (Wave 4).

RESEARCH CONCLUSION FIRST:
  Real change detection needs image registration robust to viewpoint
  (homography / feature matching) - that needs OpenCV or Accelerate/
  vImage on iOS. This box has neither, and downloading models/datasets
  is out of bounds. What CAN be honestly built with stdlib on small
  synthetic pairs:

  1. TRANSLATION registration via exhaustive search on a coarse grid
     (the reproducible core every robust method still needs);
  2. a DIFFERENCE HEATMAP after registration, with an explicit
     illumination-normalization step FIRST (raw pixel diff fires on
     exposure changes - the classic false positive);
  3. honest false-positive experiments: same scene + brightness shift
     must yield ZERO change regions after normalization; same scene +
     real change must be found; angle/scale change is OUT of scope and
     recorded as such (translation-only limitation, stated, not hidden).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class PairError(ValueError):
    pass


class GrayImage:
    """Pure-stdlib grayscale image."""

    def __init__(self, size: int, pixels: list[int]):
        if len(pixels) != size * size:
            raise PairError(f"expected {size*size} pixels, got {len(pixels)}")
        if any(not (0 <= p <= 255) for p in pixels):
            raise PairError("pixel out of range")
        self.size = size
        self.pixels = pixels

    @classmethod
    def synthetic_scene(cls, size: int, seed: int, brightness: int = 0,
                        change_rect: tuple[int, int, int, int] | None = None,
                        change_delta: int = 90) -> "GrayImage":
        """Deterministic NON-PERIODIC scene: per-pixel hashed texture
        (every position unique, like real photo detail). The Wave-4
        draft used (x*7+y*3)%40, which aliases under translation -
        shifts with equal phase produced near-zero residuals and the
        registrar landed on a wrong-but-plausible shift; the vector
        test caught it. Change rectangle = a real scene change."""
        import random
        pixels = []
        for y in range(size):
            for x in range(size):
                px_rng = random.Random(((x * 92837111) ^ (y * 12371113)
                                        ^ (seed * 7600901)) % (2**31))
                base = 60 + px_rng.randint(0, 140)     # wide-contrast texture
                pixels.append(max(0, min(255, base + brightness)))
        if change_rect:
            x0, y0, x1, y1 = change_rect
            for y in range(y0, y1):
                for x in range(x0, x1):
                    pixels[y * size + x] = max(0, min(
                        255, pixels[y * size + x] + change_delta))
        return cls(size, pixels)

    def shifted(self, dx: int, dy: int) -> "GrayImage":
        """Translate content by (dx, dy); border filled by replication."""
        size = self.size
        out = [0] * (size * size)
        for y in range(size):
            for x in range(size):
                sx, sy = x - dx, y - dy
                sx = max(0, min(size - 1, sx))
                sy = max(0, min(size - 1, sy))
                out[y * size + x] = self.pixels[sy * size + sx]
        return GrayImage(size, out)

    def normalized(self) -> "GrayImage":
        """Illumination normalization: subtract per-image mean, scale to
        unit variance over 0-255. Kills global exposure shifts."""
        n = len(self.pixels)
        mean = sum(self.pixels) / n
        var = sum((p - mean) ** 2 for p in self.pixels) / n
        std = var ** 0.5 or 1.0
        out = [max(0, min(255, int((p - mean) * (64.0 / std) + 128)))
               for p in self.pixels]
        return GrayImage(self.size, out)

    def diff_map(self, other: "GrayImage", threshold: int = 45) -> list[bool]:
        if self.size != other.size:
            raise PairError("image sizes differ")
        return [abs(a - b) > threshold
                for a, b in zip(self.pixels, other.pixels)]


@dataclass
class Registration:
    dx: int
    dy: int
    residual: float           # mean |diff| at the chosen shift


class TranslationRegistrar:
    """Exhaustive translation search on a bounded grid, using
    normalized images. Coarse (2-px steps) then fine (1-px)."""

    def __init__(self, max_shift: int = 12):
        self.max_shift = max_shift

    def register(self, ref: GrayImage, mov: GrayImage) -> Registration:
        """Exhaustive translation search on a bounded grid.

        Residual = mean-removed MAE over the interior overlap window.
        Rationale (found the hard way by the vector test): normalizing
        whole images pollutes the mean/std with REPLICATED-BORDER
        pixels (~30% of a small shifted image), so the normalized
        domains of ref and mov stop corresponding and the search lands
        on wrong-but-plausible shifts. Removing the WINDOW mean absorbs
        global exposure and never touches borders."""
        if ref.size != mov.size:
            raise PairError("image sizes differ")
        size = ref.size
        m = self.max_shift
        lo, hi = m, size - m
        ref_win = [ref.pixels[y * size + x]
                   for y in range(lo, hi) for x in range(lo, hi)]
        ref_mean = sum(ref_win) / len(ref_win)

        def _residual(dx: int, dy: int) -> float:
            shifted = mov.shifted(dx, dy)
            win = [shifted.pixels[y * size + x]
                   for y in range(lo, hi) for x in range(lo, hi)]
            win_mean = sum(win) / len(win)
            total = sum(abs((a - ref_mean) - (b - win_mean))
                        for a, b in zip(ref_win, win))
            return total / len(win)

        best: Registration | None = None
        # FULL 1-px exhaustive search (no coarse stage). Why: with
        # per-pixel random texture the residual surface is a delta peak
        # (true shift 0.0, every neighbour ~13) - a 2-px coarse pass
        # lands wherever noise is lowest and the +-2 refine never
        # reaches the peak. Feasible here because images are small;
        # REAL photos have smooth residual surfaces where pyramid
        # search applies - noted as a Mac-side optimization, not a
        # silent guess (see ChangeReport limitations).
        for dx in range(-m, m + 1):
            for dy in range(-m, m + 1):
                residual = _residual(dx, dy)
                if best is None or residual < best.residual:
                    best = Registration(dx=dx, dy=dy, residual=residual)
        assert best is not None
        return best


@dataclass
class ChangeRegion:
    box: tuple[int, int, int, int]
    pixel_count: int


def detect_change_regions(ref: GrayImage, mov: GrayImage,
                          registrar: TranslationRegistrar | None = None,
                          diff_threshold: int = 45,
                          min_pixels: int = 12) -> tuple[Registration, list[ChangeRegion]]:
    """Full pipeline: register -> align -> window-mean-removed diff ->
    connected regions. Returns the registration (for audit) and the
    regions. The diff is mean-removed over the interior window (same
    border-pollution lesson as the registrar: whole-image normalization
    mixes replicated-border pixels into the statistics)."""
    registrar = registrar or TranslationRegistrar()
    reg = registrar.register(ref, mov)
    aligned = mov.shifted(reg.dx, reg.dy)
    size = ref.size
    m = registrar.max_shift
    lo, hi = m, size - m

    # means over the INTERIOR only (border replication excluded)
    interior_ref = [ref.pixels[y * size + x] for y in range(lo, hi) for x in range(lo, hi)]
    interior_ali = [aligned.pixels[y * size + x] for y in range(lo, hi) for x in range(lo, hi)]
    ref_mean = sum(interior_ref) / len(interior_ref)
    ali_mean = sum(interior_ali) / len(interior_ali)
    mask = [abs((a - ref_mean) - (b - ali_mean)) > diff_threshold
            for a, b in zip(ref.pixels, aligned.pixels)]

    # connected components on the diff mask (4-connectivity BFS)
    visited = [False] * (size * size)
    regions: list[ChangeRegion] = []
    for idx in range(size * size):
        if not mask[idx] or visited[idx]:
            continue
        stack, xs, ys, count = [idx], [], [], 0
        visited[idx] = True
        while stack:
            cur = stack.pop()
            cx, cy = cur % size, cur // size
            xs.append(cx); ys.append(cy); count += 1
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                if 0 <= nx < size and 0 <= ny < size:
                    nidx = ny * size + nx
                    if mask[nidx] and not visited[nidx]:
                        visited[nidx] = True
                        stack.append(nidx)
        if count >= min_pixels:
            regions.append(ChangeRegion(
                box=(min(xs), min(ys), max(xs), max(ys)), pixel_count=count))
    return reg, regions


class ChangeReport(BaseModel):
    """Honesty-first report: scene-change detection results are
    ADVISORY, and the KNOWN blindnesses are part of the report."""

    registration: tuple[int, int]
    change_regions: list[tuple[int, int, int, int]]
    advisory_only: bool = True
    limitations: list[str] = Field(default_factory=lambda: [
        "translation-only registration: viewpoint angle or scale changes "
        "are NOT handled and will produce spurious regions",
        "synthetic grayscale testbench: real-photo performance untested "
        "(DEVICE-equivalent work is a Mac task)",
    ])

    def human_label(self) -> str:
        n = len(self.change_regions)
        return (f"{n} candidate change region(s) - advisory, verify "
                f"manually; not a damage verdict")
