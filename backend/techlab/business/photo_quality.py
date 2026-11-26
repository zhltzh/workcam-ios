"""M03-C04/C05: photo quality checks + duplicate candidates.

C04: blur / over-exposure / under-exposure / low-contrast /
     insufficient-resolution / decodable-warning detection. OUTPUT
     IS ADVICE ONLY - a quality hint never deletes a photo and never
     says anything about evidence authenticity.

C05: EXACT duplicates (SHA-256) vs NEAR duplicates (perceptual
     hashes: average hash + difference hash + Hamming distance).
     Near-duplicates are CANDIDATES for human review - never treated
     as the same evidence, never auto-deleted.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass
from enum import Enum

from PIL import Image, ImageFilter


# ============================================================ C04: quality

class QualityFlag(str, Enum):
    ok = "ok"
    blurry = "blurry"
    overexposed = "overexposed"
    underexposed = "underexposed"
    low_contrast = "low_contrast"
    low_resolution = "low_resolution"
    decode_warning = "decode_warning"
    tiny = "tiny"


@dataclass(frozen=True)
class QualityReport:
    flags: list[QualityFlag]
    metrics: dict
    advice: list[str]

    @property
    def ok(self) -> bool:
        return not self.flags


MIN_RECOMMENDED_PIXELS = 640 * 480


def _luminance_channel(img: Image.Image) -> Image.Image:
    return img.convert("L")


def variance_of_laplacian(gray: Image.Image) -> float:
    """Classic blur metric: Laplacian response variance. Sharp
    photos have high variance; blurry ones low. Pure PIL (portable):
    a 3x3 Laplacian via ImageFilter.Kernel."""
    laplacian = gray.filter(ImageFilter.Kernel(
        (3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    hist = laplacian.histogram()
    total = sum(hist)
    if total == 0:
        return 0.0
    mean = sum(i * n for i, n in enumerate(hist)) / total
    var = sum(n * (i - mean) ** 2 for i, n in enumerate(hist)) / total
    return var


BLUR_THRESHOLD = 100.0          # engineering default; tune per device


def assess_quality(data: bytes) -> QualityReport:
    """Decode + measure. Advice only - this module NEVER judges
    evidence value or deletes anything (callers decide)."""
    flags: list[QualityFlag] = []
    advice: list[str] = []
    metrics: dict = {}
    try:
        img = Image.open(io.BytesIO(data))
        img.load()          # force decode NOW (catches truncation)
    except Exception as e:
        return QualityReport(
            [QualityFlag.decode_warning],
            {"decode_error": str(e)[:200]},
            ["image failed to decode fully - it may be truncated "
             "or corrupt; verify before relying on it"])
    w, h = img.size
    metrics["width"] = w
    metrics["height"] = h
    metrics["megapixels"] = round(w * h / 1e6, 2)

    if w * h < MIN_RECOMMENDED_PIXELS:
        flags.append(QualityFlag.low_resolution)
        advice.append(f"{w}x{h} is below the {MIN_RECOMMENDED_PIXELS}"
                      "-pixel recommendation for report use")
    if w * h < 200 * 150:
        flags.append(QualityFlag.tiny)
        advice.append("image is extremely small - likely unusable")

    gray = _luminance_channel(img)
    hist = gray.histogram()
    total = sum(hist) or 1
    mean = sum(i * n for i, n in enumerate(hist)) / total

    # exposure: fraction of near-white / near-black pixels
    whites = sum(hist[246:]) / total
    blacks = sum(hist[:10]) / total
    metrics["mean_luma"] = round(mean, 1)
    metrics["white_fraction"] = round(whites, 3)
    metrics["black_fraction"] = round(blacks, 3)
    if whites > 0.35:
        flags.append(QualityFlag.overexposed)
        advice.append(f"{whites:.0%} of pixels are near-white - "
                      f"consider retaking with lower exposure")
    if blacks > 0.35:
        flags.append(QualityFlag.underexposed)
        advice.append(f"{blacks:.0%} of pixels are near-black - "
                      f"consider retaking with more light")

    # contrast: luminance standard deviation
    var = sum(n * (i - mean) ** 2 for i, n in enumerate(hist)) / total
    std = math.sqrt(var)
    metrics["luma_std"] = round(std, 1)
    if std < 20 and not flags:      # flat gray image
        flags.append(QualityFlag.low_contrast)
        advice.append("very low contrast - scene may be hazy, "
                      "flat-lit or out of focus")

    # blur
    blur_var = variance_of_laplacian(gray)
    metrics["laplacian_var"] = round(blur_var, 1)
    if blur_var < BLUR_THRESHOLD and not flags:
        flags.append(QualityFlag.blurry)
        advice.append("image looks blurry (low edge energy) - "
                      "steady the device and retake if the detail "
                      "matters")

    if not advice:
        advice.append("no quality issues detected by the "
                      "automated checks")
    # the disclaimer is ALWAYS present: quality advice never says
    # anything about evidentiary value
    advice.append("(automated quality hints say nothing about the "
                  "photo's evidentiary value)")
    return QualityReport(flags, metrics, advice)


# ====================================================== C05: duplicates

def exact_duplicate_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _a_hash(gray_small: Image.Image) -> int:
    """Average hash: compare each pixel to the mean -> 64 bits."""
    raw = gray_small.tobytes()
    mean = sum(raw) / len(raw)
    bits = 0
    for p in raw:
        bits = (bits << 1) | (1 if p > mean else 0)
    return bits


def _d_hash(gray_small: Image.Image) -> int:
    """Difference hash: horizontal gradient -> 64 bits (9x8)."""
    w, h = gray_small.size
    px = gray_small.load()
    bits = 0
    for row in range(h):
        for col in range(w - 1):
            bits = (bits << 1) | (1 if px[col, row] > px[col + 1, row]
                                  else 0)
    return bits


def perceptual_hashes(data: bytes) -> dict:
    """Compute aHash (8x8) and dHash (9x8) over the grayscale
    downsample. Throws decode errors up (same contract as quality)."""
    img = Image.open(io.BytesIO(data))
    a = _a_hash(img.convert("L").resize((8, 8),
                                       Image.Resampling.LANCZOS))
    d = _d_hash(img.convert("L").resize((9, 8),
                                        Image.Resampling.LANCZOS))
    return {"ahash": a, "dhash": d,
            "ahash_hex": f"{a:016x}", "dhash_hex": f"{d:016x}"}


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


@dataclass(frozen=True)
class DuplicateCandidate:
    photo_a: str
    photo_b: str
    kind: str          # 'exact' | 'near'
    distance: int      # hamming (near) / 0 (exact)
    hash_name: str

    @property
    def is_exact(self) -> bool:
        return self.kind == "exact"


def find_duplicates(items: list[tuple[str, bytes]],
                    near_threshold: int = 10
                    ) -> list[DuplicateCandidate]:
    """items: (photo_id, image_bytes). Returns BOTH kinds:
      - exact: identical SHA-256 (cryptographic identity)
      - near:  perceptually similar CANDIDATES (aHash OR dHash
        within threshold)
    Near candidates are review prompts ONLY - the caller must never
    treat them as the same evidence or delete either side."""
    if not items:
        return []
    keyed = [(pid, data, exact_duplicate_key(data),
              perceptual_hashes(data)) for pid, data in items]
    out: list[DuplicateCandidate] = []
    # exact pass
    seen: dict[str, str] = {}
    for pid, _, sha, _ in keyed:
        if sha in seen:
            out.append(DuplicateCandidate(seen[sha], pid, "exact",
                                           0, "sha256"))
        else:
            seen[sha] = pid
    # near pass (skip identical pairs already reported)
    n = len(keyed)
    for i in range(n):
        for j in range(i + 1, n):
            ai, aj = keyed[i][3], keyed[j][3]
            da = hamming_distance(ai["ahash"], aj["ahash"])
            dd = hamming_distance(ai["dhash"], aj["dhash"])
            pid_a, pid_b = keyed[i][0], keyed[j][0]
            if keyed[i][2] == keyed[j][2]:
                continue      # already reported as exact
            if da <= near_threshold:
                out.append(DuplicateCandidate(pid_a, pid_b, "near",
                                               da, "ahash"))
            elif dd <= near_threshold:
                out.append(DuplicateCandidate(pid_a, pid_b, "near",
                                               dd, "dhash"))
    return out
