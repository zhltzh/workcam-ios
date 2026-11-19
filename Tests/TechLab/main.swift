import Foundation
let session = UUID()
func observation(_ wall: Double, _ uptime: Double, _ sequence: UInt64 = 1,
                 _ id: UUID = session, _ zone: String = "UTC") -> ClockObservation {
    ClockObservation(sessionID: id, sequence: sequence,
                     wallTime: Date(timeIntervalSince1970: wall), uptime: uptime,
                     timeZoneIdentifier: zone, utcOffsetSeconds: 0)
}
let base = observation(1000, 100, 0)
func check(_ current: ClockObservation, _ expected: [ClockDiagnostic]) {
    precondition(ClockDiagnostics.compare(base, current) == expected)
}
check(observation(1010, 110), [])
check(observation(900, 110), [.wallClockRollback])
check(observation(2000, 110), [.wallClockJump])
check(observation(1010, 110, 4), [.sequenceDiscontinuity])
check(observation(1010, 1), [.uptimeReset])
check(observation(2000, 1, 1, UUID()), [.sessionBoundary])
check(observation(1010, 110, 1, session, "Asia/Shanghai"), [.timeZoneChanged])
check(observation(1010, .nan), [.invalidObservation])
let decoded = try JSONDecoder().decode(ClockObservation.self, from: JSONEncoder().encode(base))
precondition(decoded.sessionID == base.sessionID && decoded.wallTime == base.wallTime)
print("PASS: clock diagnostic fixtures and Codable roundtrip; synthetic tests, no device evidence")
