"""W003 - cross-platform vector consistency tests.

Loads test-vectors/tech-lab/*.json (the files Mac will consume) and
re-verifies EVERY case against the Python implementation: if this
passes, any disagreement on the Mac side is a Mac bug (and vice versa).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from techlab.ar_persistence import (
    ARExperimentError,
    ARFrameRecord,
    CameraIntrinsics,
    CameraPose,
    FocalUnits,
    Measurement3D,
    project_world_to_pixel,
)
from techlab.drawing_pins import (
    CalibrationError,
    CalibrationPair,
    SheetCalibration,
    SheetPoint,
    WorldPoint,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
VECTORS = REPO_ROOT / "test-vectors" / "tech-lab"


def _load(name: str) -> dict:
    path = VECTORS / name
    if not path.exists():
        pytest.fail(f"vector file missing: {path} - run "
                    f"tools/tech-lab/validation/gen_cross_vectors.py")
    return json.loads(path.read_text(encoding="utf-8"))


def test_t07_vectors_match_python_implementation():
    data = _load("t07-calibration-vectors.json")
    for case in data["cases"]:
        if case.get("expected") == "raise":
            pairs = [CalibrationPair(sheet=SheetPoint(*s), world=WorldPoint(*w))
                     for s, w in zip(case["sheet_points"], case["world_points"])]
            with pytest.raises((CalibrationError, Exception)):
                SheetCalibration(pairs=pairs).fit()
            continue
        pairs = [CalibrationPair(sheet=SheetPoint(*s), world=WorldPoint(*w))
                 for s, w in zip(case["sheet_points"], case["world_points"])]
        M, t, residual = SheetCalibration(pairs=pairs).fit()
        assert M == pytest.approx(case["expected_M"], abs=1e-9)
        assert t == pytest.approx(case["expected_t"], abs=1e-9)
        probe = case["probe"]
        wx = (M[0] * probe["sheet"][0] + M[1] * probe["sheet"][1] + t[0])
        wy = (M[2] * probe["sheet"][0] + M[3] * probe["sheet"][1] + t[1])
        assert (wx, wy) == pytest.approx(probe["expected_world"], abs=1e-9)


def test_t08_vectors_match_python_implementation():
    data = _load("t08-projection-vectors.json")
    for case in data["cases"]:
        intr = CameraIntrinsics(**case["intrinsics"])
        pose = CameraPose(**case["pose"])
        record = ARFrameRecord(photo_id="vec", session_id="vec",
                                intrinsics=intr, pose=pose)
        pt = case["world_point"]
        if case.get("expected") == "raise":
            with pytest.raises(ARExperimentError):
                project_world_to_pixel(record, Measurement3D(x=pt[0], y=pt[1], z=pt[2]))
            continue
        u, v = project_world_to_pixel(
            record, Measurement3D(x=pt[0], y=pt[1], z=pt[2]))
        assert (u, v) == pytest.approx(case["expected_uv"], abs=1e-6)


def test_record_schemas_exported():
    data = _load("record-schemas.json")
    assert "t08_ar_frame_record" in data
    assert "t07_drawing_sheet" in data
    # spot-check schema essentials for the Mac Codable author
    ar = data["t08_ar_frame_record"]
    assert "CameraIntrinsics" in ar["$defs"]
    assert "FocalUnits" in ar["$defs"]
    assert "enum" in ar["$defs"]["FocalUnits"]       # explicit units!


def test_t08_vectors_pin_declared_units():
    """Every vector must carry an explicit units field (the W003 review
    fix): a vector without it would let the Mac side guess again."""
    data = _load("t08-projection-vectors.json")
    for case in data["cases"]:
        if "intrinsics" in case:
            assert case["intrinsics"].get("units") in ("pixels", "mm_35mm_eq"), (
                f"case {case['case']}: units not declared - heuristic "
                f"regression")
