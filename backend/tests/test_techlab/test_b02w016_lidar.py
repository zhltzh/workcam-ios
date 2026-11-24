"""B02-W016 - LiDAR v2 statistics tests.

Regression guards for the three historic defects (commit 355edf9):
1. declared shot counts must equal actual recorded rows (matrix check)
2. raycast / scene_depth / smoothed_depth must never be merged
3. repeatability = signed-error stddev of repeats of the SAME baseline
   distance + condition (cell level only), never the stddev of
   absolute errors, never across distances

Plus: hand-computed metric verification, preserved failure/outlier/
invalid rows, v1/v2 CSV factor mapping, groups CSV round-trip,
Markdown sections and CLI end-to-end on DESKTOP_SIMULATION data.
"""

from __future__ import annotations

import csv as csvmod
import io
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from techlab.lidar_stats_v2 import (
    GROUP_CSV_COLUMNS,
    analyze_v2,
    groups_to_csv,
    parse_csv_v2,
    stats_for_v2,
    to_json_v2,
)
from techlab.lidar_report_v2 import to_markdown_v2

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS = REPO_ROOT / "tools" / "tech-lab"
sys.path.insert(0, str(TOOLS / "benchmarks"))
sys.path.insert(0, str(TOOLS / "validation"))
from b02w016_checklist import DECLARED_MATRIX, build_rows, self_check  # noqa: E402
from gen_b02w016_samples import generate  # noqa: E402

B02_CHECKLIST = REPO_ROOT / "docs" / "tech-lab" / "experiments" / "b02-w016-checklist.csv"
T04_CHECKLIST = REPO_ROOT / "docs" / "tech-lab" / "experiments" / "t04-checklist.csv"
ANALYSIS_CLI = TOOLS / "benchmarks" / "b02w016_lidar_analysis.py"

V2_HEADER = ["batch_id", "test_id", "device_model", "ios_version",
             "depth_received", "confidence_end", "confidence_start",
             "tracking", "mode", "condition", "device_time",
             "distance_true_m", "distance_measured_m", "absolute_error_m",
             "relative_error", "elapsed_s", "failure", "distance_m",
             "first_device_time", "first_tracking",
             "material", "lighting", "pose"]


def _row(test_id, cond, true_m, meas, failure="", mode="raycast",
         material="", lighting="", pose="", batch="b1"):
    meas_s = f"{meas:.3f}" if meas is not None else ""
    return (f"{batch},{test_id},iPhone SYN,17.0,true,0.9,0.8,ok,{mode},{cond},"
            f"2026-11-25T10:00:00+00:00,{true_m:g},{meas_s},,,0.04,{failure},"
            f"{true_m:g},,,{material},{lighting},{pose}")


V1_HEADER = V2_HEADER[:20]      # plain Mac 20-column header (no factors)


def _csv(*rows, header=V2_HEADER):
    return ",".join(header) + "\n" + "\n".join(rows) + "\n"


def _analyze(csv_text, checklist_csv=None):
    records, invalid_rows, meta = parse_csv_v2(csv_text)
    return analyze_v2(records, invalid_rows, meta, checklist_csv), records, meta


# ------------------------------------------------- defect 1: matrix honesty


def test_checklist_declared_equals_generated():
    """Generation-side guard: declared matrix must be derivable from
    emitted rows (per cell, per mode, grand total)."""
    rows = build_rows()
    summary = self_check(rows)
    declared_sum = sum(sum(c.values()) for c in DECLARED_MATRIX.values())
    assert len(rows) == declared_sum == 132
    assert summary["per_mode"] == {"raycast": 68, "scene_depth": 32,
                                   "smoothed_depth": 32}


def test_checklist_csv_on_disk_matches_generator():
    text = B02_CHECKLIST.read_text(encoding="utf-8-sig")
    reader = csvmod.DictReader(io.StringIO(text))
    rows = list(reader)
    assert len(rows) == 132
    assert len({r["test_id"] for r in rows}) == 132
    assert all(r["test_id"].startswith("B02-W016-") for r in rows)
    # every disk row is byte-identical in key fields to the generator's
    gen = {r["test_id"]: r for r in build_rows()}
    assert set(gen) == {r["test_id"] for r in rows}
    assert all(gen[r["test_id"]]["material"] == r["material"] for r in rows)


def _declared_checklist(total: int, per_cell: int, key=("raycast", "normal", 1.0)) -> str:
    lines = ["test_id,mode,condition,distance_true_m"]
    for i in range(total):
        lines.append(f"C{i:03d},{key[0]},{key[1]},{key[2]:g}")
    return "\n".join(lines) + "\n"


def test_matrix_check_detects_declared_vs_actual_mismatch():
    """Historic defect replay: 96 declared vs 68 actual must FAIL the
    check with per-mode and per-cell differences (355edf9)."""
    declared_96 = _declared_checklist(96, 96)
    actual_68 = _csv(*[_row(f"a{i}", "normal", 1.0, 1.01 + 0.001 * i) for i in range(68)])
    analysis, _, _ = _analyze(actual_68, declared_96)
    mx = analysis["matrix_check"]
    assert mx["match"] is False
    assert mx["declared_total"] == 96
    assert mx["actual_total"] == 68
    assert mx["per_mode"]["raycast"] == {"declared": 96, "actual": 68,
                                         "diff": -28, "match": False}
    assert mx["per_cell_diffs"] == [
        {"cell": "raycast/normal/1m", "declared": 96, "actual": 68, "diff": -28}]
    md = to_markdown_v2(analysis)
    assert "declared matrix does NOT match actual records" in md


def test_matrix_check_passes_on_clean_full_coverage():
    """All 132 checklist slots present exactly once -> MATCH."""
    rows = build_rows()
    csv_text = _csv(*[_row(r["test_id"], r["condition"],
                           float(r["distance_true_m"]),
                           float(r["distance_true_m"]) + 0.01,
                           mode=r["mode"],
                           material=r["material"], lighting=r["lighting"],
                           pose=r["pose"]) for r in rows])
    analysis, _, _ = _analyze(csv_text, B02_CHECKLIST.read_text(encoding="utf-8-sig"))
    mx = analysis["matrix_check"]
    assert mx["match"] is True
    assert mx["declared_total"] == mx["actual_total"] == 132
    assert mx["per_cell_diffs"] == []
    assert mx["completion"]["completed_ids"] == 132
    assert mx["completion"]["missing_count"] == 0


def test_synthetic_sample_matrix_mismatch_caught_by_duplicate():
    """The injected duplicate test_id inflates one cell (4 declared vs
    5 actual) - the matrix check must catch it even though every
    checklist id was submitted."""
    sim_rows, _ = generate()
    csv_text = "\n".join(",".join(r) for r in sim_rows) + "\n"
    analysis, _, _ = _analyze(csv_text, B02_CHECKLIST.read_text(encoding="utf-8-sig"))
    mx = analysis["matrix_check"]
    assert mx["match"] is False
    diff_cells = {d["cell"]: d for d in mx["per_cell_diffs"]}
    assert diff_cells["raycast/normal/1m"]["diff"] == 1
    assert any("DUPLICATE test_id" in w for w in analysis["warnings"])


def test_matrix_check_compatible_with_legacy_t04_checklist():
    from lidar_error_analysis import generate_sample_dataset
    analysis, _, _ = _analyze(generate_sample_dataset(),
                              T04_CHECKLIST.read_text(encoding="utf-8-sig"))
    mx = analysis["matrix_check"]
    assert mx["declared_total"] == 132
    assert mx["actual_total"] == 44
    assert mx["match"] is False
    assert mx["completion"]["completed_ids"] == 44


# ---------------------------------------------- defect 2: mode separation


def test_modes_strictly_separated():
    """Grouping invariance: per-mode stats from the mixed CSV equal the
    stats computed from each mode alone; no cross-mode statistics exist
    at the top level."""
    mixed = _csv(
        *[_row(f"r{i}", "normal", 1.0, 1.0 + 0.01 * (i + 1)) for i in range(3)],
        *[_row(f"s{i}", "normal", 3.0, 3.0 + 0.05 * (i + 1), mode="scene_depth") for i in range(3)],
        *[_row(f"m{i}", "normal", 5.0, 5.0 - 0.02 * (i + 1), mode="smoothed_depth") for i in range(3)],
    )
    raycast_only = _csv(*[_row(f"r{i}", "normal", 1.0, 1.0 + 0.01 * (i + 1)) for i in range(3)])

    mixed_analysis, _, _ = _analyze(mixed)
    raycast_alone, _, _ = _analyze(raycast_only)

    r_mixed = mixed_analysis["by_mode"]["raycast"]
    r_alone = raycast_alone["by_mode"]["raycast"]
    for key in ("attempts", "valid_samples", "success_rate", "mean_signed_error_m",
                "mean_abs_error_m", "rmse_m", "median_abs_error_m",
                "p95_abs_error_m", "max_abs_error_m"):
        assert r_mixed[key] == pytest.approx(r_alone[key], rel=1e-12)
    # no cross-mode error statistics anywhere above the per-mode groups
    acc = mixed_analysis["accounting"]
    for forbidden in ("mean_abs_error_m", "rmse_m", "p95_abs_error_m",
                      "mean_signed_error_m", "repeatability_stddev_m"):
        assert forbidden not in acc
    assert "overall" not in mixed_analysis
    # modes do not pollute each other
    assert mixed_analysis["by_mode"]["raycast"]["mean_abs_error_m"] == pytest.approx(0.02)
    assert mixed_analysis["by_mode"]["scene_depth"]["mean_abs_error_m"] == pytest.approx(0.10)
    assert mixed_analysis["by_mode"]["smoothed_depth"]["mean_abs_error_m"] == pytest.approx(0.04)


def test_mode_alias_normalization_keeps_modes_separated():
    csv_text = _csv(
        _row("a1", "normal", 1.0, 1.01, mode="Raycast"),
        _row("a2", "normal", 1.0, 1.02, mode="RAYCAST"),
        _row("a3", "normal", 1.0, 1.03, mode="ray cast"),
        _row("b1", "normal", 1.0, 1.10, mode="SCENEDEPTH"),
        _row("b2", "normal", 1.0, 1.12, mode="scene depth"),
        _row("b3", "normal", 1.0, 1.14, mode="scene_depth"),
        _row("c1", "normal", 1.0, 1.20, mode="Smoothed"),
        _row("c2", "normal", 1.0, 1.22, mode="Smoothed Depth"),
        _row("c3", "normal", 1.0, 1.24, mode="smoothed_depth"),
    )
    records, _, meta = parse_csv_v2(csv_text)
    modes = sorted({m.mode for m in records})
    assert modes == ["raycast", "scene_depth", "smoothed_depth"]
    # 5 aliases normalized ("RAYCAST" lower-cases to the canonical name
    # and passes without a warning - that is acceptable behavior)
    assert sum(1 for w in meta["warnings"] if "normalized to" in w) == 5
    assert "line 4: mode 'ray cast' normalized to 'raycast'" in meta["warnings"]
    assert "line 5: mode 'SCENEDEPTH' normalized to 'scene_depth'" in meta["warnings"]
    analysis, _, _ = _analyze(csv_text)
    assert analysis["by_mode"]["raycast"]["attempts"] == 3
    assert analysis["by_mode"]["scene_depth"]["attempts"] == 3
    assert analysis["by_mode"]["smoothed_depth"]["attempts"] == 3


# --------------------------------------- defect 3: repeatability semantics


def test_repeatability_uses_signed_stddev_not_abs_stddev():
    """Cell with signed errors [+0.05,-0.05,+0.05,-0.05]:
    absolute errors are all 0.05 -> their stddev is ZERO. A repeatability
    of zero would be the historic defect (abs stddev masquerading as
    repeatability). The correct cell repeatability is the signed
    stddev: 0.0577350..."""
    csv_text = _csv(
        _row("t1", "normal", 1.0, 1.05),
        _row("t2", "normal", 1.0, 0.95),
        _row("t3", "normal", 1.0, 1.05),
        _row("t4", "normal", 1.0, 0.95),
    )
    analysis, _, _ = _analyze(csv_text)
    cell = analysis["by_cell"]["raycast/normal/1m"]
    expected = math.sqrt((0.05 ** 2 + 0.05 ** 2 + 0.05 ** 2 + 0.05 ** 2) / 3)
    assert cell["repeatability_stddev_m"] == pytest.approx(expected)  # 0.0577350
    assert cell["repeatability_stddev_m"] > 0            # abs stddev would be 0.0
    assert cell["mean_signed_error_m"] == pytest.approx(0.0)
    assert cell["mean_abs_error_m"] == pytest.approx(0.05)
    summary = analysis["cell_repeatability"]
    assert len(summary) == 1
    assert summary[0]["repeatability_stddev_m"] == pytest.approx(expected)


def test_repeatability_null_on_every_aggregation_level():
    """Repeatability must NOT be computed when different baseline
    distances are mixed (aggregation levels)."""
    csv_text = _csv(
        *[_row(f"a{i}", "normal", 1.0, 1.0 + 0.01 * i) for i in range(4)],
        *[_row(f"b{i}", "normal", 3.0, 3.0 + 0.02 * i) for i in range(4)],
        *[_row(f"c{i}", "glass", 1.0, 1.0 + 0.03 * i) for i in range(4)],
    )
    analysis, _, _ = _analyze(csv_text)
    for level in ("by_mode", "by_mode_condition", "by_mode_material",
                  "by_mode_lighting", "by_mode_pose", "by_mode_distance"):
        for key, s in analysis[level].items():
            assert s["repeatability_stddev_m"] is None, (level, key)
            assert s["repeatability_scope"] is None
    # only cells carry repeatability
    cells = analysis["by_cell"]
    assert set(cells) == {"raycast/normal/1m", "raycast/normal/3m", "raycast/glass/1m"}
    assert all(s["repeatability_stddev_m"] is not None for s in cells.values())
    assert all(s["repeatability_scope"] == "cell" for s in cells.values())


# -------------------------------------------------- metrics hand-computed


def test_all_metrics_hand_computed():
    measurements = [1.00, 1.02, 0.98, 1.04, 0.96]
    rows = [_row(f"m{i}", "normal", 1.0, v) for i, v in enumerate(measurements)]
    rows.append(_row("f1", "normal", 1.0, None, failure="depth_missing"))
    analysis, _, _ = _analyze(_csv(*rows))
    s = analysis["by_mode"]["raycast"]
    abs_errs = [0.00, 0.02, 0.02, 0.04, 0.04]
    assert s["attempts"] == 6
    assert s["valid_samples"] == 5
    assert s["failures"] == 1
    assert s["failure_reasons"] == {"depth_missing": 1}
    assert s["success_rate"] == pytest.approx(5 / 6)
    assert s["mean_signed_error_m"] == pytest.approx(0.0)
    assert s["mean_abs_error_m"] == pytest.approx(0.12 / 5)          # 0.024
    assert s["rmse_m"] == pytest.approx(math.sqrt(0.0040 / 5))       # 0.0282843
    assert s["median_abs_error_m"] == pytest.approx(0.02)
    assert s["p95_abs_error_m"] == pytest.approx(0.04)                # rank 3.8 -> xs[3]
    assert s["max_abs_error_m"] == pytest.approx(0.04)
    # repeatability is null at mode level (only cell level has it)
    assert s["repeatability_stddev_m"] is None
    cell = analysis["by_cell"]["raycast/normal/1m"]
    assert cell["repeatability_stddev_m"] == pytest.approx(math.sqrt(0.004 / 4))


def test_insufficient_valid_samples_report_nulls_but_counts():
    rows = [_row("ok1", "normal", 1.0, 1.01),
            _row("f1", "glass", 1.0, None, failure="depth_missing"),
            _row("f2", "glass", 1.0, None, failure="depth_missing")]
    analysis, _, _ = _analyze(_csv(*rows))
    glass = analysis["by_mode_condition"]["raycast/glass"]
    assert glass["attempts"] == 2 and glass["failures"] == 2
    for key in ("mean_abs_error_m", "rmse_m", "median_abs_error_m",
                "p95_abs_error_m", "max_abs_error_m", "mean_signed_error_m",
                "repeatability_stddev_m"):
        assert glass[key] is None
    assert glass["success_rate"] == 0.0
    assert glass["sufficient_samples"] is False


# ------------------------------------- preserved rows: nothing silently lost


def test_failures_outliers_invalid_never_dropped():
    invalid_row = ("b1,inv1,iPhone SYN,17.0,true,0.9,0.8,ok,raycast,normal,"
                   "2026-11-25T10:00:00+00:00,,1.000,,,0.04,,,0,,,")
    csv_text = _csv(
        _row("ok1", "normal", 1.0, 1.01),
        _row("fail1", "glass", 1.0, None, failure="depth_missing"),
        _row("unexp1", "normal", 1.0, None, failure=""),       # unexplained
        _row("out1", "normal", 1.0, 12.0),                     # outlier, kept
        invalid_row,           # empty distance_true_m -> invalid_rows, kept
    )
    analysis, records, _ = _analyze(csv_text)
    acc = analysis["accounting"]
    assert acc["rows_received"] == 5
    assert acc["invalid_rows"] == 1
    assert acc["attempts"] == 4
    assert len(analysis["invalid_rows"]) == 1
    assert analysis["invalid_rows"][0]["reason"] == "missing_or_invalid_distance_true_m"
    failures = {r["test_id"]: r["failure"] for r in analysis["failure_rows"]}
    assert failures == {"fail1": "depth_missing", "unexp1": "measurement_missing"}
    assert analysis["outliers"][0]["test_id"] == "out1"
    assert acc["outliers_reported"] == 1
    # outlier stays inside statistics (valid sample, no silent removal)
    assert analysis["by_mode"]["raycast"]["valid_samples"] == 2


# ----------------------------------------------------- factor grouping


def test_material_lighting_pose_derived_from_condition():
    csv_text = _csv(
        _row("n1", "normal", 1.0, 1.01),
        _row("n2", "normal", 1.0, 1.02),
        _row("n3", "normal", 1.0, 1.03),
        _row("l1", "low_light", 1.0, 1.10),
        _row("l2", "low_light", 1.0, 1.12),
        _row("l3", "low_light", 1.0, 1.14),
        _row("g1", "glass", 1.0, 1.01),
        _row("g2", "glass", 1.0, 1.02),
        _row("g3", "glass", 1.0, 1.03),
        _row("o1", "oblique", 1.0, 1.01),
        _row("o2", "oblique", 1.0, 1.02),
        _row("o3", "oblique", 1.0, 1.03),
        header=V1_HEADER,
    )
    records, _, meta = parse_csv_v2(csv_text)
    assert meta["format"] == "mac"      # plain 20-column v1 CSV
    assert meta["factors_derived_from_condition"] is True
    by_id = {m.test_id: m for m in records}
    assert by_id["l1"].material == "matte_white"       # low_light is LIGHTING
    assert by_id["l1"].lighting == "low_light"
    assert by_id["g1"].material == "glass"
    assert by_id["g1"].lighting == "normal"
    assert by_id["o1"].pose == "oblique_30"
    analysis, _, _ = _analyze(csv_text)
    assert "raycast/low_light" in analysis["by_mode_lighting"]
    assert "raycast/glass" in analysis["by_mode_material"]
    assert "raycast/oblique_30" in analysis["by_mode_pose"]
    # normal vs low_light differ in the LIGHTING dimension only
    assert analysis["by_mode_lighting"]["raycast/normal"]["mean_abs_error_m"] == pytest.approx(0.02)
    assert analysis["by_mode_lighting"]["raycast/low_light"]["mean_abs_error_m"] == pytest.approx(0.12)


def test_v2_explicit_factors_win_over_condition():
    csv_text = _csv(
        _row("v1", "glass", 1.0, 1.01, material="mirror", lighting="low_light"),
        _row("v2", "glass", 1.0, 1.02, material="mirror", lighting="low_light"),
        _row("v3", "glass", 1.0, 1.03, material="mirror", lighting="low_light"),
        _row("w1", "normal", 1.0, 1.01, material="chrome"),
    )
    records, _, meta = parse_csv_v2(csv_text)
    assert meta["factors_derived_from_condition"] is False
    assert records[0].material == "mirror"
    assert records[0].lighting == "low_light"
    assert any("unknown material 'chrome'" in w for w in meta["warnings"])
    assert records[3].material == "chrome"          # kept as-is, not dropped


def test_legacy_4column_csv_still_parses():
    csv_text = ("distance_true_m,distance_measured_m,condition,distance_m\n"
                "1.0,1.02,normal,1.0\n"
                "1.0,,normal,1.0\n")
    records, invalid_rows, meta = parse_csv_v2(csv_text)
    assert meta["format"] == "legacy"
    assert len(records) == 2 and not invalid_rows
    assert records[0].mode == "raycast"
    assert records[1].failure == "measurement_missing"
    assert records[0].material == "matte_white"


# ----------------------------------------------------- output artifacts


def test_groups_csv_roundtrip_and_nulls_visible():
    sim_rows, _ = generate()
    csv_text = "\n".join(",".join(r) for r in sim_rows) + "\n"
    analysis, _, _ = _analyze(csv_text)
    groups_text = groups_to_csv(analysis)
    reader = csvmod.reader(io.StringIO(groups_text))
    header = next(reader)
    assert header == GROUP_CSV_COLUMNS
    body = list(reader)
    n_expected = sum(len(analysis[level]) for level in
                     ("by_mode", "by_mode_condition", "by_mode_material",
                      "by_mode_lighting", "by_mode_pose", "by_mode_distance",
                      "by_cell"))
    assert len(body) == n_expected
    # cell-level rows carry repeatability; mode rows leave it empty
    cell_rows = [r for r in body if r[0] == "mode/condition/distance"
                 and r[13] != ""]
    assert cell_rows, "at least one cell with a repeatability value"
    mode_rows = [r for r in body if r[0] == "mode"]
    assert all(r[13] == "" for r in mode_rows)


def test_markdown_report_sections_and_labels():
    sim_rows, _ = generate()
    csv_text = "\n".join(",".join(r) for r in sim_rows) + "\n"
    analysis, _, _ = _analyze(csv_text, B02_CHECKLIST.read_text(encoding="utf-8-sig"))
    md = to_markdown_v2(analysis)
    assert "DESKTOP_SIMULATION" in md
    assert "DEVICE_PENDING" in md
    assert "Matrix verification" in md
    assert "Per-mode statistics" in md
    assert "modes strictly separated" in md
    assert "Repeatability (cell level only)" in md
    assert "INCOMPLETE / NEEDS ATTENTION" in md          # duplicate row injected


def test_json_strict_no_nan():
    sim_rows, _ = generate()
    csv_text = "\n".join(",".join(r) for r in sim_rows) + "\n"
    analysis, _, _ = _analyze(csv_text)
    text = to_json_v2(analysis)
    assert "NaN" not in text and "Infinity" not in text
    report = json.loads(text)
    assert report["schema"] == "b02-w016-lidar-stats/1"
    assert report["data_label"] == "DESKTOP_SIMULATION"


def test_stats_for_v2_direct_repeatability_gate():
    rows, _, _ = parse_csv_v2(_csv(*[_row(f"t{i}", "normal", 1.0, 1.0 + 0.01 * i)
                                     for i in range(3)]))
    gated = stats_for_v2(rows, allow_repeatability=False)
    assert gated["repeatability_stddev_m"] is None
    open_ = stats_for_v2(rows, allow_repeatability=True)
    # signed = [0.00, 0.01, 0.02] -> mean .01, var = .0001, std = 0.01
    assert open_["repeatability_stddev_m"] == pytest.approx(0.01)


# ------------------------------------------------------------- CLI e2e


def _run_cli(args, tmp_path):
    return subprocess.run(
        [sys.executable, str(ANALYSIS_CLI)] + args,
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def test_cli_end_to_end_desktop_simulation(tmp_path):
    sim_csv = tmp_path / "sample-b02-w016-sim.csv"
    rows, counts = generate()
    with open(sim_csv, "w", newline="", encoding="utf-8") as f:
        csvmod.writer(f).writerows(rows)
    out = tmp_path / "out"
    proc = _run_cli([str(sim_csv), "--checklist", str(B02_CHECKLIST),
                     "--out-dir", str(out)], tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr    # duplicate -> mismatch
    stem = sim_csv.stem
    report = json.loads((out / f"{stem}-stats.json").read_text(encoding="utf-8"))
    assert report["data_label"] == "DESKTOP_SIMULATION"
    assert report["accounting"]["invalid_rows"] == counts["invalid"]
    assert report["accounting"]["outliers_reported"] == counts["outliers"]
    groups = (out / f"{stem}-groups.csv").read_text(encoding="utf-8")
    assert groups.splitlines()[0] == ",".join(GROUP_CSV_COLUMNS)
    md = (out / f"{stem}-report.md").read_text(encoding="utf-8")
    assert "DESKTOP_SIMULATION" in md and "DEVICE_PENDING" in md
    figures = list(out.glob(f"{stem}-fig*.png"))
    assert len(figures) == 4
    for fig in figures:
        assert fig.stat().st_size > 3000


def test_cli_exit_zero_on_clean_full_coverage(tmp_path):
    rows = build_rows()
    sim_csv = tmp_path / "clean.csv"
    with open(sim_csv, "w", newline="", encoding="utf-8") as f:
        w = csvmod.writer(f)
        w.writerow(V2_HEADER)
        for r in rows:
            true_m = float(r["distance_true_m"])
            w.writerow(["B02W016-DESKTOP-SIMULATION", r["test_id"],
                         "iPhone SYN (desktop)", "17.0", "true", "0.9", "0.85",
                         "ok", r["mode"], r["condition"],
                         "2026-11-25T10:00:00+00:00", true_m,
                         f"{true_m + 0.01:.3f}", "", "", "0.050", "",
                         true_m, "", "", r["material"], r["lighting"], r["pose"]])
    proc = _run_cli([str(sim_csv), "--checklist", str(B02_CHECKLIST),
                     "--out-dir", str(tmp_path)], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "matrix check: declared 132 vs actual 132 -> MATCH" in proc.stdout
