#!/usr/bin/env python3
"""B02-W016: generate the SYNTHETIC sample dataset (DESKTOP_SIMULATION).

Purpose: validate the v2 analyzer end-to-end on a full 132-shot
checklist-shaped dataset WITHOUT any real device. Every artifact from
this data is synthetic - the batch_id carries both
DESKTOP_SIMULATION and SYNTHETIC markers and the analyzer labels the
report accordingly. This data must NEVER be cited as iPhone LiDAR
performance. Real-device status: DEVICE_PENDING.

Injected edge cases (all countable in the analyzer output):
- failure rows with reasons (depth_missing / no_return / low_confidence)
- one unexplained missing measurement (no failure reason)
- three outliers (|error| > 1 m, measured out of physical range)
- one invalid row (empty distance_true_m)
- one duplicate test_id (re-measurement artifact)

Usage:
    python tools/tech-lab/validation/gen_b02w016_samples.py [out.csv]

Deterministic: fixed seed 20261125.
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from techlab.lidar_stats_v2 import CONDITION_FACTORS, MAC_COLUMNS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools" / "tech-lab" / "benchmarks"))
from b02w016_checklist import build_rows  # noqa: E402

SEED = 20261125
BATCH_ID = "B02W016-SYNTHETIC-DESKTOP-SIMULATION"
V2_HEADER = MAC_COLUMNS + ["material", "lighting", "pose"]

# per (mode, condition): (bias_m, sigma_m, failure_share, failure_reason)
NOISE_MODEL = {
    ("raycast", "normal"):      (-0.005, 0.008, 0.00, ""),
    ("raycast", "low_light"):   (0.020, 0.030, 0.10, "low_confidence"),
    ("raycast", "glass"):       (0.050, 0.040, 0.75, "depth_missing"),
    ("raycast", "oblique"):     (0.030, 0.020, 0.00, ""),
    ("raycast", "textureless"): (0.010, 0.015, 0.00, ""),
    ("raycast", "reflective"):  (0.150, 0.050, 0.00, ""),
    ("scene_depth", "normal"):      (-0.003, 0.006, 0.00, ""),
    ("scene_depth", "low_light"):   (0.015, 0.020, 0.05, "low_confidence"),
    ("scene_depth", "glass"):       (0.040, 0.030, 0.50, "depth_missing"),
    ("scene_depth", "textureless"): (0.008, 0.010, 0.00, ""),
    ("smoothed_depth", "normal"):      (-0.002, 0.004, 0.00, ""),
    ("smoothed_depth", "low_light"):   (0.010, 0.012, 0.05, "low_confidence"),
    ("smoothed_depth", "glass"):       (0.030, 0.020, 0.50, "depth_missing"),
    ("smoothed_depth", "textureless"): (0.006, 0.008, 0.00, ""),
}

SPECIAL_FAILURES = {
    # reflective: 7 of 8 rows physically fail (mirror kills ToF)
    "B02-W016-raycast-reflective-1m-R1": "no_return",
    "B02-W016-raycast-reflective-1m-R2": "no_return",
    "B02-W016-raycast-reflective-1m-R3": "no_return",
    "B02-W016-raycast-reflective-1m-R4": "no_return",
    "B02-W016-raycast-reflective-3m-R1": "no_return",
    "B02-W016-raycast-reflective-3m-R2": "no_return",
    "B02-W016-raycast-reflective-3m-R3": "no_return",
}
# forced implausible measurements (outliers - reported, kept in stats)
SPECIAL_MEASUREMENTS = {
    "B02-W016-raycast-low_light-5m-R1": 11.0,        # out of range + |err| > 1 m
    "B02-W016-raycast-normal-0.5m-R4": 0.03,         # below plausible minimum
    "B02-W016-scene_depth-normal-3m-R2": 4.31,       # |err| > 1 m
}
# unexplained missing: empty measurement, NO failure reason
UNEXPLAINED_MISSING_ID = "B02-W016-raycast-normal-5m-R2"


def _row(rng: random.Random, spec: dict, measured: float | None,
         failure: str) -> list[str]:
    material, lighting, pose = CONDITION_FACTORS[spec["condition"]]
    conf = round(rng.uniform(0.70, 0.95), 2) if failure == "" else round(rng.uniform(0.30, 0.6), 2)
    return [
        BATCH_ID, spec["test_id"], "iPhone SYN (desktop)", "17.0",
        "true" if failure == "" else "false", f"{conf}", f"{round(conf - 0.05, 2)}",
        "ok", spec["mode"], spec["condition"],
        f"2026-11-25T10:00:{rng.randint(10, 59)}+00:00",
        spec["distance_true_m"],
        "" if measured is None else f"{measured:.3f}",
        "", "", f"{rng.uniform(0.04, 0.06):.3f}",
        failure, spec["distance_m"], "",
        "", material, lighting, pose,
    ]


def generate() -> tuple[list[list[str]], dict]:
    rng = random.Random(SEED)
    checklist = build_rows()
    rows: list[list[str]] = [V2_HEADER]
    counts = {"success": 0, "failure_reasoned": 0, "unexplained_missing": 0,
              "outliers": 0, "invalid": 0, "duplicate_ids": 0}

    for spec in checklist:
        tid = spec["test_id"]
        true_m = float(spec["distance_true_m"])
        bias, sigma, fail_share, reason = NOISE_MODEL[(spec["mode"], spec["condition"])]
        failure = SPECIAL_FAILURES.get(tid, "")
        special = SPECIAL_MEASUREMENTS.get(tid)
        measured: float | None = None

        if tid == UNEXPLAINED_MISSING_ID:
            failure = ""                     # no reason - analyzer must flag it
            measured = None
            counts["unexplained_missing"] += 1
        elif failure:
            measured = None
            counts["failure_reasoned"] += 1
        elif special is not None:
            measured = float(special)
            counts["outliers"] += 1
        elif rng.random() < fail_share:
            failure = reason
            counts["failure_reasoned"] += 1
        else:
            measured = true_m + bias + rng.gauss(0, sigma)
            counts["success"] += 1
        rows.append(_row(rng, spec, measured, failure))

    # duplicate test_id: a re-measurement of the same checklist slot
    dup_spec = next(s for s in checklist
                    if s["test_id"] == "B02-W016-raycast-normal-1m-R1")
    rows.append(_row(rng, dup_spec, 1.006, ""))
    counts["duplicate_ids"] += 1

    # invalid row: missing distance_true_m (kept as invalid, never dropped)
    rows.append([
        BATCH_ID, "EX-B02W016-INVALID-1", "iPhone SYN (desktop)", "17.0",
        "true", "0.9", "0.85", "ok", "raycast", "normal",
        "2026-11-25T10:00:59+00:00",
        "", "", "", "", "0.050", "", "", "", "matte_white", "normal",
        "perpendicular",
    ])
    counts["invalid"] += 1

    return rows, counts


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else \
        REPO_ROOT / "tools" / "tech-lab" / "datasets" / "b02-w016-sim" / "sample-b02-w016-sim.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, counts = generate()
    with open(out, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    total_data_rows = len(rows) - 1
    print(f"sample: {out}")
    print(f"DESKTOP_SIMULATION / SYNTHETIC - {total_data_rows} data rows "
          f"({counts['success']} clean successes)")
    print(f"injected: {counts}")
    print("This data is SYNTHETIC and must never be cited as device results.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
