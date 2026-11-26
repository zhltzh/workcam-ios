// TM59: offline time component - Swift port of
// backend/techlab/time_uncertainty.py (TM10) with the W024 state
// vocabulary (TM09). Real clock acquisition is INJECTED via the
// ClockSource protocol: Windows tests use synthetic clocks, Mac
// implements the real system/monotonic adapter.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

/// Real-clock injection point. The Mac app maps this to
// CACurrentMediaTime (monotonic) + the system wall clock; tests use
/// a synthetic double.
public protocol ClockSource: Sendable {
    func now() -> ClockSample
}

public struct ClockSample: Sendable, Equatable {
    public let wall: Date            // timezone-aware wall clock
    public let monotonicSeconds: Double
    public let bootID: String        // stable per boot session

    public init(wall: Date, monotonicSeconds: Double, bootID: String) {
        self.wall = wall
        self.monotonicSeconds = monotonicSeconds
        self.bootID = bootID
    }
}

public struct TimeAnchorReceipt: Codable, Sendable {
    public let serverReceivedAt: Date
    public let rttSeconds: Double
    public let anchorMonotonic: Double
    public let bootID: String
    public let ttlSeconds: Double

    public init(serverReceivedAt: Date, rttSeconds: Double,
                anchorMonotonic: Double, bootID: String,
                ttlSeconds: Double) {
        self.serverReceivedAt = serverReceivedAt
        self.rttSeconds = rttSeconds
        self.anchorMonotonic = anchorMonotonic
        self.bootID = bootID
        self.ttlSeconds = ttlSeconds
    }
}

public enum TimeEstimateState: String, Codable, Sendable {
    case extrapolated
    case expired
    case unknown
}

public struct TimeEstimate: Sendable, Equatable {
    public let state: TimeEstimateState
    public let estimated: Date?
    public let earliest: Date?
    public let latest: Date?
    public let uncertaintySeconds: Double?
    public let timeSource: String
    public let confidenceReasons: [String]
    public let anomalies: [String]

    /// THE honesty rule of TM10, carried in the type itself: an
    /// extrapolated estimate is a BOUNDED ESTIMATE, never a
    /// server-certified capture time.
    public static let honestyNote =
        "EXTRAPOLATED time is a BOUNDED ESTIMATE, not a "
        + "server-certified capture time (TM10 honesty rule)"
}

/// Device clock drift model (conservative until real-device
/// calibration - TM22 device half).
public struct DeviceClockModel: Sendable {
    public let driftPPM: Double
    public let monotonicResolutionSeconds: Double

    public init(driftPPM: Double = 250.0,
                monotonicResolutionSeconds: Double = 0.001) {
        self.driftPPM = driftPPM
        self.monotonicResolutionSeconds = monotonicResolutionSeconds
    }
}

/// Interval arithmetic - the EXACT formulas of the Python reference
/// (documented in SWIFT_PORTABLE_SPEC there):
///   delta_t = capture_mono - anchor_mono
///   drift = delta_t * ppm * 1e-6
///   earliest = server_received - rtt/2 + delta_t - drift - res
///   latest   = server_received + rtt/2 + delta_t + drift + res
///   uncertainty = (latest - earliest) / 2
public func computeInterval(anchor: TimeAnchorReceipt,
                            capture: ClockSample,
                            clock: DeviceClockModel = DeviceClockModel(),
                            now: Date? = nil) -> TimeEstimate {
    // reboot: the monotonic chain is broken -> UNKNOWN (a fabricated
    // precise time would be worse than none)
    if capture.bootID != anchor.bootID {
        return TimeEstimate(
            state: .unknown, estimated: nil, earliest: nil,
            latest: nil, uncertaintySeconds: nil,
            timeSource: "server_anchor+monotonic_extrapolation",
            confidenceReasons: [
                "monotonic reset between anchor and capture - " +
                "offline elapsed time is unrecoverable from clocks " +
                "alone",
                "restart breaks every honest interval -> UNKNOWN"],
            anomalies: ["boot_id changed: " + anchor.bootID + " -> "
                        + capture.bootID])
    }
    let deltaT = capture.monotonicSeconds - anchor.anchorMonotonic
    if deltaT < 0 {
        return TimeEstimate(
            state: .unknown, estimated: nil, earliest: nil,
            latest: nil, uncertaintySeconds: nil,
            timeSource: "server_anchor+monotonic_extrapolation",
            confidenceReasons: [
                "negative elapsed monotonic time cannot produce a " +
                "valid interval"],
            anomalies: ["capture monotonic PRECEDES anchor monotonic " +
                        "without a boot change - inconsistent evidence"])
    }
    let drift = deltaT * clock.driftPPM * 1e-6
    let halfRTT = anchor.rttSeconds / 2
    let res = clock.monotonicResolutionSeconds
    let base = anchor.serverReceivedAt
    let earliest = base.addingTimeInterval(
        -halfRTT + deltaT - drift - res)
    let latest = base.addingTimeInterval(
        halfRTT + deltaT + drift + res)
    let estimated = base.addingTimeInterval(deltaT)
    let uncertainty = (latest.timeIntervalSince(earliest)) / 2
    let age = (now ?? capture.wall).timeIntervalSince(base)
    let expired = age > anchor.ttlSeconds
        || deltaT > anchor.ttlSeconds
    var reasons: [String] = [
        String(format:
            "monotonic elapsed %.3fs from a signed anchor; " +
            "half_rtt=%.3fs, drift=%.3fs, resolution=%.3fs",
            deltaT, halfRTT, drift, res),
        TimeEstimate.honestyNote]
    if expired {
        reasons.append(
            "anchor age/extrapolation exceeds ttl=" +
            String(format: "%.0fs", anchor.ttlSeconds) +
            " - interval computed but DEGRADED; drift bound may no " +
            "longer hold over that span")
    }
    return TimeEstimate(
        state: expired ? .expired : .extrapolated,
        estimated: estimated, earliest: earliest, latest: latest,
        uncertaintySeconds: uncertainty,
        timeSource: "server_anchor+monotonic_extrapolation",
        confidenceReasons: reasons, anomalies: [])
}
