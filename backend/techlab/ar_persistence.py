"""T08 - AR measurement & photo-annotation persistence (Wave 3 tech-lab).

PROTOCOL (platform-independent; Mac captures ARKit data, ANY party can
re-verify the math on Windows):

  ARFrameRecord: one AR-assisted capture stores
    - camera intrinsics (fx, fy, cx, cy) in PIXELS (ARKit fx/fy are in
      mm/units - the serializer REQUIRES the caller to convert; the
      record validates plausibility ranges and rejects nonsense)
    - camera pose: world->camera as (position, quaternion) at capture
    - measurements: 3D world points, each with the tracking state at
      the time it was taken

  Reopening a photo reconstructs annotations WITHOUT an AR session:
  project each world point through the SAME intrinsics + pose and get
  the on-image (u, v). Windows re-runs that projection and asserts it
  matches the u/v persisted alongside - if they disagree, the record
  is flagged, not silently trusted.

  Tracking loss honesty: a point taken with tracking state != 'normal'
  is marked tracking_degraded and carries that state into every
  export; degradation NEVER silently disappears.

HONESTY: world coordinates are SESSION-LOCAL (ARKit world origin). Two
sessions are different worlds; cross-session measurements are rejected.
"""

from __future__ import annotations

import math
import uuid
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class ARExperimentError(ValueError):
    pass


class FocalUnits(str, Enum):
    """Explicit unit declaration for camera intrinsics (W003 review fix:
    the Wave-3 PoC used a heuristic 'fx < 300 => mm' guess. Heuristics
    have no place in a cross-platform protocol: a small-fisheye or a
    4K long-focus lens both fall outside naive bounds. The unit is now
    DECLARED by the producer and VALIDATED by range checks per unit.)"""

    PIXELS = "pixels"        # ARKit intrinsics after fx/fy * imageWidth
    MM_35MM_EQ = "mm_35mm_eq"  # 35mm-equivalent focal length (rare, ~14-600)
    UNSET = "unset"          # legacy/default: range-validated but unit unknown


# plausible focal ranges per declared unit (length-inclusive)
_FOCAL_RANGES = {
    FocalUnits.PIXELS: (300.0, 30000.0),      # phone main cam ~1500-8000 px
    FocalUnits.MM_35MM_EQ: (10.0, 1200.0),    # fisheye to super-tele
    # UNSET: legacy records only. Range kept identical to the pixel range
    # so old data still validates, with the error message DEMANDING the
    # unit be declared going forward (the W003 fix: units are explicit).
    FocalUnits.UNSET: (300.0, 30000.0),
}


class CameraIntrinsics(BaseModel):
    fx: float = Field(gt=0)
    fy: float = Field(gt=0)
    cx: float = Field(ge=0)
    cy: float = Field(ge=0)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    units: FocalUnits = FocalUnits.UNSET

    @field_validator("fx", "fy")
    @classmethod
    def _validate_focal(cls, v: float, info) -> float:
        # cross-field validation happens in model_validator (needs units)
        return v

    @model_validator(mode="after")
    def _check_units(self) -> "CameraIntrinsics":
        lo, hi = _FOCAL_RANGES[self.units]
        for name in ("fx", "fy"):
            v = getattr(self, name)
            if not (lo <= v <= hi):
                hint = {
                    FocalUnits.PIXELS: "pixels (ARKit fx * imageWidth)",
                    FocalUnits.MM_35MM_EQ: "35mm-equivalent mm",
                    FocalUnits.UNSET: "UNSET - declare units (pixels or mm_35mm_eq)",
                }[self.units]
                raise ARExperimentError(
                    f"fx/fy={v} outside the plausible range for declared "
                    f"units '{self.units.value}' [{lo}, {hi}] - expected {hint}")
        # principal point must sit inside the image (pixel units only)
        if self.units == FocalUnits.PIXELS:
            if self.cx > self.image_width or self.cy > self.image_height:
                raise ARExperimentError(
                    "principal point (cx, cy) outside image bounds - "
                    "intrinsics are not in pixel units")
        return self


class CameraPose(BaseModel):
    """world->camera transform: camera position in world + orientation
    quaternion (x, y, z, w), ARKit convention."""

    tx: float
    ty: float
    tz: float
    qx: float
    qy: float
    qz: float
    qw: float = Field(ge=-1.0, le=1.0)


class TrackingState(str, Enum):
    NORMAL = "normal"
    LIMITED = "limited"        # ARKit .limited: drift risk
    NOT_AVAILABLE = "not_available"


class Measurement3D(BaseModel):
    point_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    label: str | None = None
    x: float
    y: float
    z: float
    tracking: TrackingState = TrackingState.NORMAL
    persisted_u: float | None = None    # u,v the CAPTURING device computed
    persisted_v: float | None = None

    @property
    def is_degraded(self) -> bool:
        return self.tracking != TrackingState.NORMAL


class ARFrameRecord(BaseModel):
    """One AR-assisted capture with measurements to persist."""

    record_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    photo_id: str
    session_id: str                      # world is SESSION-LOCAL
    intrinsics: CameraIntrinsics
    pose: CameraPose
    measurements: list[Measurement3D] = []

    def add_measurement(self, m: Measurement3D) -> "ARFrameRecord":
        # capture-time projection: also persist what the device computed
        u, v = project_world_to_pixel(self, m)
        m.persisted_u, m.persisted_v = u, v
        self.measurements.append(m)
        return self


def convert_arkit_pose(position, quaternion) -> "CameraPose":
    """Convert an ARKit-native camera transform into THIS protocol's
    convention. Cross-platform contract (pinned by the
    'arkit_conversion' vectors in t08-projection-vectors.json):

      R_protocol = M180x @ R_arkit^T
      t_protocol = -R_protocol @ t_arkit

    where M180x = diag(1, -1, -1) (a 180-deg rotation about X) maps
    ARKit's camera frame (z points BACK, -z is look direction) onto
    ours (z points FORWARD), keeping a right-handed system (no mirror).

    Quaternion closed form (Hamilton product q_180x (x)a* with
    q_180x = (1,0,0, 0)):
      q_protocol = (x=w_a, y=z_a, z=-y_a, w=x_a)

    Args:
      position: ARKit transform translation (x, y, z)
      quaternion: ARKit rotation as (x, y, z, w) - the CAMERA->WORLD
                 orientation, as simd_quaternionf(transform) gives.
    """
    ax, ay, az, aw = quaternion
    # closed form above
    fx, fy, fz, fw = aw, az, -ay, ax
    pose = CameraPose(tx=0.0, ty=0.0, tz=0.0,
                      qx=fx, qy=fy, qz=fz, qw=fw)
    # t_protocol = -R_protocol @ t_arkit
    R = _quat_rotation_matrix(pose)
    tx, ty, tz = position
    px = -(R[0][0] * tx + R[0][1] * ty + R[0][2] * tz)
    py = -(R[1][0] * tx + R[1][1] * ty + R[1][2] * tz)
    pz = -(R[2][0] * tx + R[2][1] * ty + R[2][2] * tz)
    return pose.model_copy(update={"tx": px, "ty": py, "tz": pz})


def _quat_rotation_matrix(pose: CameraPose) -> list[list[float]]:
    """Rotation matrix from quaternion (column-vector convention:
    p_camera = R @ p_world + t)."""
    x, y, z, w = pose.qx, pose.qy, pose.qz, pose.qw
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-9:
        raise ARExperimentError("degenerate quaternion")
    x, y, z, w = x / n, y / n, z / n, w / n
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def project_world_to_pixel(record: ARFrameRecord, m: Measurement3D) -> tuple[float, float]:
    """World point -> image pixel (u, v). Third-party verifiable: the
    ONLY inputs are the persisted record fields."""
    R = _quat_rotation_matrix(record.pose)
    t = (record.pose.tx, record.pose.ty, record.pose.tz)
    cx_ = R[0][0] * m.x + R[0][1] * m.y + R[0][2] * m.z + t[0]
    cy_ = R[1][0] * m.x + R[1][1] * m.y + R[1][2] * m.z + t[1]
    cz_ = R[2][0] * m.x + R[2][1] * m.y + R[2][2] * m.z + t[2]
    if abs(cz_) < 1e-9:
        raise ARExperimentError("point projects onto the camera plane - invalid")
    if cz_ <= 0:
        raise ARExperimentError("point is BEHIND the camera - invalid measurement")
    intr = record.intrinsics
    u = intr.fx * cx_ / cz_ + intr.cx
    v = intr.fy * cy_ / cz_ + intr.cy
    return u, v


def verify_record_projection(record: ARFrameRecord, tolerance_px: float = 0.5
                             ) -> list[str]:
    """Windows-side re-verification of a persisted record: recompute every
    (u, v) and compare with the persisted values. Returns a list of
    problems; empty = record is internally consistent."""
    problems: list[str] = []
    for m in record.measurements:
        if m.persisted_u is None or m.persisted_v is None:
            problems.append(f"{m.point_id}: missing persisted (u, v)")
            continue
        try:
            u, v = project_world_to_pixel(record, m)
        except ARExperimentError as e:
            problems.append(f"{m.point_id}: {e}")
            continue
        if abs(u - m.persisted_u) > tolerance_px or abs(v - m.persisted_v) > tolerance_px:
            problems.append(
                f"{m.point_id}: persisted ({m.persisted_u:.1f}, {m.persisted_v:.1f}) "
                f"differs from recomputed ({u:.1f}, {v:.1f}) by more than "
                f"{tolerance_px} px")
    return problems


def merge_cross_session(a: ARFrameRecord, b: ARFrameRecord) -> ARFrameRecord:
    """Cross-session merges are FORBIDDEN: ARKit world origins differ per
    session; merging would silently mix coordinate frames. This helper
    exists so the product layer has ONE place with the clear error."""
    if a.session_id == b.session_id:
        for m in b.measurements:
            a.add_measurement(m)
        return a
    raise ARExperimentError(
        f"cannot merge measurements across AR sessions "
        f"({a.session_id[:8]} vs {b.session_id[:8]}): world coordinates are "
        f"session-local; re-measure in one session instead")


def degrade_report(record: ARFrameRecord) -> dict:
    """Export-ready honesty block: how many points were captured under
    degraded tracking. Never silently dropped."""
    degraded = [m for m in record.measurements if m.is_degraded]
    return {
        "total_points": len(record.measurements),
        "degraded_points": len(degraded),
        "degraded_point_ids": [m.point_id for m in degraded],
        "note": ("points captured under limited/unavailable tracking have "
                 "higher drift risk" if degraded else "all points tracked normally"),
    }
