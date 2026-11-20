"""
W003 - cross-platform test-vector generator for T07 (drawing calibration)
and T08 (AR projection).

Emits JSON vectors under test-vectors/tech-lab/ that a Mac/Swift client
can consume DIRECTLY: every case is pure math with known inputs and
expected outputs computed here; if Swift disagrees with any vector,
one of the two implementations is wrong - that is the point.

T07 vectors: calibration point sets -> expected affine transform
T08 vectors: intrinsics+pose+world point -> expected (u, v) pixel

Run:  python tools/tech-lab/validation/gen_cross_vectors.py [outdir]
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "backend"))

from techlab.ar_persistence import (  # noqa: E402
    ARExperimentError,
    ARFrameRecord,
    CameraIntrinsics,
    CameraPose,
    FocalUnits,
    Measurement3D,
    project_world_to_pixel,
)
from techlab.drawing_pins import (  # noqa: E402
    CalibrationPair,
    SheetCalibration,
    SheetPoint,
    WorldPoint,
)


def _t07_vectors() -> list[dict]:
    """Calibration fits with expected M/t and an independent probe point
    mapped through the fit, plus the rejection cases (messages only -
    Swift must raise too, exact wording is free)."""
    vectors = []

    # case 1: exact scale+offset (1000 sheet units -> 1 m, origin shift)
    pairs = [
        CalibrationPair(sheet=SheetPoint(0, 0), world=WorldPoint(10, 20)),
        CalibrationPair(sheet=SheetPoint(1000, 0), world=WorldPoint(11, 20)),
        CalibrationPair(sheet=SheetPoint(0, 1000), world=WorldPoint(10, 21)),
    ]
    cal = SheetCalibration(pairs=pairs)
    M, t, residual = cal.fit()
    probe_sheet = (500.0, 500.0)
    probe_world = (M[0] * probe_sheet[0] + M[1] * probe_sheet[1] + t[0],
                   M[2] * probe_sheet[0] + M[3] * probe_sheet[1] + t[1])
    vectors.append({
        "case": "scale_offset",
        "sheet_points": [[p.sheet.x, p.sheet.y] for p in pairs],
        "world_points": [[p.world.x, p.world.y] for p in pairs],
        "expected_M": M, "expected_t": t, "expected_residual": residual,
        "probe": {"sheet": probe_sheet, "expected_world": probe_world},
    })

    # case 2: rotation 90deg (sheet y-axis -> world x-axis) + scale 0.5
    pairs2 = [
        CalibrationPair(sheet=SheetPoint(0, 0), world=WorldPoint(0, 0)),
        CalibrationPair(sheet=SheetPoint(0, 200), world=WorldPoint(100, 0)),
        CalibrationPair(sheet=SheetPoint(200, 0), world=WorldPoint(0, -100)),
    ]
    M2, t2, r2 = SheetCalibration(pairs=pairs2).fit()
    probe2 = (50.0, 50.0)
    pw2 = (M2[0] * probe2[0] + M2[1] * probe2[1] + t2[0],
           M2[2] * probe2[0] + M2[3] * probe2[1] + t2[1])
    vectors.append({
        "case": "rotation90_scale0.5",
        "sheet_points": [[p.sheet.x, p.sheet.y] for p in pairs2],
        "world_points": [[p.world.x, p.world.y] for p in pairs2],
        "expected_M": M2, "expected_t": t2, "expected_residual": r2,
        "probe": {"sheet": probe2, "expected_world": pw2},
    })

    # rejection cases: Swift must THROW on these (message text free)
    vectors.append({
        "case": "reject_collinear",
        "sheet_points": [[0, 0], [1, 1], [2, 2]],
        "world_points": [[0, 0], [1, 1], [2, 2]],
        "expected": "raise",
        "reason": "collinear calibration points",
    })
    # NOTE: residual rejection NEEDS 4+ points. The exact 3-point fit
    # absorbs any third-point deviation (zero residual by construction);
    # only a 4th VERIFICATION point outside the fitted plane exposes it.
    vectors.append({
        "case": "reject_residual",
        "sheet_points": [[0, 0], [1000, 0], [0, 1000], [1000, 1000]],
        "world_points": [[0, 0], [1, 0], [0, 1], [2, 2]],   # 4th point off the grid
        "expected": "raise",
        "reason": "4th verification point exceeds max_residual_m (0.05 default)",
    })
    return vectors


def _t08_vectors() -> list[dict]:
    """Projection cases with expected (u, v), each rounded to 1e-9.
    Swift's simd_float3x3/quaternion path must reproduce these."""
    vectors = []

    def project_case(name, intr, pose, point, expect_raise=None):
        record = ARFrameRecord(photo_id="vec", session_id="vec-session",
                               intrinsics=intr, pose=pose)
        try:
            u, v = project_world_to_pixel(record, Measurement3D(x=point[0], y=point[1], z=point[2]))
            vectors.append({
                "case": name,
                "intrinsics": {
                    "fx": intr.fx, "fy": intr.fy, "cx": intr.cx, "cy": intr.cy,
                    "image_width": intr.image_width, "image_height": intr.image_height,
                    "units": intr.units.value,
                },
                "pose": {"tx": pose.tx, "ty": pose.ty, "tz": pose.tz,
                         "qx": pose.qx, "qy": pose.qy, "qz": pose.qz, "qw": pose.qw},
                "world_point": point,
                "expected_uv": [round(u, 9), round(v, 9)],
            })
        except ARExperimentError as e:
            vectors.append({
                "case": name,
                "intrinsics": {
                    "fx": intr.fx, "fy": intr.fy, "cx": intr.cx, "cy": intr.cy,
                    "image_width": intr.image_width, "image_height": intr.image_height,
                    "units": intr.units.value,
                },
                "pose": {"tx": pose.tx, "ty": pose.ty, "tz": pose.tz,
                         "qx": pose.qx, "qy": pose.qy, "qz": pose.qz, "qw": pose.qw},
                "world_point": point,
                "expected": "raise",
                "reason": str(e),
            })

    pixels = CameraIntrinsics(fx=2000.0, fy=2000.0, cx=1920.0, cy=1080.0,
                              image_width=3840, image_height=2160,
                              units=FocalUnits.PIXELS)
    identity = CameraPose(tx=0, ty=0, tz=0, qx=0, qy=0, qz=0, qw=1)

    # 1: identity pose, centered point
    project_case("identity_center", pixels, identity, [0.0, 0.0, 4.0])
    # 2: identity pose, off-axis point
    project_case("identity_offset", pixels, identity, [1.0, -0.5, 4.0])
    # 3: translated camera (t = (0.2, -0.1, 0.5)), point ahead
    t_pose = CameraPose(tx=0.2, ty=-0.1, tz=0.5, qx=0, qy=0, qz=0, qw=1)
    project_case("translated_camera", pixels, t_pose, [1.0, 0.5, 4.0])
    # 4: 90deg yaw (quaternion y-axis): with THIS protocol's +z-forward
    #    convention the rotated camera's front now points toward -x
    yaw90 = CameraPose(tx=0, ty=0, tz=0, qx=0, qy=math.sin(math.pi / 4),
                       qz=0, qw=math.cos(math.pi / 4))
    project_case("yaw90_camera", pixels, yaw90, [-4.0, 0.0, 0.0])
    # 5: combined rotation + translation
    combo = CameraPose(tx=0.5, ty=0.1, tz=-0.2,
                       qx=math.sin(math.pi / 8), qy=0.0,
                       qz=0.0, qw=math.cos(math.pi / 8))
    project_case("rot_x45_translated", pixels, combo, [0.3, 1.2, 5.0])
    # 6: behind-camera => raise
    project_case("behind_camera_rejected", pixels, identity, [0.0, 0.0, -4.0])
    return vectors


def _schemas() -> dict:
    """JSON Schemas of the cross-platform record formats (Mac consumes
    these to generate matching Swift Codable structs)."""
    from techlab.ar_persistence import ARFrameRecord
    from techlab.drawing_pins import DrawingSheet

    schemas = {
        "_description": "W003 cross-platform JSON Schemas (T07/T08)",
        "t08_ar_frame_record": ARFrameRecord.model_json_schema(),
        "t07_drawing_sheet": DrawingSheet.model_json_schema(),
    }
    # strip non-JSON-serializable defs references if present
    return schemas


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        _REPO / "test-vectors" / "tech-lab"
    outdir.mkdir(parents=True, exist_ok=True)

    t07 = {
        "_description": "T07 drawing calibration cross-platform vectors",
        "_usage": "fit an affine from the sheet->world pairs; M/t must "
                  "match expected_M/expected_t to 1e-9; probe maps as "
                  "expected; rejection cases must raise",
        "cases": _t07_vectors(),
    }
    t08 = {
        "_description": "T08 AR projection cross-platform vectors",
        "_usage": "p_camera = R(q) @ p_world + t; u = fx*xc/zc + cx, "
                  "v = fy*yc/zc + cy (pixels); expected_uv to 1e-9; "
                  "behind-camera cases must raise",
        "_convention": "THIS PROTOCOL uses +z as camera-forward "
                       "(ARKit's native camera looks down -z: Mac must "
                       "either negate z consistently or sign-flip the "
                       "pose; the vectors pin whichever convention the "
                       "record declares). Behind-camera = zc <= 0.",
        "cases": _t08_vectors(),
    }
    schemas = _schemas()
    (outdir / "t07-calibration-vectors.json").write_text(
        json.dumps(t07, indent=2, allow_nan=False), encoding="utf-8")
    (outdir / "t08-projection-vectors.json").write_text(
        json.dumps(t08, indent=2, allow_nan=False), encoding="utf-8")
    (outdir / "record-schemas.json").write_text(
        json.dumps(schemas, indent=2, allow_nan=False), encoding="utf-8")
    print(f"written: {outdir}/t07-calibration-vectors.json "
          f"({len(t07['cases'])} cases)")
    print(f"written: {outdir}/t08-projection-vectors.json "
          f"({len(t08['cases'])} cases)")
    print(f"written: {outdir}/record-schemas.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
