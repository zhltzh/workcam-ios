// TM60: location quality - Swift port of
// backend/techlab/location_quality.py (TM12). Real Core Location
// acquisition is INJECTED (Mac adapts); Windows tests use synthetic
// fixes.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation
import WorkCamGeometry

public enum LocationSource: String, Codable, Sendable {
    case gnss
    case manual
    case none
    case unknown
}

public struct LocationSample: Sendable, Equatable {
    public let latitude: Double
    public let longitude: Double
    public let accuracyM: Double?
    public let altitudeM: Double?
    public let timestamp: Date?
    public let source: LocationSource

    public init(latitude: Double, longitude: Double,
                accuracyM: Double?, altitudeM: Double? = nil,
                timestamp: Date? = nil,
                source: LocationSource = .gnss) {
        self.latitude = latitude
        self.longitude = longitude
        self.accuracyM = accuracyM
        self.altitudeM = altitudeM
        self.timestamp = timestamp
        self.source = source
    }

    public var structuralProblems: [String] {
        var p: [String] = []
        if !latitude.isFinite || !longitude.isFinite {
            p.append("non-finite coordinates")
        }
        if !(abs(latitude) <= 90) { p.append("latitude out of range") }
        if !(abs(longitude) <= 180) {
            p.append("longitude out of range")
        }
        if let a = accuracyM, a < 0 {
            p.append("negative accuracy")
        }
        if let alt = altitudeM, abs(alt) > 20000 {
            p.append("altitude beyond plausible range")
        }
        return p
    }
}

public enum AccuracyGrade: String, Sendable {
    case excellent     // <= 5m
    case good          // <= 20m
    case fair          // <= 75m
    case poor          // > 75m
    case none          // manual / missing / ungradeable

    public static func grade(accuracyM: Double?) -> AccuracyGrade {
        guard let a = accuracyM else { return .none }
        switch a {
        case ...5: return .excellent
        case ...20: return .good
        case ...75: return .fair
        default: return .poor
        }
    }
}

public struct LocationRiskReport: Sendable, Equatable {
    public let grade: AccuracyGrade
    public let stale: Bool
    public let staleAgeSeconds: Double?
    public let anomalies: [String]
    public let spoofRiskSignals: [String]
    public static let honestyNote =
        "Risk signals are anomaly prompts for review, NOT proof of " +
        "position authenticity; consumer GNSS remains spoofable and " +
        "an absence of anomalies is not evidence of genuineness."
}

/// Haversine over the sphere (site-scale accuracy; the ellipsoid
/// lives in WorkCamGeometry.geo for map-scale work).
func haversineM(_ lat1: Double, _ lon1: Double,
                _ lat2: Double, _ lon2: Double) -> Double {
    let r = 6_371_000.0
    let p1 = lat1 * .pi / 180, p2 = lat2 * .pi / 180
    let dp = (lat2 - lat1) * .pi / 180
    let dl = (lon2 - lon1) * .pi / 180
    let h = sin(dp / 2) * sin(dp / 2)
        + cos(p1) * cos(p2) * sin(dl / 2) * sin(dl / 2)
    return 2 * r * asin(min(1, h.squareRoot()))
}

let stalenessLimitSeconds = 30.0
let teleportSpeedLimitMPS = 415.0        // ~1500 km/h
let altitudeJumpRateLimitMPS = 50.0     // physically impossible on foot

/// Assess one fix (optionally against the prior fix stream). The
/// grade describes PRECISION; anomalies describe motion/data
/// inconsistencies - neither is ever an authentication.
public func assessLocation(
    fix: LocationSample,
    priorFixes: [LocationSample] = [],
    captureTime: Date? = nil) -> LocationRiskReport {
    var anomalies = fix.structuralProblems
    var spoofSignals: [String] = []
    var grade: AccuracyGrade = .none
    var stale = false
    var staleAge: Double?

    if fix.source == .manual {
        spoofSignals.append(
            "manual location: human-entered, no sensor basis - " +
            "cannot be graded for precision at all")
    } else if fix.source == .none {
        grade = .none
    } else {
        grade = AccuracyGrade.grade(accuracyM: fix.accuracyM)
        if fix.accuracyM == nil {
            anomalies.append(
                "missing accuracy field - precision ungradeable")
        } else if fix.accuracyM! > 500 {
            spoofSignals.append(String(format:
                "very coarse fix (%.0fm) - indoor/cold-start or " +
                "degraded reception", fix.accuracyM!))
        }
    }

    if let ts = fix.timestamp, let ct = captureTime {
        let age = ct.timeIntervalSince(ts)
        if age > stalenessLimitSeconds {
            stale = true
            staleAge = age
            anomalies.append(String(format:
                "fix is %.0fs older than the capture - position " +
                "may predate the shutter by a meaningful walk", age))
        }
    }

    if let prev = priorFixes.last,
       let pts = prev.timestamp, let cts = fix.timestamp {
        let dt = cts.timeIntervalSince(pts)
        if dt > 0 {
            let d = haversineM(prev.latitude, prev.longitude,
                               fix.latitude, fix.longitude)
            let speed = d / dt
            if speed > teleportSpeedLimitMPS {
                anomalies.append(String(format:
                    "TELEPORT: implied ground speed %.0f m/s over " +
                    "%.1fs (%.0fm) - physically implausible jump",
                    speed, dt, d))
                spoofSignals.append(
                    "position jump beyond any vehicular speed - " +
                    "spoofing or mislabeled timestamps are candidate " +
                    "explanations (not conclusions)")
            }
            if let acc = fix.accuracyM, acc <= 5, d > 200, dt < 2 {
                anomalies.append(
                    "claimed-precise fix with a large jump - " +
                    "precision claim and motion disagree")
            }
            if let pa = prev.altitudeM, let fa = fix.altitudeM {
                let vRate = abs(fa - pa) / dt
                if vRate > altitudeJumpRateLimitMPS {
                    anomalies.append(String(format:
                        "altitude changes at %.0f m/s - barometer " +
                        "glitch or spoofed altitude", vRate))
                }
            }
        }
    }

    if anomalies.isEmpty && spoofSignals.isEmpty {
        anomalies.append(
            "no anomalies observed - NOT evidence of genuineness")
    }
    return LocationRiskReport(
        grade: grade, stale: stale, staleAgeSeconds: staleAge,
        anomalies: anomalies, spoofRiskSignals: spoofSignals)
}
