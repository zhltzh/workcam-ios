"""TM16/TM20: distance, elevation, angle and slope measurements.

Pure math, Swift-portable (TM61). All quantities in meters / degrees.
Degenerate inputs raise or return explicit invalid markers - never a
silent 0.
"""

from __future__ import annotations

import math

from techlab.geometry.camera import _require_finite

Point3 = tuple[float, float, float]


def distance_3d(a: Point3, b: Point3) -> float:
    """Euclidean 3D distance."""
    _require_finite(*a, *b)
    return math.dist(a, b)


def horizontal_distance(a: Point3, b: Point3) -> float:
    """Ground-projected distance (ignores z)."""
    _require_finite(*a, *b)
    return math.hypot(b[0] - a[0], b[1] - a[1])


def vertical_distance(a: Point3, b: Point3) -> float:
    """Elevation difference b.z - a.z (positive = b is higher)."""
    _require_finite(*a, *b)
    return b[2] - a[2]


def polyline_length(points: list[Point3],
                    horizontal_only: bool = False) -> float:
    """Total length of a multi-segment polyline. < 2 points -> 0 is
    the only honest answer (a single point has no length; raise for
    empty so callers notice)."""
    if not points:
        raise ValueError("empty polyline")
    total = 0.0
    for a, b in zip(points, points[1:]):
        if horizontal_only:
            total += horizontal_distance(a, b)
        else:
            total += distance_3d(a, b)
    return total


def unit(v: Point3) -> Point3:
    _require_finite(*v)
    n = math.dist(v, (0, 0, 0))
    if n < 1e-12:
        raise ValueError("cannot normalize a zero-length vector")
    return (v[0] / n, v[1] / n, v[2] / n)


def angle_between_vectors(a: Point3, b: Point3) -> float:
    """Angle between two 3D vectors in degrees [0, 180]."""
    _require_finite(*a, *b)
    na, nb = math.dist(a, (0, 0, 0)), math.dist(b, (0, 0, 0))
    if na < 1e-12 or nb < 1e-12:
        raise ValueError("angle with a zero-length vector")
    cos_t = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (na * nb)
    cos_t = max(-1.0, min(1.0, cos_t))       # numeric safety
    return math.degrees(math.acos(cos_t))


def angle_between_planes(n1: Point3, n2: Point3) -> float:
    """Dihedral angle between two planes given their normals, in
    degrees [0, 90] (the acute angle between the planes)."""
    theta = angle_between_vectors(n1, n2)
    return min(theta, 180.0 - theta)


def slope_from_vertical_delta(horizontal: float,
                              vertical: float) -> dict:
    """Slope of a line/run from its horizontal run and vertical rise.
    Returns {percent, degrees}; horizontal==0 raises (infinite
    percent slope is a wall, not a number callers can use)."""
    _require_finite(horizontal, vertical)
    if horizontal <= 0:
        raise ValueError(
            f"non-positive horizontal run ({horizontal}) - a slope "
            f"needs a run; vertical faces are not slopes")
    percent = vertical / horizontal * 100.0
    degrees = math.degrees(math.atan2(vertical, horizontal))
    return {"percent": percent, "degrees": degrees}


def slope_from_points(a: Point3, b: Point3) -> dict:
    """Slope between two 3D points using the horizontal plane
    projection of their separation as the run."""
    h = horizontal_distance(a, b)
    v = vertical_distance(a, b)
    return slope_from_vertical_delta(h, v)


def deviation_from_vertical(n: Point3) -> float:
    """How far a surface normal (or column axis) tilts away from
    vertical, in degrees. 0 = perfectly plumb. This is the wall-
    plumbness estimator: fit a plane (TM17), take its normal, feed it
    here."""
    return angle_between_vectors(n, (0.0, 0.0, 1.0))


def deviation_from_horizontal(n: Point3) -> float:
    """Floor-levelness companion: tilt of a normal away from the
    vertical axis' complement (0 = perfectly flat floor)."""
    return 90.0 - deviation_from_vertical(n)


# --------------------------------------------------------- unit conversion

def meters_to_feet(m: float) -> float:
    _require_finite(m)
    return m / 0.3048


def feet_to_meters(ft: float) -> float:
    _require_finite(ft)
    return ft * 0.3048


def sqm_to_sqft(sq_m: float) -> float:
    _require_finite(sq_m)
    return sq_m / (0.3048 ** 2)


def sqft_to_sqm(sq_ft: float) -> float:
    _require_finite(sq_ft)
    return sq_ft * (0.3048 ** 2)
