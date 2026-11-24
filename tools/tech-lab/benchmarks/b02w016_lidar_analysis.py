#!/usr/bin/env python3
"""B02-W016 - LiDAR measurement analysis CLI (v2 statistics).

Fixes three historic defects of the Wave-1/T04 toolchain:
1. declared shot counts are verified against actual recorded rows
   (matrix_check) when a checklist is supplied;
2. raycast / scene_depth / smoothed_depth are strictly separated -
   no cross-mode error statistics are ever emitted;
3. repeatability_stddev_m is only computed per cell (same baseline
   distance + condition), from SIGNED errors of repeats - never the
   stddev of absolute errors, never across distances.

Usage:
    python tools/tech-lab/benchmarks/b02w016_lidar_analysis.py <experiment.csv> \
        [--checklist path.csv] [--out-dir DIR] [--no-charts]

Outputs (into --out-dir, default: alongside the input):
    <stem>-stats.json    full v2 analysis (strict JSON, no NaN)
    <stem>-groups.csv    long-format table: one row per group
    <stem>-report.md     human-readable Markdown report
    <stem>-fig*.png      four figures (matplotlib; --no-charts skips)

Exit codes: 0 analyzed, 1 format error, 2 usage error.
Synthetic batches (DESKTOP_SIMULATION / SYNTHETIC markers) are labeled
in every output; the report states real-device status DEVICE_PENDING.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from techlab.lidar_stats_v2 import (  # noqa: E402
    CSVFormatError,
    analyze_v2,
    groups_to_csv,
    parse_csv_v2,
    to_json_v2,
)
from techlab.lidar_report_v2 import to_markdown_v2  # noqa: E402
from techlab import lidar_charts_v2  # noqa: E402

DEFAULT_CHECKLIST = _REPO_ROOT / "docs" / "tech-lab" / "experiments" / "b02-w016-checklist.csv"


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1].startswith("--"):
        print(__doc__)
        return 2

    csv_path = Path(argv[1])
    if not csv_path.exists():
        print(f"FAIL: input CSV not found: {csv_path}")
        return 1

    args = argv[2:]
    checklist_csv = None
    if "--checklist" in args:
        cpath = Path(args[args.index("--checklist") + 1])
        checklist_csv = cpath.read_text(encoding="utf-8-sig")
    elif DEFAULT_CHECKLIST.exists():
        checklist_csv = DEFAULT_CHECKLIST.read_text(encoding="utf-8-sig")

    out_dir = csv_path.parent
    if "--out-dir" in args:
        out_dir = Path(args[args.index("--out-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)

    csv_text = csv_path.read_text(encoding="utf-8-sig")
    try:
        records, invalid_rows, meta = parse_csv_v2(csv_text)
    except CSVFormatError as e:
        print(f"FAIL: {e}")
        return 1

    analysis = analyze_v2(records, invalid_rows, meta, checklist_csv)

    stem = csv_path.stem
    json_path = out_dir / f"{stem}-stats.json"
    json_path.write_text(to_json_v2(analysis), encoding="utf-8")
    groups_path = out_dir / f"{stem}-groups.csv"
    groups_path.write_text(groups_to_csv(analysis), encoding="utf-8")
    md_path = out_dir / f"{stem}-report.md"
    md_path.write_text(to_markdown_v2(analysis), encoding="utf-8")

    chart_paths: list[Path] = []
    if "--no-charts" not in args:
        chart_paths = lidar_charts_v2.render_charts(
            records, analysis, out_dir, prefix=f"{stem}-")
        if not chart_paths and not lidar_charts_v2.charts_available():
            print("charts unavailable: matplotlib not installed", file=sys.stderr)

    print(f"analysis: {len(records)} records, {len(invalid_rows)} invalid rows")
    print(f"data label: {analysis['data_label']}")
    if analysis["matrix_check"] is not None:
        mx = analysis["matrix_check"]
        print(f"matrix check: declared {mx['declared_total']} vs "
              f"actual {mx['actual_total']} -> "
              f"{'MATCH' if mx['match'] else 'MISMATCH'}")
    print(f"JSON:    {json_path}")
    print(f"CSV:     {groups_path}")
    print(f"report:  {md_path}")
    for p in chart_paths:
        print(f"figure:  {p}")

    if analysis["matrix_check"] is not None and not analysis["matrix_check"]["match"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
