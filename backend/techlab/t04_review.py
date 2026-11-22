"""W-03 / T04 - Mac LiDAR CSV batch review core (tech-lab).

Acceptance review of a Mac-exported experiment CSV:
- format & missing-field check (Mac 20-col or legacy 4-col)
- duplicate test_id check
- mode sanity check
- outlier check (implausible measurements - REPORTED, never hidden)
- completion vs the t04-checklist.csv (132 shots; partial data is fine)
- error statistics (delegates to lidar_stats)
- failure-reason summary

HONESTY RULES:
- Partial CSV is fully analyzed; completion is reported as a PERCENTAGE.
- Outliers are listed, not deleted.
- SYNTHETIC batches are detected and labeled in the report.
- This tool NEVER fabricates device results: it only reviews the CSV it
  is given.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from techlab.lidar_stats import (
    CSVFormatError,
    analyze,
    parse_csv,
)

DEFAULT_CHECKLIST = (
    Path(__file__).resolve().parents[2] / "docs" / "tech-lab" / "experiments" / "t04-checklist.csv"
)

# Outlier bounds (reported, not removed): LiDAR working range ~0.25-5 m;
# anything <=0.05 m or >=10 m, or |error|>1 m, is physically suspect.
OUTLIER_MIN_M = 0.05
OUTLIER_MAX_M = 10.0
OUTLIER_ERROR_M = 1.0


def _load_checklist_ids(checklist_csv: str | None) -> tuple[set[str] | None, int]:
    """Returns (expected test_ids or None, total). None = no checklist."""
    if checklist_csv is None:
        return None, 0
    reader = csv.DictReader(io.StringIO(checklist_csv))
    if "test_id" not in (reader.fieldnames or []):
        raise CSVFormatError("checklist CSV missing 'test_id' column")
    ids = [row["test_id"].strip() for row in reader if row.get("test_id")]
    return set(ids), len(ids)


def review_csv(csv_text: str, checklist_csv: str | None) -> dict:
    """Full acceptance review. Always returns a structured dict; format
    errors produce {'format_ok': False, ...} rather than raising."""
    review: dict = {
        "format_ok": True,
        "format_error": None,
        "synthetic": "SYNTHETIC" in csv_text[:400],
        "warnings": [],
        "duplicate_test_ids": [],
        "unknown_modes": [],
        "outliers": [],
        "completion": {},
        "failure_summary": {},
        "stats": {},
    }

    try:
        rows, meta = parse_csv(csv_text)
        review["csv_format"] = meta["format"]
        review["warnings"] = meta["warnings"]
    except CSVFormatError as e:
        review["format_ok"] = False
        review["format_error"] = str(e)
        return review

    # duplicates & unknown modes (collected from parser warnings, structured)
    for w in review["warnings"]:
        if "DUPLICATE test_id" in w:
            tid = w.split("'")[1] if "'" in w else w
            review["duplicate_test_ids"].append(tid)
        if "unknown mode" in w:
            review["unknown_modes"].append(w)

    # outliers: physically implausible values - listed, never deleted
    for m in rows:
        if m.distance_measured_m is None:
            continue
        reasons = []
        if m.distance_measured_m <= OUTLIER_MIN_M:
            reasons.append(f"measured<= {OUTLIER_MIN_M} m")
        if m.distance_measured_m >= OUTLIER_MAX_M:
            reasons.append(f"measured >= {OUTLIER_MAX_M} m")
        if abs(m.distance_measured_m - m.distance_true_m) > OUTLIER_ERROR_M:
            reasons.append("|error| > 1 m")
        if reasons:
            review["outliers"].append({
                "test_id": m.test_id, "mode": m.mode, "condition": m.condition,
                "distance_true_m": m.distance_true_m,
                "distance_measured_m": m.distance_measured_m,
                "reasons": reasons,
            })

    # completion vs checklist (partial data fully supported)
    expected, total_expected = _load_checklist_ids(checklist_csv)
    got_ids = {m.test_id for m in rows}
    review["completion"] = {
        "rows_received": len(rows),
        "checklist_total": total_expected if expected else None,
        "completed_ids": len(got_ids & expected) if expected else None,
        "missing_ids": sorted(expected - got_ids)[:50] if expected else None,
        "missing_count": len(expected - got_ids) if expected else None,
        "extra_ids": sorted(got_ids - expected)[:50] if expected else None,
        "completion_pct": (round(100.0 * len(got_ids & expected) / total_expected, 1)
                           if expected else None),
    }

    # statistics + failure summary (partial or complete alike)
    stats = analyze(rows)
    review["stats"] = stats
    review["failure_summary"] = stats["overall"]["failure_reasons"]

    return review


def to_json_str(review: dict) -> str:
    return json.dumps(review, indent=2, ensure_ascii=False, allow_nan=False)


def to_markdown(review: dict) -> str:
    """Human-readable acceptance report."""
    lines: list[str] = []
    origin = "SYNTHETIC (self-test data - NOT device results)" if review.get("synthetic") \
        else "Mac-exported experiment CSV (origin not verified by this tool)"
    lines.append("# T04 Experiment Batch Review")
    lines.append("")
    lines.append(f"Data origin: **{origin}**")
    lines.append("")

    if not review.get("format_ok"):
        lines.append(f"**FORMAT ERROR** - cannot review: {review.get('format_error')}")
        return "\n".join(lines) + "\n"

    lines.append(f"CSV format: {review.get('csv_format')}  |  "
                 f"rows received: {review['completion']['rows_received']}")
    lines.append("")

    comp = review["completion"]
    if comp.get("checklist_total"):
        lines.append("## Completion")
        lines.append("")
        lines.append(f"- checklist total: **{comp['checklist_total']}**")
        lines.append(f"- completed unique test IDs: **{comp['completed_ids']}** "
                     f"({comp['completion_pct']} %)")
        lines.append(f"- missing: {comp['missing_count']}")
        if comp.get("missing_ids"):
            shown = ", ".join(comp["missing_ids"][:10])
            lines.append(f"  - first missing: {shown}")
        if comp.get("extra_ids"):
            lines.append(f"- unexpected IDs (not in checklist): "
                         f"{len(comp['extra_ids'])} - e.g. {', '.join(comp['extra_ids'][:5])}")
        lines.append("")

    lines.append("## Data quality")
    n_warn = len(review.get("warnings") or [])
    lines.append(f"- parser warnings: {n_warn}")
    if review.get("duplicate_test_ids"):
        lines.append(f"- **DUPLICATE test_ids: {len(review['duplicate_test_ids'])}** "
                     f"(e.g. {', '.join(review['duplicate_test_ids'][:5])})")
    if review.get("unknown_modes"):
        lines.append(f"- unknown modes: {len(review['unknown_modes'])} warning(s)")
    n_out = len(review.get("outliers") or [])
    lines.append(f"- outliers (reported, kept): {n_out}")
    for o in review.get("outliers", [])[:5]:
        lines.append(f"  - {o['test_id']}: {', '.join(o['reasons'])}")
    lines.append("")

    lines.append("## Error statistics (overall)")
    overall = review["stats"]["overall"]
    if overall["sufficient_samples"]:
        lines.append(f"- attempts {overall['attempts']}, successes {overall['successes']}, "
                     f"failures {overall['failures']} "
                     f"(failure rate {overall['failure_rate']})")
        lines.append(f"- mean abs error: {overall['mean_abs_error_m']} m | "
                     f"median: {overall['median_abs_error_m']} m | "
                     f"RMSE: {overall['rmse_m']} m | P95: {overall['p95_abs_error_m']} m")
        lines.append(f"- mean signed error: {overall['mean_signed_error_m']} m | "
                     f"repeatability stddev: {overall['repeatability_stddev_m']} m")
    else:
        lines.append(f"- attempts {overall['attempts']}, failures {overall['failures']} - "
                     "insufficient successes for statistics (nulls reported honestly)")
    lines.append("")

    lines.append("## Failure reasons")
    if review["failure_summary"]:
        for reason, count in sorted(review["failure_summary"].items(),
                                    key=lambda kv: -kv[1]):
            lines.append(f"- {reason}: {count}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Verdict")
    problems = []
    if review.get("duplicate_test_ids"):
        problems.append("duplicate test IDs must be re-measured")
    if comp.get("checklist_total") and comp.get("missing_count"):
        problems.append(f"{comp['missing_count']} checklist shots still missing")
    if review.get("outliers"):
        problems.append(f"{len(review['outliers'])} outliers need investigation")
    if problems:
        lines.append("INCOMPLETE / NEEDS ATTENTION: " + "; ".join(problems))
    else:
        lines.append("Data quality checks passed; statistics above are ready for review.")
    lines.append("")
    lines.append("_Reminder: statistics describe the submitted CSV only; "
                 "final DEVICE_VERIFIED status requires the full checklist "
                 "on real hardware._")
    return "\n".join(lines) + "\n"
