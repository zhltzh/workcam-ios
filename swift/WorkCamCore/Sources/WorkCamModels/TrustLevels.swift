// TM58: ten-dimension evidence trust model - Swift port of
// backend/tools/trust_engine/trust_levels.py (TM07).
//
// CLIENT POLICY (the port's whole point): the client parses,
// displays and performs LIMITED local verification; it NEVER
// promotes a trust level on its own. Server-resolved facts arrive
// as data; local math only DOWNGRADES what the server sent if the
// bytes locally available contradict it.
//
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

public enum TrustStatus: String, Codable, Sendable, Equatable {
    case verified
    case unverified
    case notAttested = "not_attested"
    case unknown
    case failed
}

public enum TrustDimension: String, Codable, Sendable, CaseIterable {
    case fileIntegrity = "file_integrity"
    case signatureValidity = "signature_validity"
    case deviceRegistration = "device_registration"
    case hardwareAttestation = "hardware_attestation"
    case timeSource = "time_source"
    case locationSource = "location_source"
    case chainIntegrity = "chain_integrity"
    case externalTimestamp = "external_timestamp"
    case derivativeIntegrity = "derivative_integrity"
    case verificationEnvironment = "verification_environment"
}

public struct DimensionReport: Codable, Sendable, Equatable {
    public let name: TrustDimension
    public let status: TrustStatus
    public let reason: String
    public let evidence: String
    public let failureCondition: String
}

public struct TrustReport: Codable, Sendable {
    public let dimensions: [DimensionReport]
    public let notes: [String]

    public func dimension(_ d: TrustDimension) -> DimensionReport? {
        dimensions.first { $0.name == d }
    }

    public var anyFailed: Bool {
        dimensions.contains { $0.status == .failed }
    }

    public static let noScoreNote =
        "No composite score exists by design: dimensions are " +
        "independent claims. Any consumer decision must read the " +
        "per-dimension ledger."
}

/// Server-resolved facts, delivered to the client as DATA. The
/// struct mirrors the Python engine's inputs one field at a time;
/// the client fills what it has and the report keeps everything it
/// does not have at `nil` (which maps to unknown/unverified - never
/// a silent upgrade).
public struct TrustInputs: Codable, Sendable {
    public var photoSha256Matches: Bool?
    public var signatureVerifies: Bool?
    public var keyRegistration: KeyRegistration?
    public var appAttest: AppAttestFact?
    public var photoKeyBinding: PhotoKeyBindingFact?
    public var timeAnchor: TimeAnchorFact?
    public var location: LocationFact?
    public var chainReport: ChainFact?
    public var externalCheckpoint: CheckpointFact?
    public var derivatives: DerivativesFact?
    public var verificationEnvironment: String?

    public struct KeyRegistration: Codable, Sendable {
        public var enrollmentStatus: String   // active/rotated_out/...
        public var keyFingerprint: String?
        public init(enrollmentStatus: String,
                    keyFingerprint: String? = nil) {
            self.enrollmentStatus = enrollmentStatus
            self.keyFingerprint = keyFingerprint
        }
    }
    public struct AppAttestFact: Codable, Sendable {
        public var verified: Bool
        public init(verified: Bool) { self.verified = verified }
    }
    public struct PhotoKeyBindingFact: Codable, Sendable {
        public var verified: Bool
        public init(verified: Bool) { self.verified = verified }
    }
    public struct TimeAnchorFact: Codable, Sendable {
        public var state: String     // server_anchored/extrapolated/
                                    // unverifiable
        public var anchoredAt: String?
        public init(state: String, anchoredAt: String? = nil) {
            self.state = state
            self.anchoredAt = anchoredAt
        }
    }
    public struct LocationFact: Codable, Sendable {
        public var source: String   // gnss/manual/none/unknown
        public var spoofRisk: String?
        public init(source: String, spoofRisk: String? = nil) {
            self.source = source
            self.spoofRisk = spoofRisk
        }
    }
    public struct ChainFact: Codable, Sendable {
        public var valid: Bool
        public var records: Int
        public init(valid: Bool, records: Int) {
            self.valid = valid
            self.records = records
        }
    }
    public struct CheckpointFact: Codable, Sendable {
        public var ok: Bool
        public var records: Int
        public init(ok: Bool, records: Int) {
            self.ok = ok
            self.records = records
        }
    }
    public struct DerivativesFact: Codable, Sendable {
        public var allMatch: Bool
        public var checked: Int
        public init(allMatch: Bool, checked: Int) {
            self.allMatch = allMatch
            self.checked = checked
        }
    }

    public init() {}
}

/// Pure derivation - the SAME rule set as the Python engine
/// (TM07): client claims never promote, App Attest without the
/// same-key binding is NOT_ATTESTED, a rotated-out key keeps its
/// math but loses current authorization, an extrapolated time is
/// never server-certified, location never reports verified.
public func computeTrust(_ inputs: TrustInputs) -> TrustReport {
    var dims: [DimensionReport] = []
    func dim(_ n: TrustDimension, _ s: TrustStatus, _ r: String,
             _ e: String, _ f: String) {
        dims.append(DimensionReport(name: n, status: s, reason: r,
                                    evidence: e,
                                    failureCondition: f))
    }

    // 1. file integrity
    if let m = inputs.photoSha256Matches {
        dim(.fileIntegrity,
            m ? .verified : .failed,
            m ? "stored bytes hash to the recorded photo_hash"
              : "stored bytes DO NOT hash to the recorded photo_hash",
            "SHA-256 comparison recomputed during verification",
            "fails when any byte of the original changes")
    } else {
        dim(.fileIntegrity, .unknown,
            "bytes were not re-hashed by this verification run",
            "no hash comparison performed",
            "fails when SHA256(original bytes) != recorded photo_hash")
    }

    // 2. signature validity
    if let s = inputs.signatureVerifies {
        dim(.signatureValidity,
            s ? .verified : .failed,
            s ? "ECDSA P-256 signature verifies over the canonical "
              + "payload"
              : "signature does NOT verify - the record's math is "
              + "broken",
            "signature math executed by the verifier",
            "fails when payload, key or signature bytes are altered")
    } else {
        dim(.signatureValidity, .unverified,
            "no signature was checked by this verification run",
            "ECDSA verification not executed",
            "fails when the DER signature does not verify against "
            + "the recorded public key over the canonical payload")
    }

    // 3. device registration
    if let ks = inputs.keyRegistration {
        switch ks.enrollmentStatus {
        case "active":
            dim(.deviceRegistration, .verified,
                "the signing key is currently enrolled and active",
                "key registry lookup: fingerprint " +
                    (ks.keyFingerprint ?? "n/a"),
                "fails/diminishes when the key is unknown, "
                + "rotated-out, suspended or revoked")
        case "rotated_out":
            dim(.deviceRegistration, .unverified,
                "the signing key was rotated out AFTER this "
                + "evidence; historical evidence remains "
                + "math-checkable, new use is not authorized",
                "key registry lookup",
                "fails/diminishes when the key is unknown, "
                + "rotated-out, suspended or revoked")
        case "suspended":
            dim(.deviceRegistration, .unverified,
                "the device holding this key is suspended "
                + "(reversible hold)",
                "key registry lookup",
                "fails/diminishes when the key is unknown, "
                + "rotated-out, suspended or revoked")
        case "revoked":
            dim(.deviceRegistration, .failed,
                "the device holding this key is REVOKED (final)",
                "key registry lookup",
                "fails/diminishes when the key is unknown, "
                + "rotated-out, suspended or revoked")
        default:
            dim(.deviceRegistration, .unknown,
                "this key was never enrolled in the registry "
                + "(or the registry is unavailable)",
                "key registry lookup",
                "fails/diminishes when the key is unknown, "
                + "rotated-out, suspended or revoked")
        }
    } else {
        dim(.deviceRegistration, .unknown,
            "the signing key's registration state was not looked up",
            "no key registry query performed",
            "fails/diminishes when the key is unknown, rotated-out, "
            + "suspended or revoked")
    }

    // 4. hardware attestation (the binding rule is the whole point)
    if let aa = inputs.appAttest {
        if aa.verified {
            if let b = inputs.photoKeyBinding, b.verified {
                dim(.hardwareAttestation, .verified,
                    "App Attest credential verified AND bound to "
                    + "the photo-signing key via a signed challenge",
                    "attestation + same-key challenge both verified",
                    "fails when the binding challenge signature is "
                    + "absent or signed by a different key")
            } else if let b = inputs.photoKeyBinding, !b.verified {
                dim(.hardwareAttestation, .failed,
                    "App Attest verified but the same-key binding "
                    + "challenge FAILED",
                    "binding challenge rejected",
                    "(this failure fired)")
            } else {
                dim(.hardwareAttestation, .notAttested,
                    "an App Attest credential VERIFIED, but it was "
                    + "NOT bound to the photo-signing key (different "
                    + "keys are different verification problems)",
                    "attestation verified; binding challenge absent",
                    "binding fails unless the app signs a WorkCam "
                    + "challenge with the SAME credential")
            }
        } else {
            dim(.hardwareAttestation, .failed,
                "App Attest failed", "attestation object processed "
                + "and rejected", "(this failure fired)")
        }
    } else {
        dim(.hardwareAttestation, .notAttested,
            "no App Attest credential was presented",
            "no attestation object processed",
            "improves only when a valid App Attest credential chain "
            + "to the platform root is verified")
    }

    // 5. time source
    if let ta = inputs.timeAnchor {
        switch ta.state {
        case "server_anchored":
            dim(.timeSource, .verified,
                "a server-signed time anchor bounds this record "
                + "(anchored at \(ta.anchoredAt ?? "?"))",
                "anchor state server_anchored",
                "fails when the anchor signature is invalid or the "
                + "extrapolation window is exceeded")
        case "extrapolated":
            dim(.timeSource, .unverified,
                "capture time is EXTRAPOLATED from an earlier "
                + "anchor - a bounded estimate, NOT a "
                + "server-certified capture time",
                "anchor state extrapolated",
                "fails when the anchor signature is invalid or the "
                + "extrapolation window is exceeded")
        default:
            dim(.timeSource, .unknown,
                "time cannot be bounded (anchor expired / clock "
                + "reset)",
                "anchor state \(ta.state)",
                "fails when the anchor signature is invalid or the "
                + "extrapolation window is exceeded")
        }
    } else {
        dim(.timeSource, .unknown,
            "no server time-anchor receipt covers this record; the "
            + "capture time remains a client claim",
            "no anchor receipt presented",
            "verified only when a server-signed anchor receipt "
            + "binds the record before capture-time drift grows "
            + "unbounded")
    }

    // 6. location source (NEVER verified - GNSS is spoofable)
    if let loc = inputs.location {
        switch loc.source {
        case "gnss":
            dim(.locationSource, .unverified,
                "device-claimed GNSS fix; a plausible claim, NOT an "
                + "unforgeable position proof (spoof risk: "
                + (loc.spoofRisk ?? "not assessed") + ")",
                "source gnss",
                "never reports 'verified' - only plausibility can "
                + "be assessed")
        case "manual":
            dim(.locationSource, .unverified,
                "HUMAN-ENTERED location - by definition not "
                + "sensor-derived",
                "source manual",
                "never reports 'verified'")
        case "none":
            dim(.locationSource, .notAttested,
                "location explicitly recorded as absent",
                "source none",
                "never reports 'verified'")
        default:
            dim(.locationSource, .unknown,
                "location source \(loc.source) not recognized",
                "source \(loc.source)",
                "never reports 'verified'")
        }
    } else {
        dim(.locationSource, .notAttested,
            "no location data is attached to this record",
            "no location payload",
            "never reports 'verified'")
    }

    // 7. chain integrity
    if let ch = inputs.chainReport {
        dim(.chainIntegrity, ch.valid ? .verified : .failed,
            ch.valid
            ? "the local chain is internally consistent "
              + "(\(ch.records) records)"
            : "chain problems reported by the server",
            "chain verification executed",
            "fails on forks, interior deletions, insertions or "
            + "link mismatches (TM05 codes)")
    } else {
        dim(.chainIntegrity, .unknown,
            "the record's audit chain was not verified by this run",
            "no chain report presented",
            "fails on forks, interior deletions, insertions or "
            + "link mismatches")
    }

    // 8. external timestamp / checkpoint
    if let ck = inputs.externalCheckpoint {
        dim(.externalTimestamp, ck.ok ? .verified : .failed,
            ck.ok
            ? "external checkpoint verified (\(ck.records) records "
              + "anchored)"
            : "checkpoint mismatch reported by the server",
            "checkpoint signature + count/hash binding checked",
            "fails when the checkpoint signature is invalid or the "
            + "local chain diverges from the anchored head")
    } else {
        dim(.externalTimestamp, .notAttested,
            "no external checkpoint or RFC 3161 timestamp anchors "
            + "this record",
            "no checkpoint presented",
            "verified only when a valid external checkpoint (or TSA "
            + "token) covers the chain head / record digest")
    }

    // 9. derivative integrity
    if let dv = inputs.derivatives {
        dim(.derivativeIntegrity, dv.allMatch ? .verified : .failed,
            dv.allMatch
            ? "all \(dv.checked) declared derivatives hash to their "
              + "manifest values"
            : "one or more derivatives do NOT match their manifest "
              + "hashes",
            "per-file SHA-256 comparison",
            "fails when a derivative byte changes")
    } else {
        dim(.derivativeIntegrity, .unknown,
            "derivative files were not re-checked by this run",
            "no derivative comparison performed",
            "fails when any declared derivative's bytes no longer "
            + "hash to its manifest entry")
    }

    // 10. verification environment
    switch inputs.verificationEnvironment {
    case "device", "server":
        dim(.verificationEnvironment, .verified,
            "this report was produced in a " +
            (inputs.verificationEnvironment ?? "") +
            " verification environment",
            "engine-declared environment of this run",
            "desktop reports are DESKTOP_SIMULATION and never "
            + "substitute for device verification")
    case "desktop":
        dim(.verificationEnvironment, .unverified,
            "this report was produced in a 'desktop' verification "
            + "environment",
            "engine-declared environment of this run",
            "desktop reports are DESKTOP_SIMULATION and never "
            + "substitute for device verification")
    default:
        // The caller MUST state the environment; refusing to guess
        // is the same honesty rule as the Python engine raising.
        dim(.verificationEnvironment, .unknown,
            "verification environment not declared - trust claims "
            + "without an environment are meaningless",
            "engine-declared environment of this run",
            "desktop reports are DESKTOP_SIMULATION and never "
            + "substitute for device verification")
    }

    return TrustReport(dimensions: dims,
                       notes: [TrustReport.noScoreNote])
}
