#!/usr/bin/env python3
"""T04 (W-01): generate the LiDAR experiment checklist CSV.

Fixes the Wave-1 doc bug: the experiment plan CLAIMED 96 shots but the
table only added up to 68. This generator is now the single source of
truth; the CSV it emits IS the checklist Mac executes against.

Design decisions (also documented in t04-lidar-experiment.md):
- raycast is the field-primary path  -> FULL condition x distance matrix (68)
- scene_depth / smoothed_depth are cross-checks of the same sensor data
  -> representative subset (32 each): normal / low_light / glass /
  textureless at 1 m and 3 m. reflective is excluded: a mirror kills ToF
  physically, all three modes fail identically, and raycast already
  records that failure distribution - repeating it adds no information.
- repeats = 4 per cell for basic statistical power
- every row gets a unique test_id that maps 1:1 to the Mac CSV 'test_id'
  column (LidarLabView.swift header).

Usage:
    python tools/tech-lab/benchmarks/t04_checklist.py [out.csv]

SYNTHETIC note: the checklist describes PHYSICAL setups, not data; no
measurement values are fabricated here.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPEATS = 4

# raycast (primary): full matrix
RAYCAST_MATRIX: list[tuple[str, list[float]]] = [
    ("normal",      [0.5, 1.0, 2.0, 3.0, 5.0]),
    ("low_light",   [1.0, 2.0, 3.0, 5.0]),
    ("glass",       [1.0, 3.0]),
    ("oblique",     [1.0, 3.0]),
    ("textureless", [1.0, 3.0]),
    ("reflective",  [1.0, 3.0]),
]

# scene_depth / smoothed_depth (secondary): consistency subset
DEPTH_MODE_MATRIX: list[tuple[str, list[float]]] = [
    ("normal",      [1.0, 3.0]),
    ("low_light",   [1.0, 3.0]),
    ("glass",       [1.0, 3.0]),
    ("textureless", [1.0, 3.0]),
]

COLUMNS = ["test_id", "mode", "condition", "distance_true_m",
           "distance_m", "repeat", "phase"]


def build_rows() -> list[dict]:
    rows: list[dict] = []
    for condition, distances in RAYCAST_MATRIX:
        for dist in distances:
            for rep in range(1, REPEATS + 1):
                rows.append({
                    "test_id": f"T04-raycast-{condition}-{dist:g}m-R{rep}",
                    "mode": "raycast",
                    "condition": condition,
                    "distance_true_m": f"{dist:g}",
                    "distance_m": f"{dist:g}",
                    "repeat": rep,
                    "phase": "primary",
                })
    for mode in ("scene_depth", "smoothed_depth"):
        for condition, distances in DEPTH_MODE_MATRIX:
            for dist in distances:
                for rep in range(1, REPEATS + 1):
                    rows.append({
                        "test_id": f"T04-{mode}-{condition}-{dist:g}m-R{rep}",
                        "mode": mode,
                        "condition": condition,
                        "distance_true_m": f"{dist:g}",
                        "distance_m": f"{dist:g}",
                        "repeat": rep,
                        "phase": "secondary",
                    })
    return rows


def main(argv: list[str]) -> int:
    rows = build_rows()

    # sanity: unique IDs, honest totals (no padding, no duplicates)
    ids = [r["test_id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate test_id generated"
    total = len(rows)
    per_mode = {m: sum(1 for r in rows if r["mode"] == m) for m in
                ("raycast", "scene_depth", "smoothed_depth")}
    assert per_mode["raycast"] == 68, per_mode
    assert per_mode["scene_depth"] == 32, per_mode
    assert per_mode["smoothed_depth"] == 32, per_mode

    out = Path(argv[1]) if len(argv) > 1 else \
        Path(__file__).resolve().parents[3] / "docs" / "tech-lab" / "experiments" / "t04-checklist.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"checklist: {out}")
    print(f"total shots: {total} "
          f"(raycast {per_mode['raycast']} + scene_depth {per_mode['scene_depth']} "
          f"+ smoothed_depth {per_mode['smoothed_depth']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
