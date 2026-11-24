#!/usr/bin/env python3
"""B02-W016: generate the B02-W016 LiDAR experiment checklist CSV.

Successor of t04_checklist.py. The physical matrix is identical to T04
(132 shots: raycast 68 + scene_depth 32 + smoothed_depth 32) because the
Mac LidarLabView implementation and the physical setups are unchanged;
what changes is the bookkeeping:

1. test_id prefix is B02-W016- and maps 1:1 to the Mac CSV 'test_id'.
2. Every row carries explicit material / lighting / pose factors so the
   Windows analyzer can group by material and lighting independently of
   the combined 'condition' string.
3. DECLARED MATRIX SELF-CHECK: the declared matrix below is summed and
   compared to the generated row count on every run - the generator
   REFUSES to emit a checklist whose declared counts do not equal its
   actual rows (historic defect 355edf9: 96 shots claimed, 68 actual).

Usage:
    python tools/tech-lab/benchmarks/b02w016_checklist.py [out.csv]

This checklist describes PHYSICAL setups, not data; no measurement
values are fabricated here.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from techlab.lidar_stats_v2 import CONDITION_FACTORS  # noqa: E402

REPEATS = 4
TASK_PREFIX = "B02-W016"

# raycast (primary): full matrix  -> declared 68
RAYCAST_MATRIX: list[tuple[str, list[float]]] = [
    ("normal",      [0.5, 1.0, 2.0, 3.0, 5.0]),
    ("low_light",   [1.0, 2.0, 3.0, 5.0]),
    ("glass",       [1.0, 3.0]),
    ("oblique",     [1.0, 3.0]),
    ("textureless", [1.0, 3.0]),
    ("reflective",  [1.0, 3.0]),
]

# scene_depth / smoothed_depth (secondary): consistency subset -> 32 each
DEPTH_MODE_MATRIX: list[tuple[str, list[float]]] = [
    ("normal",      [1.0, 3.0]),
    ("low_light",   [1.0, 3.0]),
    ("glass",       [1.0, 3.0]),
    ("textureless", [1.0, 3.0]),
]

COLUMNS = ["test_id", "mode", "condition", "material", "lighting", "pose",
           "distance_true_m", "distance_m", "repeat", "phase"]

DECLARED_MATRIX = {
    "raycast": {(cond, d): REPEATS
                for cond, dists in RAYCAST_MATRIX for d in dists},
    "scene_depth": {(cond, d): REPEATS
                    for cond, dists in DEPTH_MODE_MATRIX for d in dists},
    "smoothed_depth": {(cond, d): REPEATS
                       for cond, dists in DEPTH_MODE_MATRIX for d in dists},
}
DECLARED_TOTAL = sum(sum(c.values()) for c in DECLARED_MATRIX.values())


def build_rows() -> list[dict]:
    rows: list[dict] = []

    def emit(mode: str, condition: str, dist: float, rep: int, phase: str) -> None:
        material, lighting, pose = CONDITION_FACTORS[condition]
        rows.append({
            "test_id": f"{TASK_PREFIX}-{mode}-{condition}-{dist:g}m-R{rep}",
            "mode": mode,
            "condition": condition,
            "material": material,
            "lighting": lighting,
            "pose": pose,
            "distance_true_m": f"{dist:g}",
            "distance_m": f"{dist:g}",
            "repeat": rep,
            "phase": phase,
        })

    for condition, distances in RAYCAST_MATRIX:
        for dist in distances:
            for rep in range(1, REPEATS + 1):
                emit("raycast", condition, dist, rep, "primary")
    for mode in ("scene_depth", "smoothed_depth"):
        for condition, distances in DEPTH_MODE_MATRIX:
            for dist in distances:
                for rep in range(1, REPEATS + 1):
                    emit(mode, condition, dist, rep, "secondary")
    return rows


def self_check(rows: list[dict]) -> dict:
    """Declared-vs-actual verification of the checklist itself.

    The declared counts must be DERIVABLE from the emitted rows: per
    mode, per cell and grand total. Any mismatch aborts the generator.
    """
    ids = [r["test_id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate test_id generated"

    actual_per_mode: dict[str, int] = {}
    actual_cells: dict[tuple[str, str, float], int] = {}
    for r in rows:
        actual_per_mode[r["mode"]] = actual_per_mode.get(r["mode"], 0) + 1
        key = (r["mode"], r["condition"], float(r["distance_true_m"]))
        actual_cells[key] = actual_cells.get(key, 0) + 1

    declared_cells = {(mode, cond, d): n
                      for mode, cells in DECLARED_MATRIX.items()
                      for (cond, d), n in cells.items()}
    assert actual_cells == declared_cells, (
        f"cell matrix mismatch: declared {len(declared_cells)} cells, "
        f"actual {len(actual_cells)}")
    declared_per_mode = {mode: sum(c.values())
                          for mode, c in DECLARED_MATRIX.items()}
    assert actual_per_mode == declared_per_mode, (
        f"per-mode mismatch: declared {declared_per_mode}, actual {actual_per_mode}")
    assert len(rows) == DECLARED_TOTAL, (
        f"total mismatch: declared {DECLARED_TOTAL}, actual {len(rows)}")
    return {"total": len(rows), "per_mode": actual_per_mode}


def main(argv: list[str]) -> int:
    rows = build_rows()
    summary = self_check(rows)

    out = Path(argv[1]) if len(argv) > 1 else \
        Path(__file__).resolve().parents[3] / "docs" / "tech-lab" / "experiments" / "b02-w016-checklist.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"checklist: {out}")
    print(f"declared total: {summary['total']} "
          f"(raycast {summary['per_mode']['raycast']} + "
          f"scene_depth {summary['per_mode']['scene_depth']} + "
          f"smoothed_depth {summary['per_mode']['smoothed_depth']})")
    print("declared matrix verified against emitted rows: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
