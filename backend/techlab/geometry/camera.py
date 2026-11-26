"""TM15: depth back-projection & camera coordinate transforms.

Pure-math module (no numpy) so every function ports to Swift
line-by-line (TM61). Conventions follow ARKit/AVFoundation closely so
the Mac side maps 1:1:

  - pixel origin: TOP-LEFT, u right, v down (image space)
  - camera space: x right, y UP, z away from the viewer (ARKit
    right-handed convention with the camera looking down -z)
  - world space: arbitrary rigid transform (R | t) from camera
  - depths: distances along the optical axis (z), meters

Every function raises ValueError on non-finite input or degenerate
parameters (TM61 edge-case contract: NaN/Inf/zero-size never slide
through silently).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_EPS = 1e-12


def _require_finite(*vals: float) -> None:
    for v in vals:
        if not math.isfinite(v):
            raise ValueError(f"non-finite value: {v}")


@dataclass(frozen=True)
class CameraIntrinsics:
    """Pinhole intrinsics + image dimensions."""
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    def __post_init__(self):
        if self.fx <= 0 or self.fy <= 0:
            raise ValueError("focal lengths must be positive")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("image dimensions must be positive")
        _require_finite(self.fx, self.fy, self.cx, self.cy)

    def pixel_to_normalized(self, u: float, v: float) -> tuple[float, float]:
        """Image pixel -> normalized device coordinates (x, y) in
        [-1, 1] (Metal texture convention, matches iOS)."""
        _require_finite(u, v)
        x = (2.0 * u - self.width) / self.width
        y = 1.0 - (2.0 * v) / self.height
        return x, y

    def backproject(self, u: float, v: float,
                    depth: float) -> tuple[float, float, float]:
        """Pixel + optical-axis depth -> camera-space 3D point.

        ARKit's depth API: sceneDepth.depthMap holds z-distances.
        The ray through (u, v) is scaled so its z component equals
        depth - NOT the euclidean ray length (pinhole model)."""
        _require_finite(u, v, depth)
        if depth <= 0:
            raise ValueError(f"depth must be positive, got {depth}")
        x = (u - self.cx) / self.fx * depth
        y = (self.cy - v) / self.fy * depth     # image v is DOWN
        z = -depth                                # camera looks -z
        return x, y, z

    def project(self, x: float, y: float,
                z: float) -> tuple[float, float]:
        """Camera-space point -> pixel (inverse of backproject)."""
        _require_finite(x, y, z)
        if z >= -_EPS:
            raise ValueError("point is at/behind the camera plane")
        u = self.cx + self.fx * x / -z
        v = self.cy - self.fy * y / -z
        return u, v


# ------------------------------------------------------------ rotations

def rotation_matrix_axis(axis: str, degrees: float) -> list[list[float]]:
    """3x3 rotation about a principal axis (column-vector convention:
    v' = R @ v). axis in {'x','y','z'}; degrees CCW positive."""
    _require_finite(degrees)
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    if axis == "x":
        return [[1, 0, 0], [0, c, -s], [0, s, c]]
    if axis == "y":
        return [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    if axis == "z":
        return [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    raise ValueError(f"unknown axis {axis!r}")


def mat_vec(m: list[list[float]],
            v: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = v
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z,
            m[1][0] * x + m[1][1] * y + m[1][2] * z,
            m[2][0] * x + m[2][1] * y + m[2][2] * z)


@dataclass(frozen=True)
class RigidTransform:
    """Camera pose: world_point = R @ camera_point + t."""
    r: list[list[float]]
    t: tuple[float, float, float]

    def camera_to_world(self, p: tuple[float, float, float]) \
            -> tuple[float, float, float]:
        _require_finite(*p)
        out = mat_vec(self.r, p)
        return (out[0] + self.t[0], out[1] + self.t[1],
                out[2] + self.t[2])


# ------------------------------------------------- orientation transforms

ORIENTATIONS = ("up", "down", "left", "right", "portrait",
                "portraitUpsideDown", "landscapeLeft",
                "landscapeRight")


def oriented_pixel_to_upright(u: float, v: float, width: int,
                               height: int,
                               orientation: str) \
        -> tuple[float, float]:
    """Map a pixel from a CAPTURE-space image (w x h) into the
    corresponding pixel of the UPRIGHT (display) image.

    Apple's EXIF orientation semantics (the source rectangle is the
    RAW sensor buffer, W x H; the result is the upright image whose
    dimensions may swap):
      up (1): identity            -> (W, H)
      down (3): 180                -> (W, H)
      left (8): rotate 90 CW       -> (H, W)
      right (6): rotate 90 CCW     -> (H, W)
    'portrait'/'landscape*' map to the EXIF numbers per AVCapture
    docs (portrait = right(6) for back camera, etc.)."""
    _require_finite(u, v)
    if width <= 0 or height <= 0:
        raise ValueError("non-positive dimensions")
    if orientation == "up" or orientation == "landscapeLeft":
        return u, v
    if orientation == "down" or orientation == "landscapeRight":
        return width - 1 - u, height - 1 - v
    if orientation == "right" or orientation == "portrait":
        # EXIF 6 - rotate the content 90 CCW; upright is H x W
        return (v, width - 1 - u)
    if orientation == "left" or orientation == "portraitUpsideDown":
        # EXIF 8 - rotate the content 90 CW; upright is H x W
        return (height - 1 - v, u)
    raise ValueError(f"unknown orientation {orientation!r}")


def upright_dimensions(width: int, height: int,
                       orientation: str) -> tuple[int, int]:
    if orientation in ("up", "down", "landscapeLeft", "landscapeRight"):
        return width, height
    if orientation in ("left", "right", "portrait", "portraitUpsideDown"):
        return height, width
    raise ValueError(f"unknown orientation {orientation!r}")


# ------------------------------------------------- depth-map size mapping

def depth_pixel_to_color_pixel(du: float, dv: float,
                               depth_width: int, depth_height: int,
                               color_width: int,
                               color_height: int) -> tuple[float, float]:
    """ARKit depth maps are LOWER resolution than the photo. Map a
    depth-map pixel to the corresponding photo pixel (center-based
    linear scaling - the depth map is assumed aligned with the photo
    frame, per ARKit's sceneDepth alignment contract)."""
    if depth_width <= 0 or depth_height <= 0 \
            or color_width <= 0 or color_height <= 0:
        raise ValueError("non-positive dimensions")
    u = (du + 0.5) * color_width / depth_width - 0.5
    v = (dv + 0.5) * color_height / depth_height - 0.5
    return u, v


def sample_depth_nearest(depth: list[list[float]], du: float,
                         dv: float) -> float:
    """Nearest-neighbor depth lookup with explicit bounds handling:
    out-of-range returns NaN (callers MUST treat NaN as invalid - a
    silent clamp would fabricate measurements)."""
    if not depth or not depth[0]:
        return float("nan")
    h, w = len(depth), len(depth[0])
    i, j = int(round(dv)), int(round(du))
    if i < 0 or i >= h or j < 0 or j >= w:
        return float("nan")
    val = depth[i][j]
    if not math.isfinite(val) or val <= 0:
        return float("nan")     # ARKit uses NaN for invalid pixels
    return val


# ---------------------------------------------------- crop / scale mapping

@dataclass(frozen=True)
class CropRect:
    """Normalized crop (0..1, top-left origin) as AVFoundation and
    CIFilter use: relative to the ORIGINAL image."""
    x: float
    y: float
    w: float
    h: float

    def __post_init__(self):
        for name in ("x", "y", "w", "h"):
            _require_finite(getattr(self, name))
        if self.w <= 0 or self.h <= 0:
            raise ValueError("crop size must be positive")
        if self.x < 0 or self.y < 0 or self.x + self.w > 1 + _EPS \
                or self.y + self.h > 1 + _EPS:
            raise ValueError("crop rect out of the normalized frame")

    def original_pixel_to_cropped(self, u: float, v: float,
                                  width: int,
                                  height: int) -> tuple[float, float]:
        """Original-image pixel -> pixel inside the cropped image."""
        _require_finite(u, v)
        cu = (u / width - self.x) / self.w
        cv = (v / height - self.y) / self.h
        if cu < 0 or cu > 1 or cv < 0 or cv > 1:
            raise ValueError(
                f"pixel ({u},{v}) lies outside the crop - callers "
                f"must check bounds before mapping")
        out_w = max(1, int(round(self.w * width)))
        out_h = max(1, int(round(self.h * height)))
        return cu * (out_w - 1), cv * (out_h - 1)

    def cropped_pixel_to_original(self, cu: float, cv: float,
                                  width: int,
                                  height: int) -> tuple[float, float]:
        """Inverse mapping: cropped-image pixel -> original pixel."""
        _require_finite(cu, cv)
        out_w = max(1, int(round(self.w * width)))
        out_h = max(1, int(round(self.h * height)))
        return (cu / (out_w - 1) * self.w + self.x) * width, \
               (cv / (out_h - 1) * self.h + self.y) * height


def scale_pixel(u: float, v: float, src_w: int, src_h: int,
                dst_w: int,
                dst_h: int) -> tuple[float, float]:
    """Resolution scaling pixel map (center-based, same as the depth
    mapping contract)."""
    if min(src_w, src_h, dst_w, dst_h) <= 0:
        raise ValueError("non-positive dimensions")
    return ((u + 0.5) * dst_w / src_w - 0.5,
            (v + 0.5) * dst_h / src_h - 0.5)
