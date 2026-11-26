"""TM18/TM19: area, perimeter and basic volume measurements.

Pure math, Swift-portable (TM61). Polygon areas via the shoelace
formula on plane-projected vertices; volumes only as EXPLICIT basic
models (extrusion / bounding box) with their assumptions stated -
irregular point-cloud volume is intentionally out of scope here
(declared experimental in the task brief, TM19 advanced PoC).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from techlab.geometry.camera import _require_finite
from techlab.geometry.measurement import Point3
from techlab.geometry.plane import PlaneFit


def polygon_area_2d(vertices: list[tuple[float, float]]) -> float:
    """Signed shoelace area of a planar 2D polygon (positive =
    counter-clockwise). Degenerate: < 3 vertices raises; collinear
    (zero area) returns 0.0 with the caller deciding if that is a
    valid measurement."""
    if len(vertices) < 3:
        raise ValueError("need at least 3 vertices")
    for x, y in vertices:
        _require_finite(x, y)
    s = 0.0
    n = len(vertices)
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def polygon_is_self_intersecting(
        vertices: list[tuple[float, float]]) -> bool:
    """Segment-intersection sweep for simple polygon validation. A
    self-intersecting outline produces a WRONG shoelace area, so the
    measurement UI must refuse it (the user re-draws)."""
    n = len(vertices)
    if n < 3:
        return False

    def cross(o, a, b):
        return ((a[0] - o[0]) * (b[1] - o[1])
                - (a[1] - o[1]) * (b[0] - o[0]))

    def seg_intersects(p1, p2, p3, p4) -> bool:
        d1 = cross(p3, p4, p1)
        d2 = cross(p3, p4, p2)
        d3 = cross(p1, p2, p3)
        d4 = cross(p1, p2, p4)
        if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
            return True
        # collinear-touch cases count as simple adjacency, skip them
        return False

    for i in range(n):
        a1, a2 = vertices[i], vertices[(i + 1) % n]
        for j in range(i + 1, n):
            # skip adjacent edges (share a vertex)
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue
            b1, b2 = vertices[j], vertices[(j + 1) % n]
            if seg_intersects(a1, a2, b1, b2):
                return True
    return False


def project_to_plane_basis(points: list[Point3],
                           fit: PlaneFit) -> list[tuple[float, float]]:
    """Project 3D polygon vertices onto the fitted plane's local 2D
    basis (u, v) so the shoelace formula applies. The basis is built
    from the plane normal (Gram-Schmidt against the world up or x
    axis) - deterministic and Swift-portable."""
    n = fit.normal
    # pick the world axis least aligned with n for stable u
    if abs(n[0]) < 0.9:
        ref = (1.0, 0.0, 0.0)
    else:
        ref = (0.0, 1.0, 0.0)
    # u = normalize(ref - (ref . n) n)
    dot = ref[0] * n[0] + ref[1] * n[1] + ref[2] * n[2]
    u = (ref[0] - dot * n[0], ref[1] - dot * n[1],
         ref[2] - dot * n[2])
    um = math.dist(u, (0, 0, 0))
    if um < 1e-9:
        raise ValueError("degenerate basis")
    u = (u[0] / um, u[1] / um, u[2] / um)
    # v = n x u
    v = (n[1] * u[2] - n[2] * u[1],
         n[2] * u[0] - n[0] * u[2],
         n[0] * u[1] - n[1] * u[0])
    # plane origin: the point on the plane closest to world origin
    nn = (n[0] * fit.d, n[1] * fit.d, n[2] * fit.d)  # -d * n
    out = []
    for p in points:
        _require_finite(*p)
        rel = (p[0] - nn[0], p[1] - nn[1], p[2] - nn[2])
        out.append((rel[0] * u[0] + rel[1] * u[1] + rel[2] * u[2],
                    rel[0] * v[0] + rel[1] * v[1] + rel[2] * v[2]))
    return out


def polygon_area_3d(points: list[Point3],
                    fit: PlaneFit) -> float:
    """Area of a polygon whose vertices lie (approximately) on the
    fitted plane: project to the plane basis, shoelace, abs()."""
    if len(points) < 3:
        raise ValueError("need at least 3 vertices")
    flat = project_to_plane_basis(points, fit)
    return abs(polygon_area_2d(flat))


def perimeter_3d(points: list[Point3]) -> float:
    if len(points) < 3:
        raise ValueError("need at least 3 vertices")
    from techlab.geometry.measurement import distance_3d
    total = 0.0
    n = len(points)
    for i in range(n):
        total += distance_3d(points[i], points[(i + 1) % n])
    return total


# --------------------------------------------------------------- volumes

@dataclass(frozen=True)
class VolumeResult:
    cubic_meters: float
    method: str
    assumptions: list[str]


def extrusion_volume(base_area_sqm: float,
                     height_m: float) -> VolumeResult:
    """Prism/extrusion volume: polygon base x fixed height. The
    assumption list is PART of the result - callers must display it."""
    _require_finite(base_area_sqm, height_m)
    if base_area_sqm <= 0 or height_m <= 0:
        raise ValueError("base area and height must be positive")
    return VolumeResult(base_area_sqm * height_m, "extrusion",
                        ["base is planar", "walls are vertical",
                         "height measured perpendicular to the base"])


def bounding_box_volume(points: list[Point3]) -> VolumeResult:
    """Axis-aligned bounding box volume. HONESTY: this OVERESTIMATES
    any real object; it is an upper-bound envelope, not the object's
    volume. The assumptions say so."""
    if len(points) < 2:
        raise ValueError("need at least 2 points")
    for p in points:
        _require_finite(*p)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    vol = (max(xs) - min(xs)) * (max(ys) - min(ys)) * \
          (max(zs) - min(zs))
    return VolumeResult(vol, "bounding_box",
                        ["axis-aligned envelope - overestimates the "
                         "real volume", "object may not fill the box"])


def cylinder_volume(radius_m: float, height_m: float) -> VolumeResult:
    _require_finite(radius_m, height_m)
    if radius_m <= 0 or height_m <= 0:
        raise ValueError("radius and height must be positive")
    return VolumeResult(math.pi * radius_m ** 2 * height_m, "cylinder",
                        ["cross-section is a circle", "axis is straight",
                         "walls are vertical"])
