"""W-02 / T04 - LiDAR measurement statistics core (tech-lab).

Pure-Python statistics over LiDAR lab CSV rows, shared by the analyzer
CLI and the batch review tool.

CSV compatibility (superset):
- Mac LidarLabView.swift 20-column header (PR #9):
  batch_id,test_id,device_model,ios_version,depth_received,
  confidence_end,confidence_start,tracking,mode,condition,device_time,
  distance_true_m,distance_measured_m,absolute_error_m,relative_error,
  elapsed_s,failure,distance_m,first_device_time,first_tracking
- Legacy 4-column header (Wave-1 tool): distance_true_m,
  distance_measured_m, condition, distance_m  (mode defaults to
  'raycast', failure = empty measurement, test_id = row number)

HONESTY RULES:
- Cells with fewer than MIN_SAMPLES measurements report null for every
  statistic. NaN/Infinity may NEVER appear (JSON is emitted with
  allow_nan=False as a hard guarantee).
- Failures are counted and their raw rows are preserved verbatim -
  never dropped, never turned into zero-error samples.
- SYNTHETIC sample data is always labeled as such.
"""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass, field

MIN_SAMPLES = 3          # below this -> null (honest "insufficient data")
VALID_MODES = {"raycast", "scene_depth", "smoothed_depth"}
VALID_CONDITIONS = {"normal", "low_light", "glass", "oblique",
                   "textureless", "reflective"}

MAC_COLUMNS = ["batch_id", "test_id", "device_model", "ios_version",
               "depth_received", "confidence_end", "confidence_start",
               "tracking", "mode", "condition", "device_time",
               "distance_true_m", "distance_measured_m", "absolute_error_m",
               "relative_error", "elapsed_s", "failure", "distance_m",
               "first_device_time", "first_tracking"]
LEGACY_COLUMNS = ["distance_true_m", "distance_measured_m", "condition",
                  "distance_m"]


class CSVFormatError(ValueError):
    pass


@dataclass
class Measurement:
    test_id: str
    mode: str
    condition: str
    distance_true_m: float
    distance_measured_m: float | None     # None = failure row
    distance_m: float | None              # device-to-target setup distance
    failure: str                          # "" on success, reason on failure
    raw: dict = field(default_factory=dict)


def _parse_float(text: str | None) -> float | None:
    if text is None:
        return None
    text = text.strip()
    if text == "" or text.lower() == "nan":
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_csv(csv_text: str) -> tuple[list[Measurement], dict]:
    """Parse Mac or legacy CSV. Returns (measurements, warnings)."""
    reader = csv.DictReader(io.StringIO(csv_text))
    header = reader.fieldnames or []
    header_lower = [h.strip().lower() for h in header]

    is_mac = set(header_lower) >= {"test_id", "mode", "failure"}
    is_legacy = set(LEGACY_COLUMNS) >= set(header_lower)
    if not is_mac and not is_legacy:
        raise CSVFormatError(
            f"unrecognized CSV header: {header[:6]}... "
            f"(expected Mac 20-column or legacy 4-column)"
        )

    warnings: list[str] = []
    rows: list[Measurement] = []
    seen_ids: dict[str, int] = {}
    for lineno, row in enumerate(reader, start=2):
        raw = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
        if is_mac:
            test_id = raw.get("test_id", "").strip() or f"line{lineno}"
            mode = raw.get("mode", "raycast").strip() or "raycast"
            failure = raw.get("failure", "").strip()
        else:
            test_id = f"line{lineno}"
            mode = "raycast"
            failure = ""          # legacy: empty measurement == failure

        condition = raw.get("condition", "unspecified").strip() or "unspecified"
        true_m = _parse_float(raw.get("distance_true_m"))
        measured_m = _parse_float(raw.get("distance_measured_m"))
        dist_m = _parse_float(raw.get("distance_m"))

        if true_m is None:
            warnings.append(f"line {lineno}: missing/invalid distance_true_m - row skipped")
            continue
        if is_mac and not failure and measured_m is None:
            # Mac row with no failure reason but no measurement either:
            # count it as an UNEXPLAINED failure, never drop it.
            failure = "measurement_missing"
        if not is_mac and measured_m is None:
            failure = failure or "measurement_missing"

        if mode not in VALID_MODES:
            warnings.append(f"line {lineno}: unknown mode '{mode}' - kept as-is")
        if condition not in VALID_CONDITIONS:
            warnings.append(f"line {lineno}: unknown condition '{condition}' - kept as-is")

        if test_id in seen_ids:
            warnings.append(
                f"line {lineno}: DUPLICATE test_id '{test_id}' "
                f"(first seen on line {seen_ids[test_id]})"
            )
        else:
            seen_ids[test_id] = lineno

        rows.append(Measurement(
            test_id=test_id, mode=mode, condition=condition,
            distance_true_m=true_m, distance_measured_m=measured_m,
            distance_m=dist_m, failure=failure, raw=raw,
        ))
    return rows, {"warnings": warnings, "format": "mac" if is_mac else "legacy"}


# ------------------------------------------------------------------ stats


def _safe(f):
    """null for non-finite / None, else rounded float."""
    if f is None:
        return None
    try:
        v = float(f)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _mean(xs: list[float]):
    return sum(xs) / len(xs) if xs else None


def _stddev(xs: list[float]):
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _median(xs: list[float]):
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def _percentile(xs: list[float], p: float):
    """Linear-interpolated percentile; None for empty input."""
    if not xs:
        return None
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * p / 100.0
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (rank - lo) * (xs[hi] - xs[lo])


def stats_for(group: list[Measurement]) -> dict:
    """Statistics for one group of measurements.

    Every numeric field is null when the group has < MIN_SAMPLES
    successes; failure counts are always reported (honesty: failures are
    data, successes are statistics)."""
    attempts = len(group)
    ok = [m for m in group if m.failure == "" and m.distance_measured_m is not None]
    failures = [m for m in group if m.failure != "" or m.distance_measured_m is None]

    abs_errors = [abs(m.distance_measured_m - m.distance_true_m) for m in ok]
    signed = [m.distance_measured_m - m.distance_true_m for m in ok]

    failure_reasons: dict[str, int] = {}
    for m in failures:
        reason = m.failure or "measurement_missing"
        failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    if len(ok) < MIN_SAMPLES:
        numeric = {k: None for k in (
            "success_rate", "failure_rate", "mean_abs_error_m", "max_abs_error_m",
            "rmse_m", "median_abs_error_m", "p95_abs_error_m",
            "mean_signed_error_m", "repeatability_stddev_m",
        )}
    else:
        numeric = {
            "success_rate": len(ok) / attempts if attempts else None,
            "failure_rate": len(failures) / attempts if attempts else None,
            "mean_abs_error_m": _safe(_mean(abs_errors)),
            "max_abs_error_m": _safe(max(abs_errors)) if abs_errors else None,
            "rmse_m": _safe(math.sqrt(_mean([e * e for e in abs_errors]))
                            if abs_errors else None),
            "median_abs_error_m": _safe(_median(abs_errors)),
            "p95_abs_error_m": _safe(_percentile(abs_errors, 95)),
            "mean_signed_error_m": _safe(_mean(signed)),
            # repeatability: spread of SIGNED error across repeats
            "repeatability_stddev_m": _safe(_stddev(signed)),
        }

    return {
        "attempts": attempts,
        "successes": len(ok),
        "failures": len(failures),
        "failure_reasons": failure_reasons,
        "samples_for_statistics": len(ok),
        "sufficient_samples": len(ok) >= MIN_SAMPLES,
        **numeric,
    }


def _group(rows: list[Measurement], key_fn) -> dict:
    groups: dict[str, list[Measurement]] = {}
    for m in rows:
        groups.setdefault(key_fn(m), []).append(m)
    return groups


def analyze(rows: list[Measurement]) -> dict:
    """Full analysis: overall + per-mode + per-condition + per-distance."""
    def cell_key(m: Measurement) -> str:
        d = f"{m.distance_true_m:g}m" if m.distance_true_m is not None else "?"
        return f"{m.mode}/{m.condition}/{d}"

    return {
        "overall": stats_for(rows),
        "by_mode": {k: stats_for(v) for k, v in sorted(_group(rows, lambda m: m.mode).items())},
        "by_condition": {k: stats_for(v) for k, v in sorted(_group(rows, lambda m: m.condition).items())},
        "by_true_distance": {
            f"{k:g}m": stats_for(v)
            for k, v in sorted(_group(rows, lambda m: m.distance_true_m).items())
        },
        "by_cell": {k: stats_for(v) for k, v in sorted(_group(rows, cell_key).items())},
        "failure_rows": [
            {"test_id": m.test_id, "mode": m.mode, "condition": m.condition,
             "distance_true_m": m.distance_true_m, "failure": m.failure or "measurement_missing"}
            for m in rows if m.failure != "" or m.distance_measured_m is None
        ],
    }


def to_json(analysis: dict) -> str:
    """Strict-JSON dump: NaN/Infinity would RAISE instead of leaking."""
    return json.dumps(analysis, indent=2, ensure_ascii=False, allow_nan=False)
