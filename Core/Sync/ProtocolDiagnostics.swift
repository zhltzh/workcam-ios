import CryptoKit
import Foundation

/// Isolated TM01 reader. This DTO does not replace the app's current capture format.
struct TM01Evidence: Decodable {
    struct Location: Decodable {
        let latitude: Double, longitude: Double, accuracy: Double
        let altitude: Double?
    }

    let protocol_version: String
    let record_id: String
    let sequence_number: Int
    let previous_record_hash: String?
    let photo_hash: String
    let capture_timestamp: String
    let device_reported_time: String?
    let device_id: String
    let location: Location?
    let signature: String
    let public_key_b64: String

    static func validate(_ data: Data) throws -> Self {
        let permitted: Set<String> = ["protocol_version", "record_id", "sequence_number", "previous_record_hash",
            "photo_hash", "capture_timestamp", "device_reported_time", "device_id", "location", "signature",
            "public_key_b64", "anchor_reference", "derivative_refs", "extensions"]
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let object, Set(object.keys).isSubset(of: permitted) else { throw EvidenceError.invalid }
        let value = try JSONDecoder().decode(Self.self, from: data)
        func hex(_ string: String, count: ClosedRange<Int>) -> Bool {
            count.contains(string.utf8.count) && string.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
        }
        guard ["1.0", "1.1"].contains(value.protocol_version), hex(value.record_id, count: 16...64),
              (0...1_000_000).contains(value.sequence_number), hex(value.photo_hash, count: 64...64),
              value.previous_record_hash.map({ hex($0, count: 64...64) }) ?? true,
              (1...128).contains(value.device_id.count),
              [value.device_id, value.capture_timestamp, value.device_reported_time ?? ""].allSatisfy({
                  !$0.utf8.contains(10) && !$0.utf8.contains(13)
              }),
              let der = Data(base64Encoded: value.signature), (try? P256.Signing.ECDSASignature(derRepresentation: der)) != nil,
              let key = Data(base64Encoded: value.public_key_b64),
              (try? P256.Signing.PublicKey(x963Representation: key)) != nil
        else { throw EvidenceError.invalid }
        if let location = value.location {
            let allowed = Set(["latitude", "longitude", "accuracy", "altitude"])
            guard (object["location"] as? [String: Any]).map({ Set($0.keys).isSubset(of: allowed) }) == true,
                  (-90...90).contains(location.latitude), (-180...180).contains(location.longitude),
                  location.accuracy.isFinite, location.accuracy >= 0,
                  location.altitude?.isFinite ?? true else { throw EvidenceError.invalid }
        }
        if let refs = object["derivative_refs"] {
            guard let hashes = refs as? [String], hashes.count <= 64,
                  hashes.allSatisfy({ hex($0, count: 64...64) }) else { throw EvidenceError.invalid }
        }
        if let extensions = object["extensions"], !(extensions is [String: Any]) { throw EvidenceError.invalid }
        if let anchor = object["anchor_reference"], !(anchor is NSNull), !(anchor is String) { throw EvidenceError.invalid }
        return value
    }
}

struct TM10Interval {
    let earliest: Double, latest: Double, estimate: Double
    let expired: Bool
    var uncertainty: Double { (latest - earliest) / 2 }

    /// Nil means no defensible interval. No output is a certified capture timestamp.
    static func estimate(server: Double, rtt: Double, anchor: Double, capture: Double,
                         driftPPM: Double, resolution: Double, ttl: Double, now: Double,
                         reset: Bool) -> Self? {
        guard !reset, [server, rtt, anchor, capture, driftPPM, resolution, ttl, now].allSatisfy(\.isFinite),
              rtt >= 0, capture >= anchor, anchor >= 0, driftPPM >= 0, resolution >= 0, ttl > 0 else { return nil }
        let elapsed = capture - anchor
        let middle = server + elapsed
        let slack = rtt / 2 + elapsed * driftPPM * 1e-6 + resolution
        guard (middle - slack).isFinite, (middle + slack).isFinite else { return nil }
        return Self(earliest: middle - slack, latest: middle + slack, estimate: middle,
                    expired: now - server > ttl || elapsed > ttl)
    }
}

struct TM12Location {
    enum Source { case gnss, manual, none }
    struct Fix {
        let lat: Double, lon: Double, time: Double?
        let accuracy: Double?, altitude: Double?
        let source: Source
    }

    enum Grade: String { case excellent, good, fair, poor, none }
    struct Quality {
        let grade: Grade
        let stale: Bool
        let flags: Set<String>
    }

    static func assess(_ fix: Fix, prior: Fix? = nil, capture: Double? = nil) -> Quality {
        var flags = Set<String>()
        let valid = (-90...90).contains(fix.lat) && (-180...180).contains(fix.lon)
        let accuracy = fix.accuracy.flatMap { $0.isFinite && $0 >= 0 ? $0 : nil }
        if !valid { flags.insert("invalid_coordinates") }
        if fix.accuracy != nil && accuracy == nil { flags.insert("invalid_accuracy") }
        let grade: Grade
        if fix.source != .gnss || !valid { grade = .none }
        else if let accuracy {
            grade = accuracy <= 5 ? .excellent : accuracy <= 20 ? .good : accuracy <= 75 ? .fair : .poor
            if accuracy > 500 { flags.insert("coarse_fix") }
        } else {
            grade = .none
            flags.insert("missing_accuracy")
        }
        let stale = capture.flatMap { end in fix.time.map { end - $0 > 30 } } ?? false
        if stale { flags.insert("stale") }
        if valid, let prior, (-90...90).contains(prior.lat), (-180...180).contains(prior.lon),
           let time = fix.time, let previous = prior.time, time - previous > 0 {
            let dt = time - previous, rad = Double.pi / 180
            let a = pow(sin((fix.lat - prior.lat) * rad / 2), 2)
                + cos(fix.lat * rad) * cos(prior.lat * rad) * pow(sin((fix.lon - prior.lon) * rad / 2), 2)
            let d = 12_742_000 * asin(sqrt(min(1, max(0, a))))
            if d / dt > 415 { flags.insert("ground_jump") }
            if let accuracy, accuracy <= 5, d > 200, dt < 2 { flags.insert("precision_motion_conflict") }
            if let altitude = fix.altitude, let before = prior.altitude, abs(altitude - before) / dt > 50 {
                flags.insert("altitude_jump")
            }
        }
        return Quality(grade: grade, stale: stale, flags: flags)
    }
}
