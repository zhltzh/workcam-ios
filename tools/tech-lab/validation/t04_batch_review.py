#!/usr/bin/env python3
"""W-03 - T04 Mac LiDAR CSV acceptance review CLI.

Reviews a Mac-exported experiment CSV against the T04 checklist:
format, missing fields, duplicate IDs, unknown modes, outliers,
completion, error statistics, failure reasons. Emits JSON + Markdown.

Works with PARTIAL data - a half-finished experiment is fully analyzed
and its completion percentage reported. Never fabricates device data.

Usage:
    python tools/tech-lab/validation/t04_batch_review.py <experiment.csv> \
        [--checklist path.csv] [--json out.json] [--md out.md]

Default checklist: docs/tech-lab/experiments/t04-checklist.csv (132 shots).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from techlab.t04_review import (  # noqa: E402
    DEFAULT_CHECKLIST,
    review_csv,
    to_json_str,
    to_markdown,
)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    csv_path = Path(argv[1])
    if not csv_path.exists():
        print(f"FAIL: input CSV not found: {csv_path}")
        return 1
    csv_text = csv_path.read_text(encoding="utf-8-sig")

    args = argv[2:]
    checklist_path = None
    if "--checklist" in args:
        checklist_path = Path(args[args.index("--checklist") + 1])
        checklist_csv = checklist_path.read_text(encoding="utf-8-sig")
    else:
        # default: repo checklist (may not exist in trimmed checkouts)
        if DEFAULT_CHECKLIST.exists():
            checklist_csv = DEFAULT_CHECKLIST.read_text(encoding="utf-8-sig")
        else:
            checklist_csv = None

    review = review_csv(csv_text, checklist_csv)

    md = to_markdown(review)
    print(md)
    if "--json" in args:
        out = Path(args[args.index("--json") + 1])
        out.write_text(to_json_str(review), encoding="utf-8")
        print(f"JSON report: {out}", file=sys.stderr)
    if "--md" in args:
        out = Path(args[args.index("--md") + 1])
        out.write_text(md, encoding="utf-8")
        print(f"Markdown report: {out}", file=sys.stderr)

    if not review.get("format_ok"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
