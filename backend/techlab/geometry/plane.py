"""TM17: plane fitting (least squares / PCA / RANSAC) + residuals.

Pure math, Swift-portable (TM61). Fits planes to 3D point sets for
wall/floor candidate detection. Every fit reports its residuals so
callers can REFUSE to trust a bad fit - this module never claims a
plane is a wall (that is a caller policy decision with thresholds
tuned on real device data; see TM22).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from techlab.geometry.camera import _require_finite
from techlab.geometry.measurement import Point3


@dataclass(frozen=True)
class PlaneFit:
    normal: Point3          # unit normal (z-component >= 0 by convention)
    d: float                # plane equation: n . p + d = 0
    residual_rms: float     # RMS of point-to-plane distances (m)
    max_residual: float     # worst point distance (m)
    inlier_ratio: float     # RANSAC inliers / total (1.0 for LSQ/PCA)
    method: str             # 'least_squares' | 'pca' | 'ransac'
    used_points: int

    def distance_to(self, p: Point3) -> float:
        n = self.normal
        return (n[0] * p[0] + n[1] * p[1] + n[2] * p[2] + self.d)

    def residuals(self, points: list[Point3]) -> list[float]:
        return [abs(self.distance_to(p)) for p in points]


def _normalize_with_z_up(n: Point3) -> Point3:
    """Flip the normal so its z-component is >= 0 (a floor/wall
    normal should point up out of the surface)."""
    mag = math.dist(n, (0, 0, 0))
    if mag < 1e-12:
        raise ValueError("degenerate plane: zero normal")
    n = (n[0] / mag, n[1] / mag, n[2] / mag)
    if n[2] < 0:
        n = (-n[0], -n[1], -n[2])
    return n


def _plane_d(n: Point3, centroid: Point3) -> float:
    return -(n[0] * centroid[0] + n[1] * centroid[1]
             + n[2] * centroid[2])


def fit_plane_least_squares(points: list[Point3]) -> PlaneFit:
    """Classic LSQ plane: minimize squared perpendicular distances.
    Solves the 3x3 eigenproblem for the smallest eigenvalue via the
    characteristic polynomial of the covariance's adjugate (closed
    form, no numpy). Requires >= 3 non-collinear points."""
    if len(points) < 3:
        raise ValueError("need at least 3 points")
    for p in points:
        _require_finite(*p)
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    cz = sum(p[2] for p in points) / n
    # covariance entries
    xx = sum((p[0] - cx) ** 2 for p in points) / n
    yy = sum((p[1] - cy) ** 2 for p in points) / n
    zz = sum((p[2] - cz) ** 2 for p in points) / n
    xy = sum((p[0] - cx) * (p[1] - cy) for p in points) / n
    xz = sum((p[0] - cx) * (p[2] - cz) for p in points) / n
    yz = sum((p[1] - cy) * (p[2] - cz) for p in points) / n
    # smallest eigenvector of the symmetric 3x3 covariance:
    # S = [[xx,xy,xz],[xy,yy,yz],[xz,yz,zz]]
    # eigenvector for the smallest eigenvalue solves S v = lambda v;
    # for the plane normal we need v for min |S v - lambda v| - use
    # the analytic route: normal = smallest-eigenvalue eigenvector.
    # Closed form via the cross products of rows of (S - lambda I)
    # is messy; instead compute ALL eigenvectors by Jacobi rotation
    # (simple, robust, Swift-portable).
    vals, vecs = _jacobi_eigen3(xx, xy, xz, yy, yz, zz)
    i_min = min(range(3), key=lambda k: vals[k])
    nvec = _normalize_with_z_up(vecs[i_min])
    fit = PlaneFit(normal=nvec, d=_plane_d(nvec, (cx, cy, cz)),
                   residual_rms=0.0, max_residual=0.0,
                   inlier_ratio=1.0, method="least_squares",
                   used_points=n)
    res = fit.residuals(points)
    fit = PlaneFit(fit.normal, fit.d,
                   residual_rms=math.sqrt(sum(r * r for r in res) / n),
                   max_residual=max(res), inlier_ratio=1.0,
                   method="least_squares", used_points=n)
    return fit


def fit_plane_pca(points: list[Point3]) -> PlaneFit:
    """PCA plane = LSQ plane for a centered cloud (both take the
    smallest-eigenvalue direction). Provided as its own entry point
    because callers think in PCA terms; the math is shared."""
    return fit_plane_least_squares(points)


def fit_plane_ransac(points: list[Point3],
                     threshold: float = 0.01,
                     iterations: int = 200,
                     seed: int | None = None) -> PlaneFit:
    """RANSAC: robust to outliers (bad LiDAR returns). threshold in
    meters; iterations bounded. The best model is re-fit by LSQ over
    its inliers and reported with the inlier ratio."""
    if len(points) < 3:
        raise ValueError("need at least 3 points")
    for p in points:
        _require_finite(*p)
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    rng = random.Random(seed)
    best_inliers: list[Point3] = []
    for _ in range(iterations):
        sample = rng.sample(points, 3)
        try:
            cand = fit_plane_least_squares(sample)
        except ValueError:
            continue                      # collinear sample - skip
        inl = [p for p in points
               if abs(cand.distance_to(p)) <= threshold]
        if len(inl) > len(best_inliers):
            best_inliers = inl
    if len(best_inliers) < 3:
        raise ValueError(
            "RANSAC found no consensus plane (threshold too tight or "
            "data has no dominant plane)")
    refit = fit_plane_least_squares(best_inliers)
    inlier_ratio = len(best_inliers) / len(points)
    return PlaneFit(refit.normal, refit.d, refit.residual_rms,
                    refit.max_residual, inlier_ratio, "ransac",
                    len(best_inliers))


# ------------------------------------------------------------- eigen helper

def _jacobi_eigen3(a11: float, a12: float, a13: float, a22: float,
                   a23: float, a33: float) \
        -> tuple[list[float], list[Point3]]:
    """Jacobi eigenvalue decomposition of a symmetric 3x3 matrix
    (row-major arguments: [ [a11,a12,a13],[a12,a22,a23],
    [a13,a23,a33] ]). Returns (eigenvalues, eigenvectors) sorted
    descending. Numerically simple and Swift-portable."""
    m = [[a11, a12, a13], [a12, a22, a23], [a13, a23, a33]]
    v = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(50):                    # plenty for 3x3
        # find the largest off-diagonal
        off = 0.0
        p, q = 0, 1
        for i in range(3):
            for j in range(i + 1, 3):
                if abs(m[i][j]) > off:
                    off = abs(m[i][j])
                    p, q = i, j
        if off < 1e-14:
            break
        app, aqq, apq = m[p][p], m[q][q], m[p][q]
        theta = 0.5 * (aqq - app) / apq if abs(apq) > 1e-300 else 0.0
        sign = 1.0 if theta >= 0 else -1.0
        t = sign / (abs(theta) + math.sqrt(theta * theta + 1.0))
        c = 1.0 / math.sqrt(t * t + 1.0)
        s = t * c
        # rotate rows/cols p, q
        for k in range(3):
            mkp, mkq = m[k][p], m[k][q]
            m[k][p] = mkp * c - mkq * s
            m[k][q] = mkp * s + mkq * c
        for k in range(3):
            mpk, mqk = m[p][k], m[q][k]
            m[p][k] = mpk * c - mqk * s
            m[q][k] = mpk * s + mqk * c
        for k in range(3):
            vkp, vkq = v[k][p], v[k][q]
            v[k][p] = vkp * c - vkq * s
            v[k][q] = vkp * s + vkq * c
    vals = [m[0][0], m[1][1], m[2][2]]
    order = sorted(range(3), key=lambda i: -vals[i])
    evecs = []
    for i in order:
        col = (v[0][i], v[1][i], v[2][i])
        evecs.append(col)
    return [vals[i] for i in order], evecs


# --------------------------------------------------- surface classification

def classify_surface(fit: PlaneFit,
                     up_vector: Point3 = (0.0, 0.0, 1.0),
                     tolerance_deg: float = 15.0) -> str:
    """Coarse candidate labeling from the fitted normal ONLY. This is
    a geometry observation, never a semantic claim: 'wall_candidate'
    means 'the plane is plumb within tolerance', not 'this is a wall'.

    NOTE: PlaneFit normals carry a z-up convention, so horizontal
    planes report 'horizontal_candidate' - telling floor from ceiling
    needs the camera pose context (which side the device was on),
    which the caller supplies. Thresholds are engineering defaults
    pending real-device tuning (TM22 calibration)."""
    if not (0 <= tolerance_deg < 45):
        raise ValueError("tolerance out of range")
    _require_finite(*up_vector, *fit.normal)
    from techlab.geometry.measurement import angle_between_vectors
    tilt = angle_between_vectors(fit.normal, up_vector)
    if tilt <= tolerance_deg:
        return "horizontal_candidate"          # normal along +up
    if tilt >= 180.0 - tolerance_deg:
        return "ceiling_candidate"              # normal along -up
    if abs(tilt - 90.0) <= tolerance_deg:
        return "wall_candidate"                # normal perpendicular
    return "inclined_candidate"
