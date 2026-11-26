// TM61: measurement quality & error propagation - Swift port of
// backend/techlab/geometry/quality.py (TM22).
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

public enum TrackingState: String, Sendable {
    case normal
    case limitedExcessiveMotion = "limited_excessive_motion"
    case limitedInsufficientFeatures = "limited_insufficient_features"
    case initializing
    case notAvailable = "not_available"
}

let trackingPenalty: [TrackingState: Int] = [
    .normal: 0,
    .initializing: 1,
    .limitedInsufficientFeatures: 1,
    .limitedExcessiveMotion: 2,
    .notAvailable: 3,
]

public enum MeasurementQuality: String, Sendable {
    case good, fair, poor, invalid
}

public struct CalibrationProfile: Sendable, Equatable {
    public let deviceModel: String
    public let sigmaInterceptM: Double
    public let sigmaPerMeter: Double
    public let biasM: Double
    public let calibrationVersion: String

    public init(deviceModel: String, sigmaInterceptM: Double,
                sigmaPerMeter: Double, biasM: Double,
                calibrationVersion: String) {
        self.deviceModel = deviceModel
        self.sigmaInterceptM = sigmaInterceptM
        self.sigmaPerMeter = sigmaPerMeter
        self.biasM = biasM
        self.calibrationVersion = calibrationVersion
    }

    public func sigmaAt(_ distanceM: Double) -> Double {
        (sigmaInterceptM * sigmaInterceptM
         + (sigmaPerMeter * distanceM) * (sigmaPerMeter * distanceM))
            .squareRoot()
    }

    /// The profile every report MUST use until real calibration data
    /// lands: conservative defaults, explicitly versioned.
    public static func uncalibrated() -> CalibrationProfile {
        CalibrationProfile(
            deviceModel: "unknown", sigmaInterceptM: 0.05,
            sigmaPerMeter: 0.02, biasM: 0,
            calibrationVersion: "uncalibrated")
    }
}

public struct MeasurementQualityReport: Sendable {
    public let quality: MeasurementQuality
    public let trackingState: TrackingState
    public let calibration: CalibrationProfile
    public let sigmaM: Double?
    public let factors: [String]
    public let confidenceIntervalM: (Double, Double)?
    public static let honestyNote =
        "quality factors are recorded, never averaged into a score; " +
        "an uncalibrated profile yields a CONSERVATIVE interval " +
        "explicitly marked as such, never a device-calibrated claim"
}

public func assessPointMeasurement(
    measuredDistanceM: Double,
    pointSigmaM: Double,
    tracking: TrackingState,
    calibration: CalibrationProfile? = nil,
    depthConfidence: Double? = nil,
    surfaceMaterial: String? = nil,
    illumination: String? = nil) -> MeasurementQualityReport {
    let cal = calibration ?? .uncalibrated()
    var factors: [String] = []
    let sigma = pointSigmaM
    if cal.calibrationVersion == "uncalibrated" {
        factors.append("device not calibrated - conservative model " +
                       "used, interval is NOT a device spec")
    }
    let penalty = trackingPenalty[tracking] ?? 2
    if tracking != .normal {
        factors.append("tracking degraded: \(tracking.rawValue)")
    }
    if let dc = depthConfidence, dc.isFinite, dc < 0.5 {
        factors.append(String(format:
            "low depth confidence (%.2f)", dc))
    }
    if let m = surfaceMaterial,
       ["glass", "mirror", "dark", "shiny"].contains(m) {
        factors.append("difficult surface for depth: \(m)")
    }
    if illumination == "low_light" {
        factors.append("low light degrades depth quality")
    }
    let inflation = 1.0 + 0.5 * Double(penalty)
    let sigmaEff = sigma * inflation
    var quality: MeasurementQuality = .good
    if penalty >= 1 || (depthConfidence.map { $0 < 0.5 } ?? false) {
        quality = .fair
    }
    if penalty >= 2 { quality = .poor }
    if penalty >= 3 || !sigmaEff.isFinite || sigmaEff <= 0 {
        quality = .invalid
    }
    let ci: (Double, Double)? =
        quality == .invalid
        ? nil
        : (measuredDistanceM - 2 * sigmaEff,
           measuredDistanceM + 2 * sigmaEff)
    return MeasurementQualityReport(
        quality: quality, trackingState: tracking, calibration: cal,
        sigmaM: quality == .invalid ? nil : sigmaEff,
        factors: factors, confidenceIntervalM: ci)
}

// MARK: - error propagation (first-order variance formulas)

public func propagateDistanceSigma(_ a: Double, _ b: Double) -> Double {
    (a * a + b * b).squareRoot()
}

public func propagateAreaSigma(edgeSigmas: (Double, Double),
                               edges: (Double, Double)) throws
        -> Double {
    let (sw, sh) = edgeSigmas
    let (w, h) = edges
    guard w > 0, h > 0 else {
        throw GeometryError.invalidParameter("edges must be positive")
    }
    let area = w * h
    return area * ((sw / w) * (sw / w) + (sh / h) * (sh / h))
        .squareRoot()
}

public func propagateVolumeExtrusionSigma(
    baseArea: Double, areaSigma: Double,
    height: Double, heightSigma: Double) throws -> Double {
    guard baseArea > 0, height > 0 else {
        throw GeometryError.invalidParameter(
            "area and height must be positive")
    }
    let v = baseArea * height
    return v * ((areaSigma / baseArea) * (areaSigma / baseArea)
                + (heightSigma / height) * (heightSigma / height))
        .squareRoot()
}

public struct SlopeSigma: Sendable {
    public let sigmaDeg: Double
    public let valid: Bool
    public let reason: String
}

public func propagateSlopeSigma(horizontal: Double, hSigma: Double,
                                vertical: Double,
                                vSigma: Double) -> SlopeSigma {
    guard horizontal > 0 else {
        return SlopeSigma(sigmaDeg: .nan, valid: false,
                          reason: "non-positive horizontal run")
    }
    let r2 = horizontal * horizontal + vertical * vertical
    let dthDh = -vertical / r2
    let dthDv = horizontal / r2
    let varRad = (dthDh * hSigma) * (dthDh * hSigma)
        + (dthDv * vSigma) * (dthDv * vSigma)
    return SlopeSigma(sigmaDeg: varRad.squareRoot() * 180 / .pi,
                      valid: true, reason: "")
}

/// Sample std-dev of repeats under IDENTICAL conditions; nil for
/// fewer than 2 values (a single shot has no repeatability - a zero
/// would be a lie).
public func repeatabilityStd(_ values: [Double]) -> Double? {
    guard values.count >= 2 else { return nil }
    guard values.allSatisfy({ $0.isFinite }) else { return nil }
    let n = Double(values.count)
    let mean = values.reduce(0, +) / n
    let v = values.reduce(0) { $0 + ($1 - mean) * ($1 - mean) }
    return (v / (n - 1)).squareRoot()
}
