#!/usr/bin/env python
"""TM-04 A02: auto-generate the remaining-work list and dependency
graph from TECH-MARATHON-04-STATUS.json (single status source).

Outputs (both under docs/management/windows/):
  - TM04-REMAINING.json  (machine-readable: remaining items, deps,
    ready-now set, blocked set)
  - stdout summary (counts + ready-now list)

Honesty rules: statuses come only from the JSON matrix; this tool
never invents progress. IN_PROGRESS + NOT_STARTED are "remaining";
BLOCKED items are reported separately with their recorded reasons.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "management" / "windows"
STATUS = DOCS / "TECH-MARATHON-04-STATUS.json"
OUT = DOCS / "TM04-REMAINING.json"

# TM-04 dependency graph (from the task sheet's execution order and
# the B/C queue semantics; reviewed against actual code on
# 2026-11-28). Keys are task IDs; values list hard prerequisites
# within the 120-item matrix. Absent key = no in-matrix dependency.
DEPENDS_ON: dict[str, list[str]] = {
    # A queue
    "A06": ["A04"],            # static checks are defined by real toolchain facts
    "A09": ["A08"],
    # B queue
    "B02": ["B01"],
    "B03": ["B01"],
    "B04": ["B03"],
    "B05": ["B03"],
    "B06": ["B05"],
    "B07": ["B02", "B05"],
    "B08": ["B07"],
    "B09": ["B02"],
    "B10": ["B07"],
    "B11": ["B01"],
    "B12": ["B11"],
    "B13": ["B02"],
    "B14": ["B11"],
    "B15": ["B02", "B03", "B04", "B05", "B06", "B07", "B08",
            "B09", "B10", "B11", "B12", "B13", "B14"],
    # C queue
    "C03": ["C02"],
    "C04": ["C03"],
    "C05": ["C14"],
    "C06": ["C02", "C04"],
    "C07": ["C05"],
    "C08": ["C14"],
    "C09": ["B01", "C14"],
    "C10": ["C02"],
    "C11": ["C14"],
    "C12": ["C14"],
    "C13": ["C02"],
    "C14": ["C01"],
    "C15": ["C01", "C02", "C03", "C04", "C05", "C06", "C07",
            "C08", "C09", "C10", "C11", "C12", "C13", "C14"],
    # D queue
    "D04": ["D03"],
    "D07": ["C02"],
    "D14": ["C04"],
    "D15": ["D01", "D05", "C02"],
    # E queue
    "E07": ["C14"],
    "E08": ["E07"],
    "E09": ["C14"],
    "E14": ["E11"],
    "E15": ["E11", "E12", "E13", "E14"],
    # F queue
    "F07": ["F05"],
    "F10": ["F08"],
    # G queue
    "G07": ["G01"],
    "G08": ["G01"],
    "G09": ["G01"],
    "G10": ["G01", "G02", "G03"],
    # H queue (all isolated, no cross-queue deps by directive)
    # I queue
    "I02": ["I01"],
    "I04": ["C07"],
    "I05": ["I06"],
    "I10": ["A04"],
    # J queue
    "J02": ["C01"],
    "J04": ["C15"],
    "J05": ["C04"],
    "J06": ["B15"],
    "J08": ["J01", "C15", "B15"],
    "J09": ["J08"],
    "J10": ["A10", "J08"],
}


def main() -> int:
    data = json.loads(STATUS.read_text(encoding="utf-8"))
    tasks = {t["id"]: t for t in data["tasks"]}
    remaining = {k for k, v in tasks.items()
                 if v["state"] in ("NOT_STARTED", "IN_PROGRESS")}
    blocked = {k for k, v in tasks.items() if v["state"] == "BLOCKED"}

    # ready-now: remaining items whose deps are all done
    # (DV/POC/INT count as done; blocked deps stay blocking).
    done = {k for k, v in tasks.items()
            if v["state"] not in ("NOT_STARTED", "IN_PROGRESS",
                                  "BLOCKED")}
    ready = sorted(
        t for t in remaining
        if all(d in done for d in DEPENDS_ON.get(t, [])))

    dep_blockers: dict[str, list[str]] = {}
    for t in sorted(remaining):
        miss = [d for d in DEPENDS_ON.get(t, [])
                if d not in done]
        if miss:
            dep_blockers[t] = miss

    by_queue: dict[str, list[str]] = {}
    for t in sorted(remaining):
        by_queue.setdefault(tasks[t]["queue"], []).append(t)

    out = {
        "plan_id": "TECH-MARATHON-04",
        "generated_by": "backend/tools/tm04_remaining.py",
        "total": len(tasks),
        "remaining_count": len(remaining),
        "blocked_count": len(blocked),
        "blocked_items": sorted(blocked),
        "ready_now": ready,
        "dependency_graph": DEPENDS_ON,
        "dependency_blockers": dep_blockers,
        "remaining_by_queue": by_queue,
    }
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")

    print(f"total={len(tasks)} remaining={len(remaining)} "
          f"blocked={len(blocked)}")
    print(f"ready_now ({len(ready)}): " + ", ".join(ready))
    print("by_queue: " + ", ".join(
        f"{q}={len(v)}" for q, v in sorted(by_queue.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
