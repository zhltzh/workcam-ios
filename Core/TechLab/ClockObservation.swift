import Foundation

/// Diagnostic observations only. Neither wall time nor uptime proves capture authenticity.
struct ClockObservation: Codable, Sendable {
    let sessionID: UUID
    let sequence: UInt64
    let wallTime: Date
    let uptime: TimeInterval
    let timeZoneIdentifier: String
    let utcOffsetSeconds: Int

    static func capture(sessionID: UUID, sequence: UInt64) -> Self {
        let now = Date()
        return Self(sessionID: sessionID, sequence: sequence, wallTime: now,
                    uptime: ProcessInfo.processInfo.systemUptime,
                    timeZoneIdentifier: TimeZone.current.identifier,
                    utcOffsetSeconds: TimeZone.current.secondsFromGMT(for: now))
    }
}

enum ClockDiagnostic: String, Codable {
    case invalidObservation, sessionBoundary, sequenceDiscontinuity
    case uptimeReset, wallClockRollback, wallClockJump, timeZoneChanged
}

struct ClockDiagnostics {
    /// Session boundaries intentionally prohibit uptime continuity assertions across restarts.
    static func compare(_ previous: ClockObservation, _ current: ClockObservation,
                        tolerance: TimeInterval = 2) -> [ClockDiagnostic] {
        guard tolerance.isFinite, tolerance >= 0,
              previous.uptime.isFinite, current.uptime.isFinite,
              previous.uptime >= 0, current.uptime >= 0,
              previous.wallTime.timeIntervalSince1970.isFinite,
              current.wallTime.timeIntervalSince1970.isFinite else { return [.invalidObservation] }
        var flags: [ClockDiagnostic] = []
        if previous.timeZoneIdentifier != current.timeZoneIdentifier ||
            previous.utcOffsetSeconds != current.utcOffsetSeconds { flags.append(.timeZoneChanged) }
        guard previous.sessionID == current.sessionID else {
            flags.append(.sessionBoundary)
            return flags
        }
        if previous.sequence == UInt64.max || current.sequence != previous.sequence &+ 1 {
            flags.append(.sequenceDiscontinuity)
        }
        let elapsed = current.uptime - previous.uptime
        guard elapsed >= 0 else { flags.append(.uptimeReset); return flags }
        let discrepancy = current.wallTime.timeIntervalSince(previous.wallTime) - elapsed
        if discrepancy < -tolerance { flags.append(.wallClockRollback) }
        if discrepancy > tolerance { flags.append(.wallClockJump) }
        return flags
    }
}
