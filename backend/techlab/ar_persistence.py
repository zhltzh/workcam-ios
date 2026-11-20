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

from pydantic import BaseModel, Field, field_validator


class ARExperimentError(ValueError):
    pass


class CameraIntrinsics(BaseModel):
    fx: float = Field(gt=0)
    fy: float = Field(gt=0)
    cx: float = Field(ge=0)
    cy: float = Field(ge=0)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)

    @field_validator("fx", "fy")
    @classmethod
    def _plausible_focal(cls, v: float) -> float:
        # iPhone main camera: ~1500-6000 px focal length. Values below
        # ~300 are almost certainly unconverted ARKit 'mm' units.
        if v < 300:
            raise ARExperimentError(
                f"fx/fy={v} looks like ARKit's raw mm units, not pixels - "
                f"convert before serializing (plausible range >= 300 px)")
        if v > 20000:
            raise ARExperimentError(f"fx/fy={v} exceeds plausible pixel range")
        return v


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
