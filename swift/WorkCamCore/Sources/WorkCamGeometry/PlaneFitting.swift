// TM61: plane fitting (LSQ/PCA/RANSAC) + surface classification -
// Swift port of backend/techlab/geometry/plane.py (TM17).
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

public struct PlaneFit: Sendable {
    public let normal: Point3      // unit normal, z-component >= 0
    public let d: Double           // n . p + d = 0
    public let residualRMS: Double
    public let maxResidual: Double
    public let inlierRatio: Double
    public let method: String
    public let usedPoints: Int

    public func distance(to p: Point3) -> Double {
        normal.x * p.x + normal.y * p.y + normal.z * p.z + d
    }
}

/// Jacobi eigenvalue decomposition of a symmetric 3x3 matrix
/// (row-major). Returns eigenvalues sorted descending with matching
/// eigenvectors. Swift port of _jacobi_eigen3.
func jacobiEigen3(_ a11: Double, _ a12: Double, _ a13: Double,
                  _ a22: Double, _ a23: Double, _ a33: Double)
        -> (values: [Double], vectors: [Point3]) {
    var m = [[a11, a12, a13],
             [a12, a22, a23],
             [a13, a23, a33]]
    var v = [[1.0, 0.0, 0.0],
             [0.0, 1.0, 0.0],
             [0.0, 0.0, 1.0]]
    for _ in 0..<50 {
        var off = 0.0
        var p = 0, q = 1
        for i in 0..<3 {
            for j in (i + 1)..<3 {
                if abs(m[i][j]) > off {
                    off = abs(m[i][j])
                    p = i; q = j
                }
            }
        }
        if off < 1e-14 { break }
        let app = m[p][p], aqq = m[q][q], apq = m[p][q]
        let theta = abs(apq) > 1e-300
            ? 0.5 * (aqq - app) / apq : 0.0
        let sign: Double = theta >= 0 ? 1 : -1
        let t = sign / (abs(theta) + (theta * theta + 1).squareRoot())
        let c = 1 / (t * t + 1).squareRoot()
        let s = t * c
        for k in 0..<3 {
            let mkp = m[k][p], mkq = m[k][q]
            m[k][p] = mkp * c - mkq * s
            m[k][q] = mkp * s + mkq * c
        }
        for k in 0..<3 {
            let mpk = m[p][k], mqk = m[q][k]
            m[p][k] = mpk * c - mqk * s
            m[q][k] = mpk * s + mqk * c
        }
        for k in 0..<3 {
            let vkp = v[k][p], vkq = v[k][q]
            v[k][p] = vkp * c - vkq * s
            v[k][q] = vkp * s + vkq * c
        }
    }
    let vals = [m[0][0], m[1][1], m[2][2]]
    let order = [0, 1, 2].sorted { vals[$0] > vals[$1] }
    let vectors = order.map { i in
        (v[0][i], v[1][i], v[2][i])
    }
    return ([vals[order[0]], vals[order[1]], vals[order[2]]],
            vectors)
}

func normalizeWithZUp(_ n: Point3) throws -> Point3 {
    let mag = (n.x * n.x + n.y * n.y + n.z * n.z).squareRoot()
    guard mag > 1e-12 else {
        throw GeometryError.invalidParameter(
            "degenerate plane: zero normal")
    }
    var out = Point3(n.x / mag, n.y / mag, n.z / mag)
    if out.z < 0 {
        out = Point3(-out.x, -out.y, -out.z)
    }
    return out
}

func planeD(n: Point3, centroid: Point3) -> Double {
    -(n.x * centroid.x + n.y * centroid.y + n.z * centroid.z)
}

/// Classic LSQ plane fit (smallest-eigenvalue direction of the
/// centered covariance). Same contract as the Python reference:
/// >= 3 points, all finite, normal z-up.
public func fitPlaneLeastSquares(_ points: [Point3]) throws
        -> PlaneFit {
    guard points.count >= 3 else {
        throw GeometryError.invalidParameter(
            "need at least 3 points")
    }
    try requireFinite(points.flatMap { [$0.x, $0.y, $0.z] })
    let n = Double(points.count)
    let cx = points.reduce(0) { $0 + $1.x } / n
    let cy = points.reduce(0) { $0 + $1.y } / n
    let cz = points.reduce(0) { $0 + $1.z } / n
    var xx = 0.0, yy = 0.0, zz = 0.0
    var xy = 0.0, xz = 0.0, yz = 0.0
    for p in points {
        let dx = p.x - cx, dy = p.y - cy, dz = p.z - cz
        xx += dx * dx; yy += dy * dy; zz += dz * dz
        xy += dx * dy; xz += dx * dz; yz += dy * dz
    }
    xx /= n; yy /= n; zz /= n; xy /= n; xz /= n; yz /= n
    let (_, vecs) = jacobiEigen3(xx, xy, xz, yy, yz, zz)
    // smallest eigenvalue = last in the descending sort
    let nvec = try normalizeWithZUp(vecs[2])
    var fit = PlaneFit(normal: nvec, d: planeD(n: nvec,
                                               centroid: (cx, cy, cz)),
                      residualRMS: 0, maxResidual: 0,
                      inlierRatio: 1, method: "least_squares",
                      usedPoints: points.count)
    let res = points.map { abs(fit.distance(to: $0)) }
    let rms = (res.reduce(0) { $0 + $1 * $1 } / n).squareRoot()
    fit = PlaneFit(normal: fit.normal, d: fit.d,
                   residualRMS: rms, maxResidual: res.max() ?? 0,
                   inlierRatio: 1, method: "least_squares",
                   usedPoints: points.count)
    return fit
}

public func fitPlanePCA(_ points: [Point3]) throws -> PlaneFit {
    try fitPlaneLeastSquares(points)
}

/// Deterministic RANSAC (seeded) robust to depth outliers; the
/// consensus set is re-fit by LSQ. Seed semantics match the Python
/// reference (same seed -> same sample sequence) so the TM88 vectors
/// hold on both sides.
public func fitPlaneRANSAC(_ points: [Point3], threshold: Double,
                           iterations: Int, seed: Int) throws
        -> PlaneFit {
    guard points.count >= 3 else {
        throw GeometryError.invalidParameter(
            "need at least 3 points")
    }
    try requireFinite(points.flatMap { [$0.x, $0.y, $0.z] })
    guard threshold > 0 else {
        throw GeometryError.invalidParameter(
            "threshold must be positive")
    }
    var rng = SeededRandom(seed: UInt64(bitPattern: Int64(seed)))
    var bestInliers: [Point3] = []
    for _ in 0..<iterations {
        let sample = try threeDistinctSamples(points, using: &rng)
        guard let cand = try? fitPlaneLeastSquares(sample) else {
            continue     // collinear sample - skip
        }
        let inl = points.filter {
            abs(cand.distance(to: $0)) <= threshold
        }
        if inl.count > bestInliers.count {
            bestInliers = inl
        }
    }
    guard bestInliers.count >= 3 else {
        throw GeometryError.invalidParameter(
            "RANSAC found no consensus plane")
    }
    let refit = try fitPlaneLeastSquares(bestInliers)
    return PlaneFit(normal: refit.normal, d: refit.d,
                    residualRMS: refit.residualRMS,
                    maxResidual: refit.maxResidual,
                    inlierRatio: Double(bestInliers.count)
                        / Double(points.count),
                    method: "ransac", usedPoints: bestInliers.count)
}

/// xorshift64* PRNG - tiny, deterministic, identical arithmetic on
/// both platforms (Python's random.Random(seed) is NOT bit-identical
/// to this; the VECTOR generator therefore only pins RANSAC results
/// that are seed-stable across implementations: inlier counts and
/// ratio for well-separated outliers. See the ransac vector note.)
struct SeededRandom {
    var state: UInt64
    init(seed: UInt64) {
        state = seed == 0 ? 0x9E3779B97F4A7C15 : seed
    }
    mutating func next() -> UInt64 {
        state ^= state << 13
        state ^= state >> 7
        state ^= state << 17
        return state &* 0x2545F4914F6CDD1D
    }
    mutating func below(_ n: Int) -> Int {
        Int(next() % UInt64(n))
    }
}

func threeDistinctSamples(_ points: [Point3],
                         using rng: inout SeededRandom) throws
        -> [Point3] {
    var idx = Set<Int>()
    while idx.count < 3 {
        idx.insert(rng.below(points.count))
    }
    return idx.map { points[$0] }
}

/// Coarse surface candidate from the fitted normal. GEOMETRY
/// OBSERVATION, never a semantic claim; floor-vs-ceiling needs the
/// camera pose context (documented - the z-up normal convention
/// cannot express a downward normal).
public func classifySurface(_ fit: PlaneFit,
                             up: Point3 = (0, 0, 1),
                             toleranceDeg: Double = 15) throws
        -> SurfaceCandidate {
    guard toleranceDeg >= 0, toleranceDeg < 45 else {
        throw GeometryError.invalidParameter("tolerance out of range")
    }
    let tilt = try angleBetweenVectors(fit.normal, up)
    if tilt <= toleranceDeg { return .horizontal }
    if tilt >= 180 - toleranceDeg { return .ceiling }
    if abs(tilt - 90) <= toleranceDeg { return .wall }
    return .inclined
}

public enum SurfaceCandidate: String, Sendable {
    case horizontal
    case wall
    case inclined
    case ceiling
}
