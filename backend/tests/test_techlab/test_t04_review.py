"""W-03 - T04 batch review tests (core + CLI end-to-end)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from techlab.lidar_stats import MAC_COLUMNS
from techlab.t04_review import review_csv, to_json_str, to_markdown

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS = REPO_ROOT / "tools" / "tech-lab"
BENCH_DIR = TOOLS / "benchmarks"
REVIEW_CLI = TOOLS / "validation" / "t04_batch_review.py"
CHECKLIST = REPO_ROOT / "docs" / "tech-lab" / "experiments" / "t04-checklist.csv"

sys.path.insert(0, str(BENCH_DIR))
from lidar_error_analysis import generate_sample_dataset  # noqa: E402


def _row(test_id, cond, true_m, meas, failure="", mode="raycast"):
    meas_s = f"{meas:.3f}" if meas is not None else ""
    return (f"b1,{test_id},iPhone SYN,17.0,true,0.9,0.8,ok,{mode},{cond},"
            f"2026-11-20T10:00:00+00:00,{true_m},{meas_s},,,0.04,{failure},{true_m},,")


def _csv(*rows):
    return ",".join(MAC_COLUMNS) + "\n" + "\n".join(rows) + "\n"


@pytest.fixture(scope="module")
def checklist_csv() -> str:
    return CHECKLIST.read_text(encoding="utf-8-sig")


def test_format_error_reported_not_raised():
    review = review_csv("a,b\n1,2\n", checklist_csv=None)
    assert review["format_ok"] is False
    assert "unrecognized CSV header" in review["format_error"]
    md = to_markdown(review)
    assert "FORMAT ERROR" in md


def test_partial_data_completion_percentage(checklist_csv):
    """Synthetic dataset covers 44 of the 132 checklist IDs (normal 20 +
    low_light 16 + glass 8) -> 33.3% completion, missing counted."""
    review = review_csv(generate_sample_dataset(), checklist_csv)
    comp = review["completion"]
    assert review["format_ok"] is True
    assert comp["checklist_total"] == 132
    assert comp["completed_ids"] == 44
    assert comp["completion_pct"] == 33.3
    assert comp["missing_count"] == 88
    assert len(comp["missing_ids"]) == 50          # capped preview list
    assert comp["extra_ids"] == []                # all IDs match the checklist
    assert review["synthetic"] is True


def test_unexpected_ids_reported(checklist_csv):
    csv_text = _csv(_row("T04-raycast-normal-1m-R1", "normal", 1.0, 1.01),
                    _row("MY-CUSTOM-TEST-ID", "normal", 1.0, 1.02))
    review = review_csv(csv_text, checklist_csv)
    assert review["completion"]["extra_ids"] == ["MY-CUSTOM-TEST-ID"]
    assert review["completion"]["completed_ids"] == 1


def test_duplicate_ids_structured(checklist_csv):
    csv_text = _csv(_row("DUP-1", "normal", 1.0, 1.01),
                    _row("DUP-1", "normal", 1.0, 1.02))
    review = review_csv(csv_text, checklist_csv)
    assert "DUP-1" in review["duplicate_test_ids"]
    md = to_markdown(review)
    assert "DUPLICATE test_ids" in md


def test_outliers_listed_not_removed(checklist_csv):
    csv_text = _csv(
        _row("ok-1", "normal", 1.0, 1.01),
        _row("out-1", "normal", 1.0, 12.0),          # beyond LiDAR range
        _row("out-2", "normal", 1.0, 0.01),          # below plausible minimum
        _row("out-3", "normal", 1.0, 3.5),           # |error| > 1 m
    )
    review = review_csv(csv_text, checklist_csv)
    ids = {o["test_id"] for o in review["outliers"]}
    assert ids == {"out-1", "out-2", "out-3"}
    # they stay in the statistics (attempts include outliers)
    assert review["stats"]["overall"]["attempts"] == 4


def test_failure_summary_and_null_honesty(checklist_csv):
    csv_text = _csv(_row("ok-1", "normal", 1.0, 1.01),
                    _row("f-1", "glass", 1.0, None, failure="depth_missing"),
                    _row("f-2", "glass", 1.0, None, failure="depth_missing"))
    review = review_csv(csv_text, checklist_csv)
    assert review["failure_summary"] == {"depth_missing": 2}
    # only 1 success -> statistics null (not fake numbers)
    assert review["stats"]["overall"]["mean_abs_error_m"] is None
    # strict JSON
    json.loads(to_json_str(review))


def test_verdict_sections_in_markdown(checklist_csv):
    review = review_csv(generate_sample_dataset(), checklist_csv)
    md = to_markdown(review)
    assert "## Completion" in md
    assert "33.3" in md
    assert "## Data quality" in md
    assert "## Failure reasons" in md
    assert "SYNTHETIC" in md
    assert "INCOMPLETE / NEEDS ATTENTION" in md


def test_cli_end_to_end(tmp_path):
    csv_path = tmp_path / "batch.csv"
    csv_path.write_text(generate_sample_dataset(), encoding="utf-8")
    out_json = tmp_path / "r.json"
    out_md = tmp_path / "r.md"
    import os

    proc = subprocess.run(
        [sys.executable, str(REVIEW_CLI), str(csv_path),
         "--checklist", str(CHECKLIST),
         "--json", str(out_json), "--md", str(out_md)],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["completion"]["checklist_total"] == 132
    assert out_md.exists()
    assert "T04 Experiment Batch Review" in out_md.read_text(encoding="utf-8")
