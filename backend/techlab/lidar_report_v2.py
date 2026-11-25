"""B02-W016 - Markdown report renderer for the v2 LiDAR analysis."""

from __future__ import annotations


def _fmt(v, unit: str = " m") -> str:
    return "n/a" if v is None else f"{v:.4f}{unit}"


def _pct(v) -> str:
    return "n/a" if v is None else f"{100 * v:.1f}%"


def _stats_table_line(key: str, s: dict) -> str:
    return (f"| {key} | {s['attempts']} | {s['valid_samples']} | {s['failures']} | "
            f"{_pct(s['success_rate'])} | {_fmt(s['mean_signed_error_m'])} | "
            f"{_fmt(s['mean_abs_error_m'])} | {_fmt(s['rmse_m'])} | "
            f"{_fmt(s['median_abs_error_m'])} | {_fmt(s['p95_abs_error_m'])} | "
            f"{_fmt(s['max_abs_error_m'])} |")


_TABLE_HEADER = ("| group | attempts | valid | failures | success | "
                 "mean signed | MAE | RMSE | median | P95 | max |"
                 "\n|---|---|---|---|---|---|---|---|---|---|---|")


def to_markdown_v2(analysis: dict) -> str:
    lines: list[str] = []
    label = analysis.get("data_label", "UNKNOWN")
    synthetic = label == "DESKTOP_SIMULATION"
    lines.append("# B02-W016 LiDAR Measurement Analysis Report")
    lines.append("")
    if synthetic:
        lines.append("**DATA ORIGIN: DESKTOP_SIMULATION** - synthetic data generated "
                     "to validate the analyzer. **NOT real iPhone measurement results.** "
                     "Real-device status: **DEVICE_PENDING**.")
    else:
        lines.append(f"Data origin: **{label}** (this tool does not verify the "
                     "provenance of the submitted CSV).")
    lines.append("")

    acc = analysis["accounting"]
    lines.append("## Row accounting")
    lines.append("")
    lines.append(f"- rows received: **{acc['rows_received']}** "
                 f"(valid {acc['valid_rows']} + invalid {acc['invalid_rows']})")
    lines.append(f"- valid samples: {acc['valid_samples_total']} | "
                 f"failures: {acc['failures_total']} | "
                 f"outliers reported (kept in stats): {acc['outliers_reported']}")
    lines.append(f"- modes present: {', '.join(acc['modes_present'])}")
    lines.append(f"- parser warnings: {acc['parser_warnings']}")
    lines.append("")

    mx = analysis.get("matrix_check")
    if mx is not None:
        lines.append("## Matrix verification (declared vs actual)")
        lines.append("")
        lines.append(f"- checklist declared total: **{mx['declared_total']}** | "
                     f"actual recorded rows: **{mx['actual_total']}** | "
                     f"match: **{'YES' if mx['match'] else 'NO'}**")
        lines.append(f"- completion by test_id: {mx['completion']['completed_ids']} / "
                     f"{mx['completion']['checklist_total']} "
                     f"({mx['completion']['completion_pct']} %) | "
                     f"missing: {mx['completion']['missing_count']} | "
                     f"unexpected ids: {len(mx['completion']['extra_ids'])}")
        lines.append("")
        lines.append("| mode | declared | actual | match |")
        lines.append("|---|---|---|---|")
        for mode, v in mx["per_mode"].items():
            lines.append(f"| {mode} | {v['declared']} | {v['actual']} | "
                         f"{'OK' if v['match'] else 'MISMATCH'} |")
        lines.append("")
        if mx["per_cell_diffs"]:
            lines.append(f"**{len(mx['per_cell_diffs'])} cell-level differences:**")
            lines.append("")
            lines.append("| cell | declared | actual | diff |")
            lines.append("|---|---|---|---|")
            for d in mx["per_cell_diffs"][:30]:
                lines.append(f"| {d['cell']} | {d['declared']} | {d['actual']} | "
                             f"{d['diff']:+d} |")
            lines.append("")
        elif mx["match"]:
            lines.append("All declared cells are backed by actual records - "
                         "declared counts are derivable from the recorded rows.")
            lines.append("")

    lines.append("## Per-mode statistics (modes strictly separated)")
    lines.append("")
    lines.append("No cross-mode error statistics exist in this report by design: "
                 "raycast / scene_depth / smoothed_depth are never merged.")
    lines.append("")
    lines.append(_TABLE_HEADER)
    for mode, s in analysis["by_mode"].items():
        lines.append(_stats_table_line(mode, s))
    lines.append("")

    for level, title in (
        ("by_mode_condition", "by condition"),
        ("by_mode_material", "by material"),
        ("by_mode_lighting", "by lighting"),
        ("by_mode_distance", "by baseline distance"),
        ("by_mode_pose", "by pose"),
    ):
        data = analysis[level]
        if not data:
            continue
        lines.append(f"### Grouped {title}")
        lines.append("")
        lines.append(_TABLE_HEADER)
        for key, s in data.items():
            lines.append(_stats_table_line(key, s))
        lines.append("")

    lines.append("## Repeatability (cell level only)")
    lines.append("")
    lines.append("Repeatability = sample stddev of SIGNED errors of repeats at the "
                 "**same baseline distance and condition** (cell = mode + condition "
                 "+ distance). It is null at every aggregation level above; the "
                 "stddev of absolute errors is never used as repeatability.")
    lines.append("")
    lines.append("| cell | attempts | valid | repeatability stddev |")
    lines.append("|---|---|---|---|")
    for c in analysis["cell_repeatability"]:
        lines.append(f"| {c['cell']} | {c['attempts']} | {c['valid_samples']} | "
                     f"{_fmt(c['repeatability_stddev_m'])} |")
    lines.append("")

    lines.append("## Preserved records (never silently dropped)")
    lines.append("")
    fr = analysis["failure_rows"]
    lines.append(f"- failure rows: **{len(fr)}**")
    for r in fr[:10]:
        lines.append(f"  - {r['test_id']} ({r['mode']}/{r['condition']}"
                     f"@{r['distance_true_m']:g}m): {r['failure']}")
    if len(fr) > 10:
        lines.append(f"  - ... and {len(fr) - 10} more (see JSON)")
    outs = analysis["outliers"]
    lines.append(f"- outliers (kept in statistics, reported for investigation): **{len(outs)}**")
    for o in outs[:10]:
        lines.append(f"  - {o['test_id']}: {', '.join(o['reasons'])} "
                     f"(measured {o['distance_measured_m']} m)")
    inv = analysis["invalid_rows"]
    lines.append(f"- invalid rows (missing/invalid distance_true_m): **{len(inv)}**")
    for r in inv[:10]:
        lines.append(f"  - line {r['line']}: {r['reason']}")
    lines.append("")

    if analysis["warnings"]:
        lines.append("## Parser warnings")
        lines.append("")
        for w in analysis["warnings"][:30]:
            lines.append(f"- {w}")
        if len(analysis["warnings"]) > 30:
            lines.append(f"- ... and {len(analysis['warnings']) - 30} more")
        lines.append("")

    lines.append("## Verdict")
    lines.append("")
    problems = []
    if mx is not None and not mx["match"]:
        problems.append("declared matrix does NOT match actual records "
                         f"({mx['declared_total']} declared vs {mx['actual_total']} actual)")
    if mx is not None and mx["completion"]["missing_count"]:
        problems.append(f"{mx['completion']['missing_count']} checklist shots still missing")
    if any("DUPLICATE test_id" in w for w in analysis["warnings"]):
        problems.append("duplicate test_ids present - affected shots must be re-measured")
    if inv:
        problems.append(f"{len(inv)} invalid rows need re-export from Mac")
    if problems:
        lines.append("INCOMPLETE / NEEDS ATTENTION: " + "; ".join(problems))
    else:
        lines.append("Matrix and data-quality checks passed; statistics above are "
                     "ready for review.")
    lines.append("")
    if synthetic:
        lines.append("_Reminder: this report describes SYNTHETIC validation data. "
                     "It validates the analyzer only and must never be cited as "
                     "iPhone LiDAR performance. Device data remains DEVICE_PENDING._")
    else:
        lines.append("_Reminder: DEVICE_VERIFIED status requires the full checklist "
                     "on real hardware; this report only describes the submitted CSV._")
    return "\n".join(lines) + "\n"
