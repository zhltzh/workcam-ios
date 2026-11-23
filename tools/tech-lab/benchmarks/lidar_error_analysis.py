#!/usr/bin/env python3
"""T04 / W-02 - LiDAR measurement CSV analyzer (CLI wrapper).

Delegates statistics to backend/techlab/lidar_stats.py. Accepts BOTH:
- Mac LidarLab CSV (PR #9, 20 columns incl. test_id/mode/failure)
- legacy Wave-1 4-column CSV

Usage:
    python tools/tech-lab/benchmarks/lidar_error_analysis.py <input.csv> [--json out.json]

Groups by mode / condition / true distance / cell; reports success and
failure rates, MAE, RMSE, median, P95, signed-error bias, repeatability,
and failure-reason distributions. Cells with fewer than 3 successes emit
null (never NaN). Failure rows are preserved verbatim.

generate_sample_dataset() emits SYNTHETIC data for self-tests only -
never a real-device accuracy report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from techlab.lidar_stats import (  # noqa: E402
    MAC_COLUMNS,
    analyze,
    parse_csv,
    to_json,
)


def generate_sample_dataset() -> str:
    """SYNTHETIC dataset in the Mac 20-column format. Clearly labeled:
    batch_id carries SYNTHETIC so no report can mistake it for device
    data. Same scenario mix as the Wave-1 sample (normal ~1 cm noise /
    low_light bigger noise + some failures / glass mostly failures)."""
    rows = [",".join(MAC_COLUMNS)]
    tid = 0
    for true_m in (0.5, 1.0, 2.0, 3.0, 5.0):
        for rep in range(4):
            tid += 1
            meas = true_m + 0.01 * (((rep % 2) * 2) - 1)
            rows.append(
                f"SYNTHETIC-batch,T04-raycast-normal-{true_m:g}m-R{rep+1},"
                f"SYNTHETIC-DEVICE,99.9,true,0.9,0.9,normal-tracking,raycast,normal,"
                f"2026-11-20T10:00:00+00:00,{true_m},{meas:.3f},,,"
                f"0.05,,{true_m},,"
            )
    for i, true_m in enumerate((1.0, 2.0, 3.0, 5.0)):
        for rep in range(4):
            tid += 1
            if rep == 3 and true_m >= 3.0:
                # failure row: measured empty, abs/rel empty, elapsed,
                # failure reason, distance_m, two trailing empties (20 cols)
                rows.append(
                    f"SYNTHETIC-batch,T04-raycast-low_light-{true_m:g}m-R{rep+1},"
                    f"SYNTHETIC-DEVICE,99.9,true,0.9,0.9,normal-tracking,raycast,low_light,"
                    f"2026-11-20T10:00:00+00:00,{true_m},,,,"
                    f"0.05,depth_missing,{true_m},,"
                )
            else:
                meas = true_m + 0.03 * ((rep % 3) - 1)
                rows.append(
                    f"SYNTHETIC-batch,T04-raycast-low_light-{true_m:g}m-R{rep+1},"
                    f"SYNTHETIC-DEVICE,99.9,true,0.9,0.9,normal-tracking,raycast,low_light,"
                    f"2026-11-20T10:00:00+00:00,{true_m},{meas:.3f},,,"
                    f"0.05,,{true_m},,"
                )
    for true_m in (1.0, 3.0):   # aligned with t04-checklist glass rows (1/3 m)
        for rep in range(4):
            tid += 1
            if rep < 3:
                rows.append(
                    f"SYNTHETIC-batch,T04-raycast-glass-{true_m:g}m-R{rep+1},"
                    f"SYNTHETIC-DEVICE,99.9,true,0.9,0.9,normal-tracking,raycast,glass,"
                    f"2026-11-20T10:00:00+00:00,{true_m},,,,"
                    f"0.05,depth_missing,{true_m},,"
                )
            else:
                rows.append(
                    f"SYNTHETIC-batch,T04-raycast-glass-{true_m:g}m-R{rep+1},"
                    f"SYNTHETIC-DEVICE,99.9,true,0.9,0.9,normal-tracking,raycast,glass,"
                    f"2026-11-20T10:00:00+00:00,{true_m},{true_m + 0.02:.3f},,,"
                    f"0.05,,{true_m},,"
                )
    return "\n".join(rows) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    csv_text = Path(argv[1]).read_text(encoding="utf-8-sig")
    rows, meta = parse_csv(csv_text)
    analysis = analyze(rows)
    analysis["meta"] = {
        **meta,
        "data_origin": "raw CSV provided on the command line",
        "synthetic": "SYNTHETIC" in csv_text[:400],
    }
    print(to_json(analysis))
    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
        Path(out).write_text(to_json(analysis), encoding="utf-8")
        print(f"\nwritten to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
