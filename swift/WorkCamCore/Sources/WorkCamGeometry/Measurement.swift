// TM61: distances, angles, slopes, units - Swift port of
// backend/techlab/geometry/measurement.py (TM16/TM20).
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

public typealias Point3 = (x: Double, y: Double, z: Double)

public func requireFinite(_ v: [Double]) throws {
    guard v.allSatisfy({ $0.isFinite }) else {
        throw GeometryError.nonFinite("measurement input")
    }
}

public func distance3D(_ a: Point3, _ b: Point3) throws -> Double {
    try requireFinite([a.x, a.y, a.z, b.x, b.y, b.z])
    let dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z
    return (dx * dx + dy * dy + dz * dz).squareRoot()
}

public func horizontalDistance(_ a: Point3, _ b: Point3) throws
        -> Double {
    try requireFinite([a.x, a.y, a.z, b.x, b.y, b.z])
    let dx = b.x - a.x, dy = b.y - a.y
    return (dx * dx + dy * dy).squareRoot()
}

/// b.z - a.z (positive = b is higher).
public func verticalDistance(_ a: Point3, _ b: Point3) throws
        -> Double {
    try requireFinite([a.z, b.z])
    return b.z - a.z
}

public func polylineLength(_ points: [Point3],
                           horizontalOnly: Bool = false) throws
        -> Double {
    guard !points.isEmpty else {
        throw GeometryError.invalidParameter("empty polyline")
    }
    var total = 0.0
    for i in 0..<(points.count - 1) {
        total += horizontalOnly
            ? try horizontalDistance(points[i], points[i + 1])
            : try distance3D(points[i], points[i + 1])
    }
    return total
}

public func angleBetweenVectors(_ a: Point3, _ b: Point3) throws
        -> Double {
    try requireFinite([a.x, a.y, a.z, b.x, b.y, b.z])
    let na = (a.x * a.x + a.y * a.y + a.z * a.z).squareRoot()
    let nb = (b.x * b.x + b.y * b.y + b.z * b.z).squareRoot()
    guard na > 1e-12, nb > 1e-12 else {
        throw GeometryError.invalidParameter(
            "angle with a zero-length vector")
    }
    var cosT = (a.x * b.x + a.y * b.y + a.z * b.z) / (na * nb)
    cosT = max(-1.0, min(1.0, cosT))
    return acos(cosT) * 180 / .pi
}

/// Dihedral angle between two planes given their normals: the ACUTE
/// angle between the planes, in [0, 90].
public func angleBetweenPlanes(n1: Point3, n2: Point3) throws
        -> Double {
    let theta = try angleBetweenVectors(n1, n2)
    return min(theta, 180 - theta)
}

public struct SlopeResult: Sendable, Equatable {
    public let percent: Double
    public let degrees: Double
}

public func slopeFromVerticalDelta(horizontal: Double,
                                   vertical: Double) throws
        -> SlopeResult {
    try requireFinite([horizontal, vertical])
    guard horizontal > 0 else {
        throw GeometryError.invalidParameter(
            "non-positive horizontal run - a slope needs a run; " +
            "vertical faces are not slopes")
    }
    let percent = vertical / horizontal * 100
    let degrees = atan2(vertical, horizontal) * 180 / .pi
    return SlopeResult(percent: percent, degrees: degrees)
}

public func slopeFromPoints(_ a: Point3, _ b: Point3) throws
        -> SlopeResult {
    let h = try horizontalDistance(a, b)
    let v = try verticalDistance(a, b)
    return try slopeFromVerticalDelta(horizontal: h, vertical: v)
}

/// Wall plumbness: tilt of a surface normal away from vertical.
public func deviationFromVertical(_ n: Point3) throws -> Double {
    try angleBetweenVectors(n, (0, 0, 1))
}

/// Floor levelness companion: 90 - plumbness of the normal.
public func deviationFromHorizontal(_ n: Point3) throws -> Double {
    90 - (try deviationFromVertical(n))
}

// MARK: - Unit conversion (exact international foot)

public func metersToFeet(_ m: Double) -> Double { m / 0.3048 }
public func feetToMeters(_ ft: Double) -> Double { ft * 0.3048 }
public func sqmToSqft(_ sqm: Double) -> Double { sqm / (0.3048 * 0.3048) }
public func sqftToSqm(_ sqft: Double) -> Double { sqft * 0.3048 * 0.3048 }
