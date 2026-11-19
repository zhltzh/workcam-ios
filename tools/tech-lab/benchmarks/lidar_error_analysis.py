"""T04 - LiDAR distance-measurement CSV analysis (tech-lab benchmark tool).

Consumes raw CSV exported from the iPhone LiDAR experiment
(docs/tech-lab/experiments/t04-lidar-experiment.md) and produces the
error statistics required by the acceptance standard:

    absolute error, relative error, repeatability (stddev), failure rate

Input CSV columns (one row per measurement):
    distance_true_m   ground truth from the calibration setup
    distance_measured_m  LiDAR/ARKit reported value (empty on failure)
    condition         e.g. normal / low_light / glass / oblique / textureless
    distance_m        measured distance of the device from target (0.5-5 m)

"Failure" = the experiment produced no reading for that attempt; failures
are counted in the failure rate and are NEVER silently dropped.

Usage:
    python tools/tech-lab/benchmarks/lidar_error_analysis.py <input.csv> [--json out.json]

The module ships with `generate_sample_dataset()` used by the automated
tests; sample data is synthetic and clearly labeled as such.
"""

from __future__ import annotations

import csv
import io
import json
import math
import sys
from dataclasses import dataclass, field


REQUIRED_COLUMNS = ("distance_true_m", "distance_measured_m", "condition", "distance_m")


@dataclass
class ConditionStats:
    condition: str
    n_total: int = 0
    n_failed: int = 0
    errors_abs: list[float] = field(default_factory=list)
    rel_errors: list[float] = field(default_factory=list)

    @property
    def failure_rate(self) -> float:
        return self.n_failed / self.n_total if self.n_total else 0.0

    @property
    def mean_abs_error_m(self) -> float:
        return sum(self.errors_abs) / len(self.errors_abs) if self.errors_abs else float("nan")

    @property
    def max_abs_error_m(self) -> float:
        return max(self.errors_abs) if self.errors_abs else float("nan")

    @property
    def repeatability_m(self) -> float:
        """Stddev of measured values at the same ground truth = repeatability."""
        if len(self.errors_abs) < 2:
            return float("nan")
        mean = sum(self.errors_abs) / len(self.errors_abs)
        var = sum((e - mean) ** 2 for e in self.errors_abs) / (len(self.errors_abs) - 1)
        return math.sqrt(var)

    def to_dict(self) -> dict:
        return {
            "condition": self.condition,
            "attempts": self.n_total,
            "failures": self.n_failed,
            "failure_rate": round(self.failure_rate, 4),
            "mean_abs_error_m": round(self.mean_abs_error_m, 4),
            "max_abs_error_m": round(self.max_abs_error_m, 4),
            "mean_rel_error": round(
                sum(self.rel_errors) / len(self.rel_errors), 4
            ) if self.rel_errors else None,
            "repeatability_m": round(self.repeatability_m, 4),
        }


def analyze_csv(csv_text: str) -> dict:
    """Parse the experiment CSV and compute per-condition statistics."""
    reader = csv.DictReader(io.StringIO(csv_text))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")

    stats: dict[str, ConditionStats] = {}
    for row in reader:
        cond = row["condition"] or "unspecified"
        cs = stats.setdefault(cond, ConditionStats(condition=cond))
        cs.n_total += 1
        measured = (row.get("distance_measured_m") or "").strip()
        if measured == "":
            cs.n_failed += 1
            continue
        true_m = float(row["distance_true_m"])
        meas_m = float(measured)
        abs_err = abs(meas_m - true_m)
        cs.errors_abs.append(abs_err)
        cs.rel_errors.append(abs_err / true_m if true_m > 0 else float("nan"))

    overall_attempts = sum(s.n_total for s in stats.values())
    overall_failures = sum(s.n_failed for s in stats.values())
    all_errors = [e for s in stats.values() for e in s.errors_abs]

    return {
        "overall": {
            "attempts": overall_attempts,
            "failures": overall_failures,
            "failure_rate": round(overall_failures / overall_attempts, 4) if overall_attempts else 0.0,
            "mean_abs_error_m": round(sum(all_errors) / len(all_errors), 4) if all_errors else None,
        },
        "conditions": [stats[k].to_dict() for k in sorted(stats)],
        "notes": [
            "failures are counted, never dropped",
            "sample data from generate_sample_dataset() is SYNTHETIC (tests only)",
        ],
    }


def generate_sample_dataset() -> str:
    """Synthetic dataset for the tool's own tests. NOT real hardware data."""
    rows = [",".join(REQUIRED_COLUMNS)]
    # normal condition: 1-2 cm noise, no failures
    for true_m in (0.5, 1.0, 2.0, 3.0, 5.0):
        for _ in range(4):
            meas = true_m + 0.01 * (((_ % 2) * 2) - 1)
            rows.append(f"{true_m},{meas:.3f},normal,{true_m}")
    # low_light: bigger noise, occasional failure (empty measured)
    for i, true_m in enumerate((1.0, 2.0, 3.0, 5.0)):
        for j in range(4):
            if j == 3 and true_m >= 3.0:
                rows.append(f"{true_m},,low_light,{true_m}")
            else:
                meas = true_m + 0.03 * ((j % 3) - 1)
                rows.append(f"{true_m},{meas:.3f},low_light,{true_m}")
    # glass: mostly failures
    for true_m in (1.0, 2.0):
        for j in range(4):
            if j < 3:
                rows.append(f"{true_m},,glass,{true_m}")
            else:
                rows.append(f"{true_m},{true_m + 0.02:.3f},glass,{true_m}")
    return "\n".join(rows) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        result = analyze_csv(f.read())
    print(json.dumps(result, indent=2))
    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"\nwritten to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
