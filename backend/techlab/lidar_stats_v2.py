"""B02-W016 - LiDAR measurement statistics v2 (tech-lab).

Upgrade over techlab/lidar_stats.py (W-02) fixing three historic defects
(see repo commit 355edf9 and docs/tech-lab/experiments/b02-w016-lidar-experiment.md):

1. MATRIX HONESTY - declared shot counts must be derivable from the
   actual recorded rows. verify_matrix() rebuilds the declared matrix
   from the checklist and the actual matrix from the CSV records, then
   reports per-mode and per-cell differences. A checklist that claims
   96 shots while its rows add up to 68 can no longer pass silently.

2. MODE SEPARATION - raycast / scene_depth / smoothed_depth are NEVER
   merged. analyze_v2() emits no cross-mode error statistics at all:
   the top level only carries accounting counts; every error metric
   lives inside a per-mode (or per-mode x factor) group.

3. REPEATABILITY DEFINITION - repeatability_stddev_m is the sample
   standard deviation of SIGNED errors of repeated measurements of the
   SAME baseline distance and condition (cell = mode + condition +
   distance_true). It is computed ONLY at cell level; every
   aggregation level (by_mode, by_mode_x_distance, ...) reports null.
   The standard deviation of ABSOLUTE errors is never used as a
   repeatability metric.

HONESTY RULES (inherited and extended):
- Failures, unexplained-missing rows, outliers and invalid rows are
  counted and preserved verbatim; never silently dropped.
- Cells with fewer than MIN_SAMPLES valid samples report null for all
  error statistics (counts stay). NaN/Infinity may never appear
  (strict JSON, allow_nan=False).
- Synthetic batches are labeled (DESKTOP_SIMULATION / SYNTHETIC).

CSV compatibility:
- Mac LidarLabView 20-column header (v1) - material / lighting / pose
  are derived from the condition via CONDITION_FACTORS.
- v2 superset: the same 20 columns plus optional material, lighting,
  pose columns (named, order-independent). Explicit columns win over
  the condition mapping.
- Legacy 4-column Wave-1 header still parses (mode defaults to
  raycast, failure = empty measurement).
"""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass, field

SCHEMA = "b02-w016-lidar-stats/1"
MIN_SAMPLES = 3

VALID_MODES = {"raycast", "scene_depth", "smoothed_depth"}
MODE_ALIASES = {
    "raycast": "raycast",
    "ray_cast": "raycast",
    "ray cast": "raycast",
    "hit_test": "raycast",
    "hit-test": "raycast",
    "scene_depth": "scene_depth",
    "scenedepth": "scene_depth",
    "scene depth": "scene_depth",
    "depth": "scene_depth",
    "smoothed_depth": "smoothed_depth",
    "smoothed": "smoothed_depth",
    "smoothed depth": "smoothed_depth",
    "smoothed_scene_depth": "smoothed_depth",
    "smoothed_scenedepth": "smoothed_depth",
}

# condition -> (material, lighting, pose) factors for v1 CSVs
CONDITION_FACTORS = {
    "normal":      ("matte_white", "normal", "perpendicular"),
    "low_light":   ("matte_white", "low_light", "perpendicular"),
    "glass":       ("glass", "normal", "perpendicular"),
    "oblique":     ("matte_white", "normal", "oblique_30"),
    "textureless": ("textureless", "normal", "perpendicular"),
    "reflective":  ("mirror", "normal", "perpendicular"),
}
VALID_CONDITIONS = set(CONDITION_FACTORS)
VALID_MATERIALS = {"matte_white", "textureless", "glass", "mirror"}
VALID_LIGHTINGS = {"normal", "low_light"}
VALID_POSES = {"perpendicular", "oblique_30"}

MAC_COLUMNS = ["batch_id", "test_id", "device_model", "ios_version",
               "depth_received", "confidence_end", "confidence_start",
               "tracking", "mode", "condition", "device_time",
               "distance_true_m", "distance_measured_m", "absolute_error_m",
               "relative_error", "elapsed_s", "failure", "distance_m",
               "first_device_time", "first_tracking"]
V2_EXTRA_COLUMNS = ["material", "lighting", "pose"]
LEGACY_COLUMNS = ["distance_true_m", "distance_measured_m", "condition",
                  "distance_m"]

# Outlier bounds - REPORTED, never removed from statistics.
OUTLIER_MIN_M = 0.05
OUTLIER_MAX_M = 10.0
OUTLIER_ERROR_M = 1.0

SYNTHETIC_MARKERS = ("DESKTOP_SIMULATION", "SYNTHETIC")


class CSVFormatError(ValueError):
    pass


@dataclass
class Record:
    line: int
    test_id: str
    mode: str
    condition: str
    material: str
    lighting: str
    pose: str
    distance_true_m: float
    distance_measured_m: float | None
    distance_m: float | None
    failure: str
    raw: dict = field(default_factory=dict)


@dataclass
class InvalidRow:
    line: int
    reason: str
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


def _norm_mode(text: str, warnings: list[str], line: int) -> str:
    key = text.strip().lower()
    if key in VALID_MODES:
        return key
    if key in MODE_ALIASES:
        canon = MODE_ALIASES[key]
        warnings.append(f"line {line}: mode '{text}' normalized to '{canon}'")
        return canon
    warnings.append(f"line {line}: unknown mode '{text}' - kept as-is")
    return text.strip()


def _factor(raw: dict, column: str, valid: set[str], fallback: str,
            condition: str, warnings: list[str], line: int) -> str:
    """Explicit v2 column wins over the condition mapping."""
    value = (raw.get(column) or "").strip().lower()
    if value:
        if value not in valid:
            warnings.append(f"line {line}: unknown {column} '{value}' - kept as-is")
        return value
    return fallback


def parse_csv_v2(csv_text: str) -> tuple[list[Record], list[InvalidRow], dict]:
    """Parse v1 Mac / v2 superset / legacy CSV.

    Returns (records, invalid_rows, meta). Invalid rows (missing or
    non-numeric distance_true_m) are RETURNED, not dropped silently.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    header = reader.fieldnames or []
    header_lower = [h.strip().lower() for h in header]

    is_mac = set(header_lower) >= {"test_id", "mode", "failure"}
    is_legacy = set(LEGACY_COLUMNS) >= set(header_lower)
    if not is_mac and not is_legacy:
        raise CSVFormatError(
            f"unrecognized CSV header: {header[:6]}... "
            f"(expected Mac 20-column, v2 superset or legacy 4-column)"
        )

    warnings: list[str] = []
    records: list[Record] = []
    invalid_rows: list[InvalidRow] = []
    seen_ids: dict[str, int] = {}

    for lineno, row in enumerate(reader, start=2):
        raw = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
        if is_mac:
            test_id = raw.get("test_id", "").strip() or f"line{lineno}"
            mode = _norm_mode(raw.get("mode", "raycast"), warnings, lineno)
            failure = raw.get("failure", "").strip()
        else:
            test_id = f"line{lineno}"
            mode = "raycast"
            failure = ""

        condition = raw.get("condition", "unspecified").strip().lower() or "unspecified"
        if is_mac and condition not in VALID_CONDITIONS:
            warnings.append(f"line {lineno}: unknown condition '{condition}' - kept as-is")

        true_m = _parse_float(raw.get("distance_true_m"))
        if true_m is None:
            reason = "missing_or_invalid_distance_true_m"
            invalid_rows.append(InvalidRow(line=lineno, reason=reason, raw=raw))
            warnings.append(f"line {lineno}: {reason} - row kept in invalid_rows")
            continue

        measured_m = _parse_float(raw.get("distance_measured_m"))
        dist_m = _parse_float(raw.get("distance_m"))
        if is_mac and not failure and measured_m is None:
            failure = "measurement_missing"
        if not is_mac and measured_m is None:
            failure = failure or "measurement_missing"

        mat_fb, light_fb, pose_fb = CONDITION_FACTORS.get(condition, ("unknown", "unknown", "unknown"))
        material = _factor(raw, "material", VALID_MATERIALS, mat_fb, condition, warnings, lineno)
        lighting = _factor(raw, "lighting", VALID_LIGHTINGS, light_fb, condition, warnings, lineno)
        pose = _factor(raw, "pose", VALID_POSES, pose_fb, condition, warnings, lineno)

        if test_id in seen_ids:
            warnings.append(
                f"line {lineno}: DUPLICATE test_id '{test_id}' "
                f"(first seen on line {seen_ids[test_id]})"
            )
        else:
            seen_ids[test_id] = lineno

        records.append(Record(
            line=lineno, test_id=test_id, mode=mode, condition=condition,
            material=material, lighting=lighting, pose=pose,
            distance_true_m=true_m, distance_measured_m=measured_m,
            distance_m=dist_m, failure=failure, raw=raw,
        ))

    synthetic = any(m in csv_text for m in SYNTHETIC_MARKERS)
    meta = {
        "format": "mac_v2" if (is_mac and {"material"} <= set(header_lower)) else
                  ("mac" if is_mac else "legacy"),
        "factors_derived_from_condition": not ({"material", "lighting", "pose"} <= set(header_lower)),
        "synthetic": synthetic,
        "data_label": "DESKTOP_SIMULATION" if synthetic else "MAC_EXPORT_UNVERIFIED",
        "warnings": warnings,
    }
    return records, invalid_rows, meta


# ------------------------------------------------------------------ stats


def _safe(f):
    if f is None:
        return None
    try:
        v = float(f)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _stddev(xs):
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _median(xs):
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def _percentile(xs, p):
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


def stats_for_v2(group: list[Record], *, allow_repeatability: bool) -> dict:
    """Statistics for one group.

    Counting metrics (attempts / valid samples / failures / rates) are
    always reported. Error statistics require >= MIN_SAMPLES valid
    samples. repeatability_stddev_m is only computed when the group is
    a single cell (same baseline distance AND same condition); callers
    must pass allow_repeatability=False for any aggregation level.
    """
    attempts = len(group)
    ok = [m for m in group if m.failure == "" and m.distance_measured_m is not None]
    failures = [m for m in group if m.failure != "" or m.distance_measured_m is None]

    abs_errors = [abs(m.distance_measured_m - m.distance_true_m) for m in ok]
    signed = [m.distance_measured_m - m.distance_true_m for m in ok]

    failure_reasons: dict[str, int] = {}
    for m in failures:
        reason = m.failure or "measurement_missing"
        failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    stats = {
        "attempts": attempts,
        "valid_samples": len(ok),
        "failures": len(failures),
        "failure_reasons": failure_reasons,
        "success_rate": (len(ok) / attempts) if attempts else None,
        "failure_rate": (len(failures) / attempts) if attempts else None,
        "sufficient_samples": len(ok) >= MIN_SAMPLES,
    }

    if len(ok) >= MIN_SAMPLES:
        stats.update({
            "mean_signed_error_m": _safe(_mean(signed)),
            "mean_abs_error_m": _safe(_mean(abs_errors)),
            "rmse_m": _safe(math.sqrt(_mean([e * e for e in abs_errors]))),
            "median_abs_error_m": _safe(_median(abs_errors)),
            "p95_abs_error_m": _safe(_percentile(abs_errors, 95)),
            "max_abs_error_m": _safe(max(abs_errors)),
        })
    else:
        stats.update({k: None for k in (
            "mean_signed_error_m", "mean_abs_error_m", "rmse_m",
            "median_abs_error_m", "p95_abs_error_m", "max_abs_error_m",
        )})

    # Repeatability: ONLY within one cell (same baseline distance and
    # condition). Never on aggregation levels, never from absolute
    # errors (historic defect: abs-error stddev masquerading as
    # repeatability).
    if allow_repeatability and len(ok) >= MIN_SAMPLES:
        stats["repeatability_stddev_m"] = _safe(_stddev(signed))
        stats["repeatability_samples"] = len(ok)
    else:
        stats["repeatability_stddev_m"] = None
        stats["repeatability_samples"] = None
    stats["repeatability_scope"] = "cell" if allow_repeatability else None
    return stats


def _group(records, key_fn):
    groups: dict[str, list[Record]] = {}
    for m in records:
        groups.setdefault(key_fn(m), []).append(m)
    return groups


def cell_key(m: Record) -> str:
    return f"{m.mode}/{m.condition}/{m.distance_true_m:g}m"


def find_outliers(records: list[Record]) -> list[dict]:
    """Physically implausible values - listed, never removed."""
    out = []
    for m in records:
        if m.distance_measured_m is None:
            continue
        reasons = []
        if m.distance_measured_m <= OUTLIER_MIN_M:
            reasons.append(f"measured <= {OUTLIER_MIN_M} m")
        if m.distance_measured_m >= OUTLIER_MAX_M:
            reasons.append(f"measured >= {OUTLIER_MAX_M} m")
        if abs(m.distance_measured_m - m.distance_true_m) > OUTLIER_ERROR_M:
            reasons.append(f"|error| > {OUTLIER_ERROR_M} m")
        if reasons:
            out.append({
                "test_id": m.test_id, "mode": m.mode, "condition": m.condition,
                "distance_true_m": m.distance_true_m,
                "distance_measured_m": m.distance_measured_m,
                "reasons": reasons,
            })
    return out


# ------------------------------------------------------- matrix verification


def _parse_checklist(checklist_csv: str) -> dict:
    """Rebuild the DECLARED matrix from the checklist CSV itself.

    Declared counts are derived row-by-row (never trusted from prose):
    per mode, per cell (mode/condition/distance) and the grand total.
    """
    reader = csv.DictReader(io.StringIO(checklist_csv))
    if "test_id" not in (reader.fieldnames or []):
        raise CSVFormatError("checklist CSV missing 'test_id' column")
    declared_cells: dict[str, int] = {}
    declared_modes: dict[str, int] = {}
    ids: list[str] = []
    for row in reader:
        tid = (row.get("test_id") or "").strip()
        if not tid:
            continue
        ids.append(tid)
        mode = (row.get("mode") or "").strip().lower()
        condition = (row.get("condition") or "").strip().lower()
        d = _parse_float(row.get("distance_true_m"))
        key = f"{mode}/{condition}/{d:g}m" if d is not None else f"{mode}/{condition}/?"
        declared_cells[key] = declared_cells.get(key, 0) + 1
        declared_modes[mode] = declared_modes.get(mode, 0) + 1
    dup = len(ids) - len(set(ids))
    return {
        "ids": set(ids),
        "total": len(ids),
        "duplicate_ids_in_checklist": dup,
        "per_mode": declared_modes,
        "per_cell": declared_cells,
    }


def verify_matrix(records: list[Record], invalid_rows: list[InvalidRow],
                  checklist_csv: str | None) -> dict | None:
    """Declared-vs-actual matrix check (historic defect: 96 claimed vs
    68 actual). The declared matrix is DERIVED from checklist rows; the
    actual matrix is DERIVED from the recorded CSV rows; the two are
    compared per mode and per cell. Nothing is trusted from prose.
    """
    if checklist_csv is None:
        return None
    declared = _parse_checklist(checklist_csv)

    actual_cells: dict[str, int] = {}
    actual_modes: dict[str, int] = {}
    for m in records:
        key = cell_key(m)
        actual_cells[key] = actual_cells.get(key, 0) + 1
        actual_modes[m.mode] = actual_modes.get(m.mode, 0) + 1

    per_mode = {}
    for mode in sorted(set(declared["per_mode"]) | set(actual_modes)):
        d = declared["per_mode"].get(mode, 0)
        a = actual_modes.get(mode, 0)
        per_mode[mode] = {"declared": d, "actual": a, "diff": a - d, "match": d == a}

    per_cell_diffs = []
    for key in sorted(set(declared["per_cell"]) | set(actual_cells)):
        d = declared["per_cell"].get(key, 0)
        a = actual_cells.get(key, 0)
        if d != a:
            per_cell_diffs.append({"cell": key, "declared": d, "actual": a, "diff": a - d})

    got_ids = {m.test_id for m in records}
    completion = {
        "rows_received": len(records) + len(invalid_rows),
        "checklist_total": declared["total"],
        "completed_ids": len(got_ids & declared["ids"]),
        "missing_count": len(declared["ids"] - got_ids),
        "missing_ids_preview": sorted(declared["ids"] - got_ids)[:50],
        "extra_ids": sorted(got_ids - declared["ids"]),
        "completion_pct": (round(100.0 * len(got_ids & declared["ids"]) / declared["total"], 1)
                           if declared["total"] else None),
    }

    match = (not per_cell_diffs
             and all(v["match"] for v in per_mode.values())
             and declared["total"] == len(records))
    return {
        "match": match,
        "declared_total": declared["total"],
        "actual_total": len(records),
        "invalid_rows_not_counted_in_matrix": len(invalid_rows),
        "duplicate_ids_in_checklist": declared["duplicate_ids_in_checklist"],
        "per_mode": per_mode,
        "per_cell_diffs": per_cell_diffs,
        "completion": completion,
    }


# ------------------------------------------------------------- analysis


def analyze_v2(records: list[Record], invalid_rows: list[InvalidRow],
               meta: dict, checklist_csv: str | None = None) -> dict:
    """Full v2 analysis. NO cross-mode error statistics anywhere:
    every error metric lives inside a per-mode group."""
    def mode_of(m):
        return m.mode

    by_mode = {k: stats_for_v2(v, allow_repeatability=False)
               for k, v in sorted(_group(records, mode_of).items())}

    # per mode x factor groups (repeatability never allowed here)
    by_mode_condition = {}
    for mode, recs in sorted(_group(records, mode_of).items()):
        for key, v in sorted(_group(recs, lambda m: m.condition).items()):
            by_mode_condition[f"{mode}/{key}"] = stats_for_v2(v, allow_repeatability=False)
    by_mode_material = {}
    for mode, recs in sorted(_group(records, mode_of).items()):
        for key, v in sorted(_group(recs, lambda m: m.material).items()):
            by_mode_material[f"{mode}/{key}"] = stats_for_v2(v, allow_repeatability=False)
    by_mode_lighting = {}
    for mode, recs in sorted(_group(records, mode_of).items()):
        for key, v in sorted(_group(recs, lambda m: m.lighting).items()):
            by_mode_lighting[f"{mode}/{key}"] = stats_for_v2(v, allow_repeatability=False)
    by_mode_pose = {}
    for mode, recs in sorted(_group(records, mode_of).items()):
        for key, v in sorted(_group(recs, lambda m: m.pose).items()):
            by_mode_pose[f"{mode}/{key}"] = stats_for_v2(v, allow_repeatability=False)
    by_mode_distance = {}
    for mode, recs in sorted(_group(records, mode_of).items()):
        for key, v in sorted(_group(recs, lambda m: f"{m.distance_true_m:g}m").items()):
            by_mode_distance[f"{mode}/{key}"] = stats_for_v2(v, allow_repeatability=False)

    by_cell = {k: stats_for_v2(v, allow_repeatability=True)
               for k, v in sorted(_group(records, cell_key).items())}

    cell_repeatability = [
        {
            "cell": k,
            "attempts": s["attempts"],
            "valid_samples": s["valid_samples"],
            "repeatability_stddev_m": s["repeatability_stddev_m"],
        }
        for k, s in by_cell.items()
    ]

    failures_total = sum(s["failures"] for s in by_mode.values())
    valid_total = sum(s["valid_samples"] for s in by_mode.values())
    failure_reasons: dict[str, int] = {}
    for s in by_mode.values():
        for r, n in s["failure_reasons"].items():
            failure_reasons[r] = failure_reasons.get(r, 0) + n

    outliers = find_outliers(records)

    return {
        "schema": SCHEMA,
        "data_label": meta["data_label"],
        "csv_format": meta["format"],
        "factors_derived_from_condition": meta["factors_derived_from_condition"],
        "accounting": {
            "rows_received": len(records) + len(invalid_rows),
            "valid_rows": len(records),
            "invalid_rows": len(invalid_rows),
            "attempts": len(records),
            "valid_samples_total": valid_total,
            "failures_total": failures_total,
            "failure_reasons": failure_reasons,
            "outliers_reported": len(outliers),
            "modes_present": sorted({m.mode for m in records}),
            "parser_warnings": len(meta["warnings"]),
        },
        "matrix_check": verify_matrix(records, invalid_rows, checklist_csv),
        "by_mode": by_mode,
        "by_mode_condition": by_mode_condition,
        "by_mode_material": by_mode_material,
        "by_mode_lighting": by_mode_lighting,
        "by_mode_pose": by_mode_pose,
        "by_mode_distance": by_mode_distance,
        "by_cell": by_cell,
        "cell_repeatability": cell_repeatability,
        "failure_rows": [
            {"test_id": m.test_id, "mode": m.mode, "condition": m.condition,
             "distance_true_m": m.distance_true_m, "failure": m.failure or "measurement_missing"}
            for m in records if m.failure != "" or m.distance_measured_m is None
        ],
        "outliers": outliers,
        "invalid_rows": [
            {"line": r.line, "reason": r.reason, "raw": r.raw} for r in invalid_rows
        ],
        "warnings": meta["warnings"],
    }


GROUP_LEVELS = [
    ("by_mode", "mode"),
    ("by_mode_condition", "mode/condition"),
    ("by_mode_material", "mode/material"),
    ("by_mode_lighting", "mode/lighting"),
    ("by_mode_pose", "mode/pose"),
    ("by_mode_distance", "mode/distance"),
    ("by_cell", "mode/condition/distance"),
]

GROUP_CSV_COLUMNS = [
    "level", "key", "attempts", "valid_samples", "failures", "success_rate",
    "failure_rate", "mean_signed_error_m", "mean_abs_error_m", "rmse_m",
    "median_abs_error_m", "p95_abs_error_m", "max_abs_error_m",
    "repeatability_stddev_m", "sufficient_samples",
]


def groups_to_csv(analysis: dict) -> str:
    """Long-format CSV: one row per group; every declared metric as a
    column. Nulls stay empty - a missing value is visible as empty."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(GROUP_CSV_COLUMNS)
    for level_key, level_name in GROUP_LEVELS:
        for key, s in analysis[level_key].items():
            writer.writerow([
                level_name, key,
                s["attempts"], s["valid_samples"], s["failures"],
                "" if s["success_rate"] is None else round(s["success_rate"], 6),
                "" if s["failure_rate"] is None else round(s["failure_rate"], 6),
                "" if s["mean_signed_error_m"] is None else round(s["mean_signed_error_m"], 9),
                "" if s["mean_abs_error_m"] is None else round(s["mean_abs_error_m"], 9),
                "" if s["rmse_m"] is None else round(s["rmse_m"], 9),
                "" if s["median_abs_error_m"] is None else round(s["median_abs_error_m"], 9),
                "" if s["p95_abs_error_m"] is None else round(s["p95_abs_error_m"], 9),
                "" if s["max_abs_error_m"] is None else round(s["max_abs_error_m"], 9),
                "" if s["repeatability_stddev_m"] is None else round(s["repeatability_stddev_m"], 9),
                s["sufficient_samples"],
            ])
    return out.getvalue()


def to_json_v2(analysis: dict) -> str:
    return json.dumps(analysis, indent=2, ensure_ascii=False, allow_nan=False)
