"""TM-04 E07 (was TM-03 C06): registration PoC tests.

Synthetic ground truth only: every displacement is KNOWN by
construction, so error assertions are exact functional invariants -
no wall-clock, no flakiness (fixed seeds throughout).

Budget (from the selection review):
  integer & subpixel shifts up to ~12 px  -> |error| <= 0.5 px P95
  (tests assert the stricter per-case bound so a regression is
  caught immediately, not statistically)
  uncorrelated pair / 10-degree rotation / tiny patch -> NAMED
  refusal, never a fabricated transform.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from techlab.registration import (
    RegistrationError,
    estimate_translation,
)

SIZE = 160          # patch head-room around the 128 estimation window
RNG = np.random.default_rng(20261128)


def _texture(seed: int = 7, size: int = SIZE) -> np.ndarray:
    """Structured random texture: white noise smoothed into blobs -
    has both fine detail (phase correlation) and structure (NCC)."""
    r = np.random.default_rng(seed)
    fine = r.normal(0, 8, (size, size))
    coarse = np.kron(r.normal(0, 40, (size // 8, size // 8)),
                    np.ones((8, 8)))
    t = fine + coarse
    # a few high-contrast anchors (screws/edges stand-in)
    for _ in range(12):
        v, u = r.integers(20, size - 20, 2)
        s = int(r.integers(3, 9))
        t[v - s:v + s, u - s:u + s] += r.normal(60, 20)
    return np.clip(t, 0, 255).astype(np.float32)


def _shift_bilinear(img: np.ndarray, du: float, dv: float) -> np.ndarray:
    """Ground-truth subpixel shift of the MOVING image: pixel (u, v)
    of the result samples img at (u + du, v + dv)."""
    h, w = img.shape
    uu = np.arange(w, dtype=np.float32) + du
    vv = np.arange(h, dtype=np.float32) + dv
    u0 = np.clip(np.floor(uu).astype(int), 0, w - 2)
    v0 = np.clip(np.floor(vv).astype(int), 0, h - 2)
    fu = (uu - u0)[None, :]
    fv = (vv - v0)[:, None]
    out = np.zeros((h, w), dtype=np.float32)
    for v in range(h):
        vi, fr = v0[v], fv[v]
        rows = img[vi][None, :] * 0 + img  # keep linter honest
        a = img[vi][u0] * (1 - fu) + img[vi][u0 + 1] * fu
        b = img[vi + 1][u0] * (1 - fu) + img[vi + 1][u0 + 1] * fu
        out[v] = (a * (1 - fr) + b * fr)[0] if a.ndim == 2 else \
            a * (1 - fr) + b * fr
    return out


def _shift_simple(img: np.ndarray, du: float, dv: float) -> np.ndarray:
    """Straightforward bilinear shift (vectorized)."""
    h, w = img.shape
    uu = np.arange(w, dtype=np.float32) - du
    vv = np.arange(h, dtype=np.float32) - dv
    u0 = np.clip(np.floor(uu).astype(int), 0, w - 2)
    v0 = np.clip(np.floor(vv).astype(int), 0, h - 2)
    fu = np.clip(uu - u0, 0.0, 1.0)[None, :].astype(np.float32)
    fv = np.clip(vv - v0, 0.0, 1.0)[:, None].astype(np.float32)
    U0, V0 = np.meshgrid(u0, v0)
    a = img[V0, U0] * (1 - fu) + img[V0, U0 + 1] * fu
    b = img[V0 + 1, U0] * (1 - fu) + img[V0 + 1, U0 + 1] * fu
    return (a * (1 - fv) + b * fv).astype(np.float32)


class TestKnownShifts:
    @pytest.mark.parametrize("du,dv", [
        (0, 0), (3, 0), (0, -4), (5, 7), (-6, -8), (10, 2), (-11, 9),
    ])
    def test_integer_shifts_recovered_within_half_pixel(
            self, du, dv):
        base = _texture()
        moving = np.roll(np.roll(base, dv, axis=0), du, axis=1)
        r = estimate_translation(base, moving)
        assert abs(r.dx - du) <= 0.5, f"dx {r.dx} vs {du}"
        assert abs(r.dy - dv) <= 0.5, f"dy {r.dy} vs {dv}"
        assert r.method == "pc+ncc/1"

    @pytest.mark.parametrize("du,dv", [
        (2.3, -1.7), (-3.5, 4.5), (6.75, 0.25), (0.6, 0.6),
    ])
    def test_subpixel_shifts_recovered_within_half_pixel(
            self, du, dv):
        base = _texture()
        moving = _shift_simple(base, du, dv)
        r = estimate_translation(base, moving)
        assert abs(r.dx - du) <= 0.5, f"dx {r.dx} vs {du}"
        assert abs(r.dy - dv) <= 0.5, f"dy {r.dy} vs {dv}"

    def test_brightness_shift_does_not_break_registration(self):
        base = _texture()
        moving = np.roll(base, 4, axis=1) + 50.0
        r = estimate_translation(base, moving)
        assert abs(r.dx - 4) <= 0.5
        assert abs(r.dy) <= 0.5

    def test_sensor_noise_does_not_break_registration(self):
        base = _texture()
        noisy = np.random.default_rng(99).normal(
            0, 4, base.shape).astype(np.float32)
        moving = np.roll(base + noisy, -5, axis=0)
        r = estimate_translation(base, moving)
        assert abs(r.dx) <= 0.5
        assert abs(r.dy + 5) <= 0.5


class TestNamedRefusals:
    def test_uncorrelated_pair_is_refused_never_fabricated(self):
        a = _texture(seed=1)
        b = _texture(seed=2)      # different random scene
        with pytest.raises(RegistrationError) as ei:
            estimate_translation(a, b)
        assert ei.value.reason in ("low_peak_sharpness", "low_ncc_score")

    def test_ten_degree_rotation_is_refused_not_misregistered(self):
        base = _texture(seed=3)
        h, w = base.shape
        # rotate about the patch center (nearest-neighbor is fine:
        # the refusal gate must fire well before interpolation
        # quality matters)
        vv, uu = np.mgrid[0:h, 0:w].astype(np.float32)
        cu, cv = (w - 1) / 2, (h - 1) / 2
        ang = math.radians(10)
        ru = (uu - cu) * math.cos(-ang) - (vv - cv) * math.sin(-ang) + cu
        rv = (uu - cu) * math.sin(-ang) + (vv - cv) * math.cos(-ang) + cv
        ru = np.clip(ru, 0, w - 2).astype(int)
        rv = np.clip(rv, 0, h - 2).astype(int)
        rotated = base[rv, ru]
        with pytest.raises(RegistrationError) as ei:
            estimate_translation(base, rotated)
        assert ei.value.reason in ("low_peak_sharpness", "low_ncc_score")

    def test_flat_images_refused(self):
        flat = np.full((128, 128), 42.0, dtype=np.float32)
        with pytest.raises(RegistrationError) as ei:
            estimate_translation(flat, flat.copy())
        assert ei.value.reason in ("low_peak_sharpness", "low_ncc_score")

    def test_too_small_patch_named(self):
        small = np.zeros((16, 16), dtype=np.float32)
        with pytest.raises(RegistrationError) as ei:
            estimate_translation(small, small)
        assert ei.value.reason == "patch_too_small"

    def test_color_input_named(self):
        rgb = np.zeros((200, 200, 3), dtype=np.float32)
        with pytest.raises(RegistrationError) as ei:
            estimate_translation(rgb, rgb)
        assert ei.value.reason == "not_grayscale"


class TestResultShape:
    def test_result_carries_both_gate_values(self):
        base = _texture(seed=5)
        moving = np.roll(base, 3, axis=0)
        r = estimate_translation(base, moving)
        assert r.peak_sharpness >= 2.0
        assert r.ncc_score >= 0.30
        assert math.isfinite(r.dx) and math.isfinite(r.dy)
