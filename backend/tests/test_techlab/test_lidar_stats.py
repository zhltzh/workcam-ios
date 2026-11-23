"""W-02 - LiDAR statistics core tests (Mac CSV + legacy + null honesty)."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "backend" / ".venv" / "Lib" / "site-packages") ) if False else None

from techlab.lidar_stats import (  # noqa: E402
    CSVFormatError,
    MAC_COLUMNS,
    analyze,
    parse_csv,
    stats_for,
    to_json,
)

TOOLS = Path(__file__).parents[3] / "tools" / "tech-lab" / "benchmarks"
sys.path.insert(0, str(TOOLS))
from lidar_error_analysis import generate_sample_dataset  # noqa: E402


def _mac_row(test_id, cond, true_m, meas, failure="", mode="raycast", dist=None):
    d = f"{dist:g}" if dist is not None else f"{true_m:g}"
    meas_s = f"{meas:.3f}" if meas is not None else ""
    return (f"b1,{test_id},iPhone SYN,17.0,true,0.9,0.8,ok,{mode},{cond},"
            f"2026-11-20T10:00:00+00:00,{true_m},{meas_s},,,0.04,{failure},{d},,")


def _legacy_row(true_m, meas, cond="normal"):
    return f"{true_m},{'' if meas is None else meas},{cond},{true_m}"


# ------------------------------------------------------------------ parsing


def test_parse_mac_csv_full_columns():
    csv_text = ",".join(["batch_id", "test_id", "device_model", "ios_version",
                         "depth_received", "confidence_end", "confidence_start",
                         "tracking", "mode", "condition", "device_time",
                         "distance_true_m", "distance_measured_m", "absolute_error_m",
                         "relative_error", "elapsed_s", "failure", "distance_m",
                         "first_device_time", "first_tracking"]) + "\n"
    csv_text += _mac_row("T1", "normal", 1.0, 1.01) + "\n"
    rows, meta = parse_csv(csv_text)
    assert meta["format"] == "mac"
    assert len(rows) == 1
    assert rows[0].test_id == "T1"
    assert rows[0].failure == ""
    assert rows[0].distance_measured_m == pytest.approx(1.01)


def test_parse_legacy_csv_defaults():
    csv_text = "distance_true_m,distance_measured_m,condition,distance_m\n"
    csv_text += _legacy_row(1.0, 1.02) + "\n"
    csv_text += _legacy_row(1.0, None) + "\n"
    rows, meta = parse_csv(csv_text)
    assert meta["format"] == "legacy"
    assert rows[0].mode == "raycast"
    assert rows[1].failure != ""          # empty measurement = counted failure
    assert rows[1].distance_measured_m is None


def test_parse_rejects_unknown_header():
    with pytest.raises(CSVFormatError):
        parse_csv("a,b\n1,2\n")


def test_duplicate_test_id_warns_not_hidden():
    csv_text = ",".join(MAC_COLUMNS) + "\n"
    csv_text += _mac_row("DUP", "normal", 1.0, 1.0) + "\n"
    csv_text += _mac_row("DUP", "normal", 1.0, 1.0) + "\n"
    _rows, meta = parse_csv(csv_text)
    assert any("DUPLICATE test_id 'DUP'" in w for w in meta["warnings"])


def test_unknown_mode_and_condition_warn_but_keep_row():
    csv_text = ",".join(MAC_COLUMNS) + "\n"
    csv_text += _mac_row("X1", "weird_cond", 1.0, 1.0, mode="lidar_magic") + "\n"
    rows, meta = parse_csv(csv_text)
    assert rows[0].mode == "lidar_magic"
    assert any("unknown mode" in w for w in meta["warnings"])
    assert any("unknown condition" in w for w in meta["warnings"])


def test_mac_row_missing_measurement_without_reason_is_failure():
    csv_text = ",".join(MAC_COLUMNS) + "\n"
    csv_text += _mac_row("F1", "normal", 1.0, None, failure="") + "\n"
    rows, _ = parse_csv(csv_text)
    assert rows[0].failure == "measurement_missing"   # never silently dropped


# ---------------------------------------------------------------- statistics


def test_stats_math_exact_small_group():
    rows = [type("M", (), {
        "test_id": f"t{i}", "mode": "raycast", "condition": "normal",
        "distance_true_m": 1.0, "distance_measured_m": 1.0 + e,
        "distance_m": 1.0, "failure": "", "raw": {},
    })() for i, e in enumerate((0.01, -0.01, 0.03))]
    s = stats_for(rows)
    abs_errs = [0.01, 0.01, 0.03]
    assert s["attempts"] == 3 and s["failures"] == 0
    assert s["mean_abs_error_m"] == pytest.approx(sum(abs_errs) / 3)
    assert s["median_abs_error_m"] == pytest.approx(0.01)
    assert s["rmse_m"] == pytest.approx(math.sqrt((0.0001 + 0.0001 + 0.0009) / 3))
    assert s["p95_abs_error_m"] == pytest.approx(0.028)   # interpolated: 0.01+0.9*(0.03-0.01)
    assert s["mean_signed_error_m"] == pytest.approx(0.01)   # (0.01-0.01+0.03)/3
    assert s["repeatability_stddev_m"] is not None


def test_insufficient_samples_all_null_but_failures_counted():
    rows = [type("M", (), {
        "test_id": f"t{i}", "mode": "raycast", "condition": "normal",
        "distance_true_m": 1.0, "distance_measured_m": m,
        "distance_m": 1.0, "failure": f, "raw": {},
    })() for i, (m, f) in enumerate([(1.01, ""), (None, "depth_missing"), (None, "depth_missing")])]
    s = stats_for(rows)
    assert s["attempts"] == 3 and s["failures"] == 2
    assert s["failure_reasons"] == {"depth_missing": 2}
    # honesty: statistics are null, counts are NOT
    for key in ("mean_abs_error_m", "rmse_m", "median_abs_error_m",
                "p95_abs_error_m", "mean_signed_error_m",
                "repeatability_stddev_m", "success_rate", "failure_rate"):
        assert s[key] is None
    assert s["sufficient_samples"] is False
    # and the JSON stays strictly valid
    json.loads(to_json({"s": s}))


def test_analyze_groups_by_mode_condition_distance_cell():
    csv_text = ",".join(MAC_COLUMNS) + "\n"
    for i in range(3):
        csv_text += _mac_row(f"a{i}", "normal", 1.0, 1.01) + "\n"
        csv_text += _mac_row(f"b{i}", "normal", 3.0, 3.02, mode="scene_depth") + "\n"
    rows, _ = parse_csv(csv_text)
    result = analyze(rows)
    assert set(result["by_mode"]) == {"raycast", "scene_depth"}
    assert set(result["by_true_distance"]) == {"1m", "3m"}
    assert "raycast/normal/1m" in result["by_cell"]
    assert result["by_mode"]["scene_depth"]["successes"] == 3


def test_failure_rows_preserved_verbatim():
    csv_text = ",".join(MAC_COLUMNS) + "\n"
    csv_text += _mac_row("ok1", "normal", 1.0, 1.01) + "\n"
    csv_text += _mac_row("fail1", "glass", 1.0, None, failure="depth_missing") + "\n"
    rows, _ = parse_csv(csv_text)
    result = analyze(rows)
    assert len(result["failure_rows"]) == 1
    assert result["failure_rows"][0]["test_id"] == "fail1"
    assert result["failure_rows"][0]["failure"] == "depth_missing"
    assert result["overall"]["failure_reasons"] == {"depth_missing": 1}


def test_json_output_never_contains_nan_or_infinity():
    csv_text = ",".join(MAC_COLUMNS) + "\n"
    csv_text += _mac_row("only1", "normal", 1.0, 1.01) + "\n"   # 1 success -> nulls
    rows, _ = parse_csv(csv_text)
    text = to_json(analyze(rows))
    assert "NaN" not in text and "Infinity" not in text
    json.loads(text)   # strict parse


# ------------------------------------------------------------ synthetic data


def test_synthetic_dataset_shape_and_labels():
    rows, meta = parse_csv(generate_sample_dataset())
    assert meta["format"] == "mac"
    # every batch is labeled SYNTHETIC
    assert all("SYNTHETIC" in (r.raw.get("batch_id") or "") for r in rows)

    result = analyze(rows)
    glass = result["by_condition"]["glass"]
    assert glass["failures"] == 6
    assert glass["failure_reasons"].get("depth_missing") == 6
    # by_mode aggregates ALL raycast rows: low_light 2 + glass 6 = 8 failures
    raycast = result["by_mode"]["raycast"]
    assert raycast["failures"] == 8
    assert raycast["attempts"] == 44          # normal 20 + low_light 16 + glass 8
    # normal-only stats stay clean
    normal = result["by_condition"]["normal"]
    assert normal["failures"] == 0
    assert normal["mean_abs_error_m"] == pytest.approx(0.01, abs=1e-9)
    low_light = result["by_condition"]["low_light"]
    assert low_light["failures"] == 2


def test_cli_analyzer_end_to_end(tmp_path):
    import subprocess
    cli = TOOLS / "lidar_error_analysis.py"
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(generate_sample_dataset(), encoding="utf-8")
    out_json = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, str(cli), str(csv_path), "--json", str(out_json)],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["meta"]["synthetic"] is True
    assert "by_mode" in report
