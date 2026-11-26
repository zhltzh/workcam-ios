"""TM21: point-cloud processing & denoising (lightweight, portable).

DESIGN RULES (task brief):
  - NO heavy dependencies (no numpy/open3d): every routine is pure
    Python and ports line-by-line to Swift (SIMD-accelerable).
  - k-NN searches here are BRUTE FORCE O(n^2) - fine for the
    measurement-scale clouds WorkCam deals with (hundreds to a few
    thousand points per surface sample). Larger clouds need a
    spatial index (KD-tree); that is an iOS-side optimization to
    make AFTER profiling, not a dependency to add now.
  - every filter REPORTS what it removed (counts + reason list) -
    denoising is measurement preprocessing, and silently swallowed
    points are silently swallowed evidence.

Provided:
  voxel_downsample        - grid-average thinning
  statistical_outlier_removal - k-NN distance z-score filter
  radius_outlier_removal  - neighbor-count filter
  confidence_filter       - per-point depth-confidence gate
  median_filter           - k-NN coordinate median smoothing
  normal_estimation      - plane-fit normals via covariance (TM17)
  segment_clusters         - connectivity-based region splitting
  edge_protect_smooth     - smoothing that skips high-curvature
                            points (SIMPLIFIED - see docstring)
  to_ply                  - ASCII PLY export (xyz [+ normals]
                            [+ confidence])
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from techlab.geometry.camera import _require_finite
from techlab.geometry.measurement import Point3
from techlab.geometry.plane import _jacobi_eigen3

Point = tuple[float, float, float]


@dataclass(frozen=True)
class CloudPoint:
    """A point with optional depth confidence (ARKit's confidenceMap
    is 0..1; some pipelines return LiDAR return intensity instead)."""
    x: float
    y: float
    z: float
    confidence: float | None = None

    @property
    def xyz(self) -> Point:
        return (self.x, self.y, self.z)


@dataclass
class FilterReport:
    """Every filtering call returns one - the removed are COUNTED and
    NAMED, never silently dropped."""
    kept: int
    removed: int
    reasons: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.kept + self.removed


def _validate(points: list[CloudPoint]) -> None:
    if not points:
        raise ValueError("empty cloud")
    for p in points:
        _require_finite(p.x, p.y, p.z)
        if p.confidence is not None:
            _require_finite(p.confidence)


# ------------------------------------------------------------ k-NN helper

def _knn(point: Point, cloud: list[Point], k: int) -> list[int]:
    """Brute-force k nearest neighbor indices (O(n log n) via sort).
    k < n required; ties resolve by index (deterministic across
    platforms - no dict/set iteration anywhere)."""
    n = len(cloud)
    if not 1 <= k < n:
        raise ValueError(f"k must be in 1..{n - 1}")
    scored = []
    for i, c in enumerate(cloud):
        d = math.dist(point, c)
        scored.append((d, i))
    scored.sort()
    return [i for _, i in scored[:k]]


# ------------------------------------------------------------ downsampling

def voxel_downsample(points: list[CloudPoint],
                     voxel_size: float) -> tuple[list[CloudPoint],
                                                 FilterReport]:
    """Average the points of each occupied voxel into ONE centroid.

    Centroid (not first-point) is used: averaging is the documented
    behavior on BOTH sides of the port and the golden vectors pin
    it. Confidence of the centroid = mean of its members."""
    _validate(points)
    if voxel_size <= 0 or not math.isfinite(voxel_size):
        raise ValueError("voxel_size must be positive and finite")
    buckets: dict[tuple[int, int, int], list[CloudPoint]] = {}
    for p in points:
        key = (math.floor(p.x / voxel_size),
               math.floor(p.y / voxel_size),
               math.floor(p.z / voxel_size))
        buckets.setdefault(key, []).append(p)
    out: list[CloudPoint] = []
    for key in sorted(buckets):          # deterministic order
        members = buckets[key]
        n = len(members)
        cx = sum(m.x for m in members) / n
        cy = sum(m.y for m in members) / n
        cz = sum(m.z for m in members) / n
        conf = None
        if all(m.confidence is not None for m in members):
            conf = sum(m.confidence for m in members) / n  # type: ignore
        out.append(CloudPoint(cx, cy, cz, conf))
    removed = len(points) - len(out)
    return out, FilterReport(kept=len(out), removed=removed, reasons=[
        f"voxel grid {voxel_size:g}m: {len(buckets)} occupied "
        f"voxels, {removed} points merged into centroids"])


# ------------------------------------------------------------- outlier filters

def statistical_outlier_removal(
        points: list[CloudPoint], k: int = 8,
        sigma_factor: float = 2.0) -> tuple[list[CloudPoint],
                                            FilterReport]:
    """Classic SOR: a point whose mean k-NN distance is more than
    mean + sigma_factor * stddev away from the cloud's mean k-NN
    distance is removed. Report carries both thresholds."""
    _validate(points)
    if sigma_factor <= 0:
        raise ValueError("sigma_factor must be positive")
    cloud = [p.xyz for p in points]
    nn_dists: list[float] = []
    for i, c in enumerate(cloud):
        idx = _knn(c, cloud, min(k, len(cloud) - 1))
        nn_dists.append(sum(math.dist(c, cloud[j]) for j in idx)
                        / len(idx))
    n = len(nn_dists)
    mean = sum(nn_dists) / n
    var = sum((d - mean) ** 2 for d in nn_dists) / n
    std = math.sqrt(var)
    threshold = mean + sigma_factor * std
    kept: list[CloudPoint] = []
    removed = 0
    for p, d in zip(points, nn_dists):
        if d <= threshold:
            kept.append(p)
        else:
            removed += 1
    return kept, FilterReport(kept=len(kept), removed=removed, reasons=[
        f"k-NN (k={min(k, len(cloud) - 1)}) mean distance "
        f"{mean:.4f} + {sigma_factor:g}σ ({std:.4f}) = "
        f"{threshold:.4f}m; {removed} points beyond"])


def radius_outlier_removal(points: list[CloudPoint], radius: float,
                           min_neighbors: int = 5
                           ) -> tuple[list[CloudPoint], FilterReport]:
    """Remove points with fewer than min_neighbors neighbors within
    radius (isolated speckle / stray LiDAR returns)."""
    _validate(points)
    if radius <= 0:
        raise ValueError("radius must be positive")
    if min_neighbors < 1:
        raise ValueError("min_neighbors must be >= 1")
    cloud = [p.xyz for p in points]
    kept: list[CloudPoint] = []
    removed_idx: list[int] = []
    for i, c in enumerate(cloud):
        count = 0
        for j, other in enumerate(cloud):
            if j != i and math.dist(c, other) <= radius:
                count += 1
        if count >= min_neighbors:
            kept.append(points[i])
        else:
            removed_idx.append(i)
    return kept, FilterReport(
        kept=len(kept), removed=len(removed_idx),
        reasons=[f"radius {radius:g}m needs >= {min_neighbors} "
                 f"neighbors; {len(removed_idx)} isolated points "
                 f"removed"])


def confidence_filter(points: list[CloudPoint],
                      min_confidence: float = 0.5
                      ) -> tuple[list[CloudPoint], FilterReport]:
    """Gate on per-point depth confidence. Points WITHOUT a
    confidence value are KEPT (absence of a quality signal is not
    evidence of bad quality - it is absence)."""
    _validate(points)
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be in [0, 1]")
    kept, removed = [], 0
    for p in points:
        if p.confidence is None or p.confidence >= min_confidence:
            kept.append(p)
        else:
            removed += 1
    return kept, FilterReport(kept=len(kept), removed=removed, reasons=[
        f"confidence gate {min_confidence:g}; {removed} low-confidence "
        f"points removed (no-confidence points kept)"])


# ------------------------------------------------------------- smoothing

def median_filter(points: list[CloudPoint], k: int = 6
                  ) -> tuple[list[CloudPoint], FilterReport]:
    """Coordinate-median smoothing over the k nearest neighbors
    (INCLUDING the point itself). Moves every point; the report
    records the max displacement so callers can bound the effect."""
    _validate(points)
    cloud = [p.xyz for p in points]
    out: list[CloudPoint] = []
    max_disp = 0.0
    for i, c in enumerate(cloud):
        idx = _knn(c, cloud, min(k, len(cloud) - 1))
        idx.append(i)                       # include self
        xs = sorted(cloud[j][0] for j in idx)
        ys = sorted(cloud[j][1] for j in idx)
        zs = sorted(cloud[j][2] for j in idx)
        mid = len(idx) // 2
        med = (xs[mid], ys[mid], zs[mid])
        max_disp = max(max_disp, math.dist(c, med))
        out.append(CloudPoint(med[0], med[1], med[2],
                              points[i].confidence))
    return out, FilterReport(kept=len(out), removed=0, reasons=[
        f"median filter (k={min(k, len(cloud) - 1)}); max point "
        f"displacement {max_disp:.4f}m"])


def edge_protect_smooth(points: list[CloudPoint], k: int = 6,
                        normal_angle_limit_deg: float = 25.0
                        ) -> tuple[list[CloudPoint], FilterReport]:
    """SIMPLIFIED edge protection: a point is smoothed (median over
    its k-NN) ONLY when its neighborhood is locally planar (the
    neighbors' normals agree within normal_angle_limit_deg). Near
    edges/discontinuities the neighborhood normal spread is high and
    the point is left UNTOUCHED.

    HONEST LIMITATION (stated, per the task's honesty rules): this
    is a curvature heuristic on point data only - real edge-preserving
    smoothing also needs the depth IMAGE structure; the full version
    belongs to the Mac/image-domain side. Tests pin the heuristic."""
    _validate(points)
    cloud = [p.xyz for p in points]
    normals: list[Point | None] = []
    for i, c in enumerate(cloud):
        idx = _knn(c, cloud, min(k, len(cloud) - 1))
        idx.append(i)
        try:
            fit_n = _normal_from_points([cloud[j] for j in idx])
            normals.append(fit_n)
        except ValueError:
            normals.append(None)      # degenerate neighborhood
    out: list[CloudPoint] = []
    smoothed = 0
    skipped = 0
    for i, c in enumerate(cloud):
        idx = _knn(c, cloud, min(k, len(cloud) - 1))
        neighbor_normals = [normals[j] for j in idx]
        valid = [nn for nn in neighbor_normals if nn is not None]
        planar = False
        if len(valid) >= 2 and normals[i] is not None:
            from techlab.geometry.measurement import angle_between_vectors
            max_dev = max(angle_between_vectors(normals[i], v)
                          for v in valid)      # type: ignore[arg-type]
            planar = max_dev <= normal_angle_limit_deg
        if planar:
            xs = sorted(cloud[j][0] for j in idx)
            ys = sorted(cloud[j][1] for j in idx)
            zs = sorted(cloud[j][2] for j in idx)
            mid = len(idx) // 2
            out.append(CloudPoint(xs[mid], ys[mid], zs[mid],
                                  points[i].confidence))
            smoothed += 1
        else:
            out.append(points[i])
            skipped += 1
    return out, FilterReport(kept=len(out), removed=0, reasons=[
        f"edge-protect smooth: {smoothed} smoothed on planar "
        f"neighborhoods, {skipped} left untouched (heuristic; image-"
        f"domain edge handling is the Mac half)"])


def _normal_from_points(pts: list[Point]) -> Point:
    """Smallest-eigenvalue direction of the neighborhood covariance
    (reuses the TM17 Jacobi eigen machinery)."""
    if len(pts) < 3:
        raise ValueError("need >= 3 points")
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    cz = sum(p[2] for p in pts) / n
    xx = sum((p[0] - cx) ** 2 for p in pts) / n
    yy = sum((p[1] - cy) ** 2 for p in pts) / n
    zz = sum((p[2] - cz) ** 2 for p in pts) / n
    xy = sum((p[0] - cx) * (p[1] - cy) for p in pts) / n
    xz = sum((p[0] - cx) * (p[2] - cz) for p in pts) / n
    yz = sum((p[1] - cy) * (p[2] - cz) for p in pts) / n
    vals, vecs = _jacobi_eigen3(xx, xy, xz, yy, yz, zz)
    n_vec = vecs[2]                      # smallest eigenvalue
    mag = math.dist(n_vec, (0, 0, 0))
    if mag < 1e-12:
        raise ValueError("degenerate neighborhood")
    if n_vec[2] < 0:                    # z-up convention (TM17)
        n_vec = (-n_vec[0], -n_vec[1], -n_vec[2])
    return (n_vec[0] / mag, n_vec[1] / mag, n_vec[2] / mag)


# ------------------------------------------------------------- normals

def normal_estimation(points: list[CloudPoint], k: int = 8
                      ) -> list[Point | None]:
    """Per-point normal via k-NN covariance plane fit. Degenerate
    neighborhoods yield None (recorded, never guessed)."""
    _validate(points)
    cloud = [p.xyz for p in points]
    out: list[Point | None] = []
    for i, c in enumerate(cloud):
        idx = _knn(c, cloud, min(k, len(cloud) - 1))
        try:
            out.append(_normal_from_points([cloud[j] for j in idx]))
        except ValueError:
            out.append(None)
    return out


# ------------------------------------------------------------- segmentation

def segment_clusters(points: list[CloudPoint],
                     max_dist: float = 0.05
                     ) -> list[list[CloudPoint]]:
    """Connectivity-based segmentation: two points belong to one
    cluster when a chain of <= max_dist hops connects them (union-
    find over the brute-force neighbor graph). Deterministic order
    (index-scan union, clusters emitted in first-member order)."""
    _validate(points)
    if max_dist <= 0:
        raise ValueError("max_dist must be positive")
    n = len(points)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    cloud = [p.xyz for p in points]
    for i in range(n):
        for j in range(i + 1, n):
            if math.dist(cloud[i], cloud[j]) <= max_dist:
                union(i, j)
    clusters: dict[int, list[CloudPoint]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(points[i])
    return list(clusters.values())


# ------------------------------------------------------------- PLY export

def to_ply(points: list[CloudPoint], normals: list[Point | None]
            | None = None, comment: str = "") -> str:
    """ASCII PLY 1.0 export. Property set depends on inputs (xyz
    [+ nx ny nz] [+ confidence]) so consumers stay free-form; the
    header declares exactly what is present - no hidden columns."""
    _validate(points)
    lines: list[str] = ["ply", "format ascii 1.0"]
    if comment:
        for chunk in comment.splitlines():
            lines.append(f"comment {chunk}")
    has_normals = normals is not None and len(normals) == len(points)
    has_conf = any(p.confidence is not None for p in points)
    if has_conf and any(p.confidence is None for p in points):
        raise ValueError("PLY export needs confidence on EVERY point "
                         "or none - mixed clouds are ambiguous")
    props = ["property float x", "property float y",
             "property float z"]
    if has_normals:
        props += ["property float nx", "property float ny",
                  "property float nz"]
    if has_conf:
        props += ["property float confidence"]
    lines.append(f"element vertex {len(points)}")
    lines += props
    lines.append("end_header")
    for i, p in enumerate(points):
        row = [f"{p.x:.6f}", f"{p.y:.6f}", f"{p.z:.6f}"]
        if has_normals:
            nn = normals[i]           # type: ignore[index]
            row += [f"{nn[0]:.6f}", f"{nn[1]:.6f}", f"{nn[2]:.6f}"]
        if has_conf:
            row.append(f"{p.confidence:.4f}")   # type: ignore[union-attr]
        lines.append(" ".join(row))
    return "\n".join(lines) + "\n"
