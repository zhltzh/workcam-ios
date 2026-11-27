"""TM-04 E07: two-stage translation registration (phase-1 PoC).

Stage 1 - FFT phase correlation on mean-subtracted grayscale patches:
    the normalized cross-power spectrum peak encodes the integer
    circular shift; the peak's sharpness (peak-to-median ratio) is
    the confidence gate.

Stage 2 - overlap-window NCC refinement around the coarse estimate:
    for each candidate integer offset in a small window the moving
    patch is shifted and compared with the reference over the TRUE
    OVERLAP only (no wrapped garbage, no clamped fabrication); a
    quadratic parabola fit on the NCC surface gives the subpixel
    estimate. The NCC score at the optimum is the second, independent
    confidence gate.

Both gates REFUSE with a named reason; the engine never returns a
low-confidence transform. Wall-clock timing is never measured or
asserted here (the two-flakes rule).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

METHOD_ID = "pc+ncc/1"


class RegistrationError(Exception):
    """Named refusal - the pair is not auto-registratable."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class RegistrationResult:
    dx: float                    # pixels, moving -> reference frame
    dy: float
    peak_sharpness: float        # stage-1 gate value
    ncc_score: float             # stage-2 gate value
    method: str = METHOD_ID


def _to_patch(gray, size: int = 128) -> np.ndarray:
    """Center-crop to a fixed square, float32, (v=row, u=col)."""
    a = np.asarray(gray, dtype=np.float32)
    if a.ndim != 2:
        raise RegistrationError("not_grayscale", f"ndim={a.ndim}")
    h, w = a.shape
    if h < size or w < size:
        raise RegistrationError("patch_too_small", f"{w}x{h} < {size}")
    top = (h - size) // 2
    left = (w - size) // 2
    return a[top:top + size, left:left + size]


def _phase_correlation(a: np.ndarray,
                       b: np.ndarray) -> tuple[int, int, float]:
    """Coarse circular-shift estimate + peak sharpness.

    Convention: the returned (du, dv) is the shift that, applied to
    `b` (the moving patch), best aligns it with `a` (reference).
    sharpness = peak / median(|surface|), a deterministic confidence
    proxy (large => one clean peak; ~1 => flat noise).
    """
    fa = np.fft.rfft2(a - a.mean())
    fb = np.fft.rfft2(b - b.mean())
    # b is the MOVING patch: b(x) = a(x - s). Fb*conj(Fa) peaks at
    # +s (DFT shift theorem), so the returned shift IS the moving
    # displacement - matching the _overlap_ncc convention below.
    cross = fb * np.conj(fa)
    mag = np.abs(cross)
    with np.errstate(divide="ignore", invalid="ignore"):
        norm = np.where(mag > 1e-12, cross / mag, 0)
    surface = np.fft.irfft2(norm, s=a.shape)
    surface = np.fft.fftshift(surface)
    size = surface.shape[0]
    center = size // 2
    flat = int(np.argmax(surface))
    v, u = divmod(flat, size)
    sharp = float(surface[v, u]
                  / (np.median(np.abs(surface)) + 1e-12))
    du, dv = u - center, v - center
    if du > center:
        du -= size
    if dv > center:
        dv -= size
    return du, dv, sharp


def _overlap_ncc(ref: np.ndarray, mov: np.ndarray,
                 du: int, dv: int) -> float | None:
    """NCC of ref/mov over the valid overlap for shift (du, dv).

    Positive du means the MOVING image was displaced +du relative to
    the reference (moving content moved right/down); the comparison
    window reads mov at ref-coordinate + shift. The overlap excludes
    the exposed bands on both patches, so only true-corresponding
    pixels contribute. Returns None when the shift leaves less than
    half the patch in common.
    """
    h, w = ref.shape
    if abs(du) >= w // 2 or abs(dv) >= h // 2:
        return None
    # overlap window in ref coordinates
    r_u0, r_u1 = max(0, -du), min(w, w - du)
    r_v0, r_v1 = max(0, -dv), min(h, h - dv)
    # matching window in mov coordinates (mov(u) = ref(u - shift))
    m_u0, m_v0 = r_u0 + du, r_v0 + dv
    ra = ref[r_v0:r_v1, r_u0:r_u1]
    mb = mov[m_v0:m_v0 + (r_v1 - r_v0),
             m_u0:m_u0 + (r_u1 - r_u0)]
    if ra.shape != mb.shape or ra.size < (h * w) // 4:
        return None
    an = ra - ra.mean()
    bn = mb - mb.mean()
    denom = math.sqrt(float((an * an).sum()) * float((bn * bn).sum()))
    if denom < 1e-12:
        return None
    return float((an * bn).sum() / denom)


def _refine(ref: np.ndarray, mov: np.ndarray, du0: int, dv0: int,
            window: int = 3) -> tuple[float, float, float]:
    """Integer search in a +/-window around the coarse estimate,
    then a parabola subpixel fit per axis. Returns (du, dv, score).
    """
    scores: dict[tuple[int, int], float] = {}
    for ddu in range(-window, window + 1):
        for ddv in range(-window, window + 1):
            s = _overlap_ncc(ref, mov, du0 + ddu, dv0 + ddv)
            if s is not None:
                scores[(du0 + ddu, dv0 + ddv)] = s
    if not scores:
        raise RegistrationError("refinement_window_out_of_bounds",
                                f"coarse=({du0},{dv0})")
    (bu, bv), best = max(scores.items(), key=lambda kv: kv[1])

    def axis_scores(fixed_v: int) -> list[float] | None:
        vals = []
        for d in (-1, 0, 1):
            s = scores.get((bu + d, fixed_v))
            if s is None:
                s = _overlap_ncc(ref, mov, bu + d, fixed_v)
            if s is None:
                return None
            vals.append(s)
        return vals

    def parabola(vals: list[float]) -> float | None:
        # peak at index 1; offset = 0.5*(s[-1]-s[+1])/(s[0]-2s[1]+s[2])
        denom = vals[0] - 2 * vals[1] + vals[2]
        if abs(denom) < 1e-12:
            return None
        off = 0.5 * (vals[0] - vals[2]) / denom
        if not math.isfinite(off) or abs(off) > 1.0:
            return None
        return off

    du_f, dv_f = float(bu), float(bv)
    su_vals = axis_scores(bv)
    if su_vals is not None:
        off = parabola(su_vals)
        if off is not None:
            du_f = bu + off
    sv_vals = None
    sv_scores = []
    for d in (-1, 0, 1):
        s = scores.get((int(round(du_f)), bv + d))
        if s is None:
            s = _overlap_ncc(ref, mov, int(round(du_f)), bv + d)
        if s is None:
            sv_scores = None
            break
        sv_scores.append(s)
    sv_vals = sv_scores
    if sv_vals:
        off = parabola(sv_vals)
        if off is not None:
            dv_f = bv + off
    return du_f, dv_f, best


def estimate_translation(
        reference_gray, moving_gray, *,
        min_sharpness: float = 2.0, min_ncc: float = 0.30,
        patch_size: int = 128) -> RegistrationResult:
    """Full two-stage estimate with named-refusal gates.

    Raises RegistrationError with one of:
      not_grayscale / patch_too_small
      low_peak_sharpness   (stage 1 - pair not correlatable)
      low_ncc_score        (stage 2 - refined alignment unconvincing)
      refinement_window_out_of_bounds
    """
    a = _to_patch(reference_gray, patch_size)
    b = _to_patch(moving_gray, patch_size)
    du, dv, sharp = _phase_correlation(a, b)
    if sharp < min_sharpness:
        raise RegistrationError(
            "low_peak_sharpness",
            f"sharpness={sharp:.3f} < {min_sharpness}")
    du_f, dv_f, ncc = _refine(a, b, du, dv)
    if ncc < min_ncc:
        raise RegistrationError(
            "low_ncc_score", f"ncc={ncc:.3f} < {min_ncc}")
    return RegistrationResult(
        dx=float(du_f), dy=float(dv_f),
        peak_sharpness=sharp, ncc_score=ncc)
