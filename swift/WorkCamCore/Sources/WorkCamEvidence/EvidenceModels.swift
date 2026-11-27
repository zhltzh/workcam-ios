// TM57: evidence protocol models - Swift port of the TM01 schema
// contract (docs/evidence/evidence-schema-v1.json) + the TM06
// verification-result model.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).
//
// CRYPTO POLICY: this target NEVER implements raw ECDSA/SHA from
// scratch. Verification goes through CryptoKit on Apple platforms;
// the canonical-payload BUILDER is pure text and lives here because
// the byte contract is protocol, not cryptography.

import Foundation

/// Protocol versions with the never-trust-unknown rule baked in.
public enum EvidenceProtocolVersion: String, Codable, Sendable {
    case v1_0 = "1.0"
    case v1_1 = "1.1"

    public var domainSeparator: String {
        switch self {
        case .v1_0: return "WorkCam-Sig-v1"
        case .v1_1: return "WorkCam-Sig-v1.1"
        }
    }
}

/// An unparseable version string - the typed carrier for the schema
/// rule that unknown protocol versions are NEVER trusted (the
/// verifier reports them; it never guesses).
public struct UnknownProtocolVersion: Error, Sendable {
    public let raw: String
}

public struct EvidenceLocation: Codable, Sendable, Equatable {
    public let latitude: Double
    public let longitude: Double
    public let accuracy: Double
    public let altitude: Double?

    public init(latitude: Double, longitude: Double,
                accuracy: Double, altitude: Double?) {
        self.latitude = latitude
        self.longitude = longitude
        self.accuracy = accuracy
        self.altitude = altitude
    }
}

/// Codable mirror of the schema record (v1.x). Fields the client
/// cannot be trusted to set (registration state etc.) are NOT here -
/// they live in the server-resolved TrustInputs (WorkCamModels).
public struct EvidenceRecord: Codable, Sendable, Equatable {
    public var protocolVersion: String
    public var recordId: String
    public var sequenceNumber: Int
    public var previousRecordHash: String?
    public var photoHash: String
    public var captureTimestamp: String
    public var deviceReportedTime: String
    public var deviceId: String
    public var location: EvidenceLocation?
    public var signature: String?          // Base64 DER
    public var publicKeyB64: String?
    public var derivativeRefs: [String]?
    public var extensions: [String: JSONValue]?

    /// Typed version parse with the unknown-version refusal.
    public var version: Result<EvidenceProtocolVersion,
                              UnknownProtocolVersion> {
        if let v = EvidenceProtocolVersion(rawValue: protocolVersion) {
            return .success(v)
        }
        return .failure(UnknownProtocolVersion(raw: protocolVersion))
    }
}

/// Minimal JSON value wrapper (Codable-friendly) for the open
/// extensions bag.
public enum JSONValue: Codable, Sendable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case null
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) {
            self = .number(n)
        } else if let s = try? c.decode(String.self) {
            self = .string(s)
        } else if let a = try? c.decode([JSONValue].self) {
            self = .array(a)
        } else {
            self = .object(try c.decode([String: JSONValue].self))
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .null: try c.encodeNil()
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }

    /// Convenience accessor for the object case (nil otherwise).
    public var objectValue: [String: JSONValue]? {
        if case .object(let o) = self { return o }
        return nil
    }

    /// Convenience accessor for the string case (nil otherwise).
    public var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }
}

/// Canonical payload builder - the byte contract (TM01/TM02):
/// fixed field order, domain separator first, trailing newline,
/// %.10g floats with a guaranteed decimal point on v1.1, 'none'
/// for null links, and the newline-rejection rule on every string
/// field (a value like "a\ninjected=1" must be REFUSED, never
/// smuggled into the signed text).
public enum CanonicalPayload {
    public static func formatFloat(_ v: Double, version: String)
            throws -> String {
        guard v.isFinite else {
            throw GeometryError.nonFinite(
                "location float in canonical payload")
        }
        if v == v.rounded() && abs(v) < 1e15 {
            return String(format: "%.1f", v)
        }
        var s = String(format: "%.10g", v)
        if !s.contains(".") && !s.lowercased().contains("e") {
            s += ".0"
        }
        return s
    }

    public static func build(_ record: EvidenceRecord) throws
            -> String {
        let version = try record.version.get()
        // the newline rule (TM02 hostile-vector finding)
        for f in [record.recordId, record.captureTimestamp,
                  record.deviceReportedTime, record.deviceId] {
            if f.contains("\n") || f.contains("\r") {
                throw GeometryError.invalidParameter(
                    "string field must not contain newlines " +
                    "(protocol canonical-payload rule)")
            }
        }
        var lines: [String] = [version.domainSeparator]
        lines.append("version=\(version.rawValue)")
        lines.append("record_id=\(record.recordId)")
        lines.append("sequence_number=\(record.sequenceNumber)")
        lines.append("previous_record_hash="
                     + (record.previousRecordHash ?? "none"))
        lines.append("photo_hash=\(record.photoHash)")
        lines.append("capture_timestamp=\(record.captureTimestamp)")
        lines.append("device_reported_time="
                     + record.deviceReportedTime)
        lines.append("device_id=\(record.deviceId)")
        if let loc = record.location {
            let alt = loc.altitude.map {
                try? formatFloat($0, version: version.rawValue)
            } ?? "none"
            lines.append("location="
                + (try formatFloat(loc.latitude,
                                   version: version.rawValue)) + ","
                + (try formatFloat(loc.longitude,
                                   version: version.rawValue)) + ","
                + (try formatFloat(loc.accuracy,
                                   version: version.rawValue)) + ","
                + (alt ?? "none"))
        } else {
            lines.append("location=none")
        }
        return lines.joined(separator: "\n") + "\n"
    }
}

/// Six-layer verification result (the W018 CLI's layer vocabulary,
/// bridged into the ten-dimension engine by TrustInputs).
public struct VerificationLayers: Codable, Sendable, Equatable {
    public var fileIntegrity: String?      // pass/fail/absent
    public var signatureMath: String?      // pass/fail/absent
    public var deviceIdentity: String?      // registered/unknown/...
    public var hardwareProvenance: String?  // not_verifiable_offline
    public var trustedTime: String?        // server_anchored/...
    public var locationProvenance: String?  // signed_in_payload/none

    public init(fileIntegrity: String? = nil,
                signatureMath: String? = nil,
                deviceIdentity: String? = nil,
                hardwareProvenance: String? = nil,
                trustedTime: String? = nil,
                locationProvenance: String? = nil) {
        self.fileIntegrity = fileIntegrity
        self.signatureMath = signatureMath
        self.deviceIdentity = deviceIdentity
        self.hardwareProvenance = hardwareProvenance
        self.trustedTime = trustedTime
        self.locationProvenance = locationProvenance
    }
}
