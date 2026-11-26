// TM61: geodesy for engineering use - Swift port of
// backend/techlab/geometry/geo.py (TM13).
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

public struct Geodetic: Sendable, Equatable {
    public let latitude: Double
    public let longitude: Double
    public let altitudeM: Double?

    public init(latitude: Double, longitude: Double,
                altitudeM: Double? = nil) throws {
        guard latitude.isFinite, longitude.isFinite else {
            throw GeometryError.nonFinite("geodetic")
        }
        guard abs(latitude) <= 90 else {
            throw GeometryError.invalidParameter("latitude out of range")
        }
        guard abs(longitude) <= 180 else {
            throw GeometryError.invalidParameter("longitude out of range")
        }
        self.latitude = latitude
        self.longitude = longitude
        self.altitudeM = altitudeM
    }
}

// WGS84 constants
let wgs84A = 6_378_137.0
let wgs84F = 1.0 / 298.257223563
let wgs84E2 = wgs84F * (2.0 - wgs84F)

public struct ECEF: Sendable, Equatable {
    public let x: Double, y: Double, z: Double
}

public func geodeticToECEF(_ g: Geodetic) -> ECEF {
    let lat = g.latitude * .pi / 180
    let lon = g.longitude * .pi / 180
    let sinLat = sin(lat), cosLat = cos(lat)
    let n = wgs84A / (1.0 - wgs84E2 * sinLat * sinLat).squareRoot()
    let h = g.altitudeM ?? 0
    return ECEF(
        x: (n + h) * cosLat * cos(lon),
        y: (n + h) * cosLat * sin(lon),
        z: (n * (1 - wgs84E2) + h) * sinLat)
}

public struct ENU: Sendable, Equatable {
    public let east: Double, north: Double, up: Double
}

public func ecefToENU(_ p: ECEF, origin: ECEF,
                      originGeodetic: Geodetic) -> ENU {
    let lat = originGeodetic.latitude * .pi / 180
    let lon = originGeodetic.longitude * .pi / 180
    let dx = p.x - origin.x, dy = p.y - origin.y
    let dz = p.z - origin.z
    let sinLat = sin(lat), cosLat = cos(lat)
    let sinLon = sin(lon), cosLon = cos(lon)
    return ENU(
        east: -sinLon * dx + cosLon * dy,
        north: -sinLat * cosLon * dx - sinLat * sinLon * dy
            + cosLat * dz,
        up: cosLat * cosLon * dx + cosLat * sinLon * dy + sinLat * dz)
}

/// Haversine great-circle distance (sphere model, ~0.3% vs the
/// ellipsoid - fine for site-scale work; the limitation is stated).
public func geodeticDistanceM(_ a: Geodetic, _ b: Geodetic) -> Double {
    let p1 = a.latitude * .pi / 180, l1 = a.longitude * .pi / 180
    let p2 = b.latitude * .pi / 180, l2 = b.longitude * .pi / 180
    let dp = p2 - p1, dl = l2 - l1
    let h = sin(dp / 2) * sin(dp / 2)
        + cos(p1) * cos(p2) * sin(dl / 2) * sin(dl / 2)
    return 2 * wgs84A * asin(min(1.0, h.squareRoot()))
}

/// Initial bearing a -> b in [0, 360).
public func bearingDeg(_ a: Geodetic, _ b: Geodetic) -> Double {
    let p1 = a.latitude * .pi / 180, l1 = a.longitude * .pi / 180
    let p2 = b.latitude * .pi / 180, l2 = b.longitude * .pi / 180
    let dl = l2 - l1
    let y = sin(dl) * cos(p2)
    let x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
    let deg = atan2(y, x) * 180 / .pi
    return (deg + 360).truncatingRemainder(dividingBy: 360)
}

/// Site-local engineering frame around an origin.
public struct LocalFrame: Sendable {
    public let origin: Geodetic
    let originECEF: ECEF

    public init(origin: Geodetic) {
        self.origin = origin
        self.originECEF = geodeticToECEF(origin)
    }

    public func toLocal(_ p: Geodetic) -> ENU {
        ecefToENU(geodeticToECEF(p), origin: originECEF,
                  originGeodetic: origin)
    }
}

/// Polygon geofence (ray casting on lon/lat; site scale, away from
/// the poles - high-latitude sites should use the local frame).
public struct GeoFence: Sendable {
    public let vertices: [Geodetic]

    public init(vertices: [Geodetic]) throws {
        guard vertices.count >= 3 else {
            throw GeometryError.invalidParameter(
                "a fence needs >= 3 vertices")
        }
        self.vertices = vertices
    }

    public func contains(_ p: Geodetic) -> Bool {
        let n = vertices.count
        var inside = false
        var j = n - 1
        for i in 0..<n {
            let vi = vertices[i], vj = vertices[j]
            if (vi.longitude > p.longitude) !=
                    (vj.longitude > p.longitude) {
                let latAt = (vj.latitude - vi.latitude)
                    * (p.longitude - vi.longitude)
                    / (vj.longitude - vi.longitude)
                    + vi.latitude
                if p.latitude < latAt { inside.toggle() }
            }
            j = i
        }
        return inside
    }
}

// MARK: - export

/// GeoJSON FeatureCollection (RFC 7946 position order: LON, LAT,
/// ALT - the order is pinned by the TM88 vectors).
public func toGeoJSON(_ points: [Geodetic]) -> String {
    let features: [[String: Any]] = points.map { g in
        var pos: [Any] = [g.longitude, g.latitude]
        if let alt = g.altitudeM { pos.append(alt) }
        return ["type": "Feature",
                "geometry": ["type": "Point",
                             "coordinates": pos],
                "properties": [String: Any]()]
    }
    let fc: [String: Any] = ["type": "FeatureCollection",
                             "features": features]
    let data = try! JSONSerialization.data(
        withJSONObject: fc,
        options: [.sortedKeys])   // deterministic output
    return String(data: data, encoding: .utf8)!
}

/// Minimal KML 2.2 LineString export.
public func toKML(_ points: [Geodetic], name: String = "WorkCam points")
        -> String {
    let coords = points.map { g -> String in
        var s = "\(g.longitude),\(g.latitude)"
        if let alt = g.altitudeM { s += ",\(alt)" }
        return s
    }.joined(separator: " ")
    return """
    <?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>\
    <name>\(name)</name><Placemark><LineString><coordinates>\
    \(coords)</coordinates></LineString></Placemark>\
    </Document></kml>
    """
}
