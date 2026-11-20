"""T04 - LiDAR CSV analysis tool self-tests (synthetic data only)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "tools" / "tech-lab" / "benchmarks"))

from lidar_error_analysis import analyze_csv, generate_sample_dataset  # noqa: E402


def test_sample_dataset_analysis_shape():
    result = analyze_csv(generate_sample_dataset())
    assert set(result.keys()) == {"overall", "conditions", "notes"}

    conds = {c["condition"]: c for c in result["conditions"]}
    assert set(conds) == {"normal", "low_light", "glass"}

    # normal: 20 attempts, zero failures, ~1-2 cm error
    assert conds["normal"]["attempts"] == 20
    assert conds["normal"]["failures"] == 0
    assert 0.0 <= conds["normal"]["mean_abs_error_m"] <= 0.02

    # low_light: failures counted, never dropped
    assert conds["low_light"]["failures"] == 2
    assert conds["low_light"]["failure_rate"] == round(2 / 16, 4)

    # glass: mostly failures
    assert conds["glass"]["failure_rate"] == 0.75


def test_failures_are_counted_not_dropped():
    csv_text = (
        "distance_true_m,distance_measured_m,condition,distance_m\n"
        "1.0,1.01,normal,1.0\n"
        "1.0,,normal,1.0\n"          # failed attempt: empty measurement
    )
    result = analyze_csv(csv_text)
    assert result["overall"]["attempts"] == 2
    assert result["overall"]["failures"] == 1
    assert result["overall"]["failure_rate"] == 0.5


def test_missing_columns_rejected():
    import pytest

    with pytest.raises(ValueError):
        analyze_csv("a,b\n1,2\n")
