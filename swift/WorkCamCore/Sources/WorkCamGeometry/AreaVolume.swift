// TM61: areas & basic volumes - Swift port of
// backend/techlab/geometry/area.py (TM18/TM19).
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

/// Signed shoelace area of a 2D polygon (CCW positive).
public func polygonArea2D(_ vertices: [(Double, Double)]) throws
        -> Double {
    guard vertices.count >= 3 else {
        throw GeometryError.invalidParameter(
            "need at least 3 vertices")
    }
    for (x, y) in vertices {
        guard x.isFinite, y.isFinite else {
            throw GeometryError.nonFinite("polygon vertex")
        }
    }
    var s = 0.0
    let n = vertices.count
    for i in 0..<n {
        let (x1, y1) = vertices[i]
        let (x2, y2) = vertices[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    }
    return s / 2
}

/// Segment-sweep validation: a self-intersecting outline yields a
/// wrong shoelace area, so the measurement UI must refuse it.
public func polygonIsSelfIntersecting(
    _ vertices: [(Double, Double)]) -> Bool {
    let n = vertices.count
    guard n >= 3 else { return false }
    func cross(_ o: (Double, Double), _ a: (Double, Double),
               _ b: (Double, Double)) -> Double {
        (a.0 - o.0) * (b.1 - o.1) - (a.1 - o.1) * (b.0 - o.0)
    }
    func intersects(_ p1: (Double, Double), _ p2: (Double, Double),
                    _ p3: (Double, Double), _ p4: (Double, Double))
            -> Bool {
        let d1 = cross(p3, p4, p1)
        let d2 = cross(p3, p4, p2)
        let d3 = cross(p1, p2, p3)
        let d4 = cross(p1, p2, p4)
        return ((d1 > 0) != (d2 > 0)) && ((d3 > 0) != (d4 > 0))
    }
    for i in 0..<n {
        let a1 = vertices[i]
        let a2 = vertices[(i + 1) % n]
        for j in (i + 1)..<n {
            if (j + 1) % n == i || (i + 1) % n == j { continue }
            let b1 = vertices[j]
            let b2 = vertices[(j + 1) % n]
            if intersects(a1, a2, b1, b2) { return true }
        }
    }
    return false
}

/// Local 2D basis of a fitted plane (Gram-Schmidt against the world
/// axis least aligned with the normal) - deterministic, matches the
/// Python reference so polygon areas agree across platforms.
func projectToPlaneBasis(_ points: [Point3], _ fit: PlaneFit)
        throws -> [(Double, Double)] {
    let n = fit.normal
    let ref: Point3 = abs(n.x) < 0.9 ? (1, 0, 0) : (0, 1, 0)
    let dot = ref.x * n.x + ref.y * n.y + ref.z * n.z
    var u = Point3(ref.x - dot * n.x, ref.y - dot * n.y,
                   ref.z - dot * n.z)
    let um = (u.x * u.x + u.y * u.y + u.z * u.z).squareRoot()
    guard um > 1e-9 else {
        throw GeometryError.invalidParameter("degenerate basis")
    }
    u = Point3(u.x / um, u.y / um, u.z / um)
    let v = Point3(n.y * u.z - n.z * u.y,
                   n.z * u.x - n.x * u.z,
                   n.x * u.y - n.y * u.x)
    let origin = Point3(n.x * fit.d, n.y * fit.d, n.z * fit.d)
    return try points.map { p in
        try requireFinite([p.x, p.y, p.z])
        let rel = Point3(p.x - origin.x, p.y - origin.y,
                         p.z - origin.z)
        return (rel.x * u.x + rel.y * u.y + rel.z * u.z,
                rel.x * v.x + rel.y * v.y + rel.z * v.z)
    }
}

/// Area of a near-planar 3D polygon: project to the fitted plane and
/// take the absolute shoelace area.
public func polygonArea3D(_ points: [Point3], fit: PlaneFit) throws
        -> Double {
    guard points.count >= 3 else {
        throw GeometryError.invalidParameter(
            "need at least 3 vertices")
    }
    let flat = try projectToPlaneBasis(points, fit)
    return abs(try polygonArea2D(flat))
}

public func perimeter3D(_ points: [Point3]) throws -> Double {
    guard points.count >= 3 else {
        throw GeometryError.invalidParameter(
            "need at least 3 vertices")
    }
    var total = 0.0
    for i in 0..<points.count {
        total += try distance3D(points[i],
                                points[(i + 1) % points.count])
    }
    return total
}

// MARK: - volumes (basic models with stated assumptions)

public struct VolumeResult: Sendable {
    public let cubicMeters: Double
    public let method: String
    public let assumptions: [String]
}

public func extrusionVolume(baseAreaSqm: Double, heightM: Double)
        throws -> VolumeResult {
    guard baseAreaSqm.isFinite, heightM.isFinite,
          baseAreaSqm > 0, heightM > 0 else {
        throw GeometryError.invalidParameter(
            "base area and height must be positive")
    }
    return VolumeResult(
        cubicMeters: baseAreaSqm * heightM, method: "extrusion",
        assumptions: ["base is planar", "walls are vertical",
                      "height measured perpendicular to the base"])
}

public func boundingBoxVolume(_ points: [Point3]) throws
        -> VolumeResult {
    guard points.count >= 2 else {
        throw GeometryError.invalidParameter(
            "need at least 2 points")
    }
    try requireFinite(points.flatMap { [$0.x, $0.y, $0.z] })
    let xs = points.map(\.x), ys = points.map(\.y),
        zs = points.map(\.z)
    let vol = (xs.max()! - xs.min()!) * (ys.max()! - ys.min()!)
        * (zs.max()! - zs.min()!)
    return VolumeResult(
        cubicMeters: vol, method: "bounding_box",
        assumptions: [
            "axis-aligned envelope - overestimates the real volume",
            "object may not fill the box"])
}

public func cylinderVolume(radiusM: Double, heightM: Double) throws
        -> VolumeResult {
    guard radiusM.isFinite, heightM.isFinite,
          radiusM > 0, heightM > 0 else {
        throw GeometryError.invalidParameter(
            "radius and height must be positive")
    }
    return VolumeResult(
        cubicMeters: .pi * radiusM * radiusM * heightM,
        method: "cylinder",
        assumptions: ["cross-section is a circle",
                      "axis is straight", "walls are vertical"])
}
