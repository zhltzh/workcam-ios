//
//  W023CrosslangVectorTests.swift
//  WorkCam B02-W023 cross-language signature & serialization vectors.
//
//  STATUS: NOT VERIFIED on any Mac yet (Windows cannot run Xcode).
//  This file is a REFERENCE test, delivered as text for the Mac side to
//  add to a test target and run. It mirrors backend/w023_reference_verifier.py
//  function by function and validates every case in
//  test-vectors/crosslang/w023-crosslang-vectors.json.
//
//  Setup (see also test-vectors/crosslang/MacIntegrationGuide.md):
//    1. Add this file to a macOS/iOS unit test target.
//    2. Add w023-crosslang-vectors.json to the SAME test target as a
//       bundle resource (Target Membership checkbox in the File Inspector).
//    3. Run the test class.
//
//  No dependency on the app target is required: only Foundation, CryptoKit
//  and XCTest. All test keys are TEST KEYS ONLY - never production keys.
//

import XCTest
import CryptoKit
import Foundation

final class W023CrosslangVectorTests: XCTestCase {

    // MARK: - Vector loading

    private static let loadedVectors: [String: Any] = {
        guard let url = Bundle(for: W023CrosslangVectorTests.self)
            .url(forResource: "w023-crosslang-vectors", withExtension: "json") else {
            fatalError("w023-crosslang-vectors.json not found in test bundle - add it as a target resource")
        }
        do {
            let data = try Data(contentsOf: url)
            return try JSONSerialization.jsonObject(with: data) as! [String: Any]
        } catch {
            fatalError("cannot parse w023-crosslang-vectors.json: \(error)")
        }
    }()

    private var vectors: [String: Any] { Self.loadedVectors }

    private static let testPublicKey: Data = {
        let v = loadedVectors
        let b64 = (v["test_key"] as! [String: Any])["public_key_b64"] as! String
        return Data(base64Encoded: b64)!
    }()

    private static let testPublicKeyB64: String = {
        (loadedVectors["test_key"] as! [String: Any])["public_key_b64"] as! String
    }()

    // MARK: - Contract implementations (mirror of the Python verifier)
    // Every function below is the Swift port of the corresponding Python
    // function in backend/w023_reference_verifier.py.

    private func sha256Hex(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// Strict base64 (RFC 4648 section 4). Mirrors Python
    /// base64.b64decode(validate=True) semantics closely enough for the
    /// reject vectors: Data(base64Encoded:) already rejects characters
    /// outside the standard alphabet, embedded whitespace and bad padding.
    private func base64DecodeStrict(_ value: String) -> Data? {
        guard !value.isEmpty else { return Data() }
        guard value.count % 4 == 0 else { return nil }
        let alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+="
        for ch in value.unicodeScalars where !alphabet.unicodeScalars.contains(ch) {
            return nil
        }
        guard let data = Data(base64Encoded: value) else { return nil }
        // re-encode must round-trip (catches non-canonical padding bits)
        guard data.base64EncodedString() == value else { return nil }
        return data
    }

    /// Public key format check: 65 bytes, 0x04 prefix, BEFORE curve math.
    private func decodePublicKey(_ b64: String) throws -> Data {
        guard let raw = base64DecodeStrict(b64) else {
            throw W023Error.notBase64
        }
        guard raw.count == 65, raw[raw.startIndex] == 0x04 else {
            throw W023Error.badPublicKeyFormat
        }
        _ = try P256.Signing.PublicKey(x963Representation: raw) // curve validity
        return raw
    }

    /// DER structure check before crypto (SEQUENCE, lengths, two INTEGERs,
    /// no trailing data) - direct port of the production Python checker.
    private func derStructureCheck(_ der: Data) throws {
        let bytes = [UInt8](der)
        guard bytes.count >= 8 else { throw W023Error.derTooShort }
        guard bytes[0] == 0x30 else { throw W023Error.derNotSequence }
        var total: Int
        var pos = 2
        if bytes[1] & 0x80 != 0 {
            let n = Int(bytes[1] & 0x7F)
            guard n <= 2, bytes.count >= 2 + n else { throw W023Error.derBadLength }
            var len = 0
            for i in 0..<n { len = (len << 8) | Int(bytes[2 + i]) }
            total = 2 + n + len
            pos = 2 + n
        } else {
            total = 2 + Int(bytes[1])
        }
        guard bytes.count == total else { throw W023Error.derLengthMismatch }
        for _ in 0..<2 {
            guard pos < bytes.count else { throw W023Error.derTruncated }
            guard bytes[pos] == 0x02 else { throw W023Error.derNotInteger }
            pos += 1
            guard pos < bytes.count else { throw W023Error.derTruncated }
            let intLen = Int(bytes[pos])
            pos += 1
            guard pos + intLen <= bytes.count else { throw W023Error.derIntegerOverflow }
            pos += intLen
        }
        guard pos == bytes.count else { throw W023Error.derTrailingData }
    }

    /// Verbatim port of the production v1.1 float rule
    /// (backend app/services/serialization.py _format_float):
    ///   - integer values (except negative zero) -> "<digits>.0"
    ///   - otherwise "%.10g", appending ".0" when no '.' and no exponent
    ///   - negative zero -> "-0.0"
    /// A bare String(format: "%.10g") is NOT sufficient - see float_cases.
    private func formatFloatV11(_ value: Double) throws -> String {
        guard value.isFinite else { throw W023Error.nonFiniteFloat }
        let isIntegerValue = value.truncatingRemainder(dividingBy: 1) == 0
        let isNegativeZero = value == 0.0 && value.sign == .minus
        if isIntegerValue && !isNegativeZero {
            if abs(value) <= 9_007_199_254_740_992 { // 2^53: exact Int64 range
                return String(format: "%lld.0", Int64(value))
            }
            return String(format: "%.0f.0", value)
        }
        var formatted = String(format: "%.10g", value)
        if !formatted.contains(".") && !formatted.lowercased().contains("e") {
            formatted += ".0"
        }
        return formatted
    }

    /// GPS bounds, identical to the production model:
    /// lat [-90, 90], lon [-180, 180], accuracy > 0, alt [-1000, 100000].
    private func gpsBoundsCheck(name: String, value: Double?) throws {
        guard let value = value else { return }
        guard value.isFinite else { throw W023Error.gpsOutOfRange(name) }
        switch name {
        case "latitude": guard (-90.0...90.0).contains(value) else { throw W023Error.gpsOutOfRange(name) }
        case "longitude": guard (-180.0...180.0).contains(value) else { throw W023Error.gpsOutOfRange(name) }
        case "horizontal_accuracy": guard value > 0 else { throw W023Error.gpsOutOfRange(name) }
        case "altitude": guard (-1000.0...100000.0).contains(value) else { throw W023Error.gpsOutOfRange(name) }
        default: break
        }
    }

    private let iso8601ShapeRegex = try! NSRegularExpression(
        pattern: "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})$"
    )

    /// ISO 8601 SHAPE check only. The timestamp is an opaque string in the
    /// payload: never parse-then-reformat.
    private func iso8601ShapeMatches(_ value: String) -> Bool {
        let range = NSRange(value.startIndex..., in: value)
        return iso8601ShapeRegex.firstMatch(in: value, range: range) != nil
    }

    /// Location -> canonical 'lat,lon,acc,alt' string (v1.1 rule).
    private func locationString(_ location: [String: Any]?, version: String) throws -> String {
        guard let location = location else { return "none" }
        let lat = location["latitude"] as? Double
        let lon = location["longitude"] as? Double
        let acc = location["horizontal_accuracy"] as? Double
        let alt = location["altitude"] as? Double
        try gpsBoundsCheck(name: "latitude", value: lat)
        try gpsBoundsCheck(name: "longitude", value: lon)
        try gpsBoundsCheck(name: "horizontal_accuracy", value: acc)
        try gpsBoundsCheck(name: "altitude", value: alt)
        if version == "1.1" {
            let altStr = alt == nil ? "none" : try formatFloatV11(alt!)
            return [try formatFloatV11(lat!), try formatFloatV11(lon!),
                    try formatFloatV11(acc!), altStr].joined(separator: ",")
        }
        let altStr = alt == nil ? "none" : "\(alt!)"
        return ["\(lat!)", "\(lon!)", "\(acc!)", altStr].joined(separator: ",")
    }

    private static let fieldOrder = [
        "version", "record_id", "sequence_number", "previous_record_hash",
        "photo_hash", "capture_timestamp", "device_reported_time",
        "device_id", "location",
    ]

    /// THE byte contract: rebuild the exact signed payload from the
    /// manifest fields. JSON null and absent keys both map to 'none'.
    private func buildCanonicalPayload(_ manifest: [String: Any]) throws -> Data {
        let version = manifest["version"] as! String
        guard version == "1.0" || version == "1.1" else {
            // cross-language rule: reject BEFORE serialization - do NOT
            // mimic the Python fallback domain separator
            throw W023Error.unsupportedVersion(version)
        }
        for key in ["capture_timestamp", "device_reported_time"] {
            let ts = manifest[key] as? String ?? ""
            guard iso8601ShapeMatches(ts) else { throw W023Error.badIso8601(key) }
        }
        for key in ["record_id", "photo_hash", "device_id"] {
            let v = manifest[key] as? String ?? ""
            guard !v.contains("\n") && !v.contains("\r") else {
                throw W023Error.newlineInField(key)
            }
        }
        let domain = version == "1.1" ? "WorkCam-Sig-v1.1" : "WorkCam-Sig-v1"
        var lines = [domain]
        for name in Self.fieldOrder {
            if name == "location" {
                // JSONSerialization: NSNull and absent both fail the as? cast
                let loc = manifest["location"] as? [String: Any]
                lines.append("location=" + (try locationString(loc, version: version)))
                continue
            }
            if let value = manifest[name] {
                if value is NSNull {
                    lines.append("\(name)=none")
                } else if let s = value as? String {
                    lines.append("\(name)=\(s)")
                } else if let i = value as? NSNumber {
                    lines.append("\(name)=\(i)")
                }
            } else {
                lines.append("\(name)=none")
            }
        }
        let text = lines.joined(separator: "\n") + "\n"
        // defensive: the payload must never contain the un-signed fields
        XCTAssertFalse(text.contains("\nsignature=") || text.contains("\npublic_key="))
        return Data(text.utf8)
    }

    private func verifyECDSA(pubBytes: Data, sigDer: Data, payload: Data) -> Bool {
        guard let key = try? P256.Signing.PublicKey(x963Representation: pubBytes),
              let sig = try? P256.Signing.ECDSASignature(derRepresentation: sigDer) else {
            return false
        }
        return key.isValidSignature(sig, for: payload)
    }

    private func recordHash(payload: Data, signatureB64: String) -> String {
        sha256Hex(payload + Data(signatureB64.utf8))
    }

    private func hexString(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }

    private func dataFromHex(_ hex: String) -> Data {
        var bytes: [UInt8] = []
        var i = hex.startIndex
        while i < hex.endIndex {
            let j = hex.index(i, offsetBy: 2)
            bytes.append(UInt8(hex[i..<j], radix: 16)!)
            i = j
        }
        return Data(bytes)
    }

    private func doubleFromBitsHex(_ bitsHex: String) -> Double {
        Double(bitPattern: UInt64(bitsHex, radix: 16)!)
    }

    private func signedCases() -> [[String: Any]] {
        let section = vectors["signed_manifest_cases"] as! [String: Any]
        return section["cases"] as! [[String: Any]]
    }

    private func findSignedCase(_ id: String) -> [String: Any] {
        signedCases().first { ($0["id"] as! String) == id }!
    }

    private enum W023Error: Error {
        case notBase64
        case badPublicKeyFormat
        case derTooShort, derNotSequence, derBadLength, derLengthMismatch
        case derTruncated, derNotInteger, derIntegerOverflow, derTrailingData
        case nonFiniteFloat
        case gpsOutOfRange(String)
        case unsupportedVersion(String)
        case badIso8601(String)
        case newlineInField(String)
        case payloadMustNotContainSignature
    }

    // MARK: - 1. SHA-256 vectors

    func testSha256Vectors() throws {
        let section = vectors["sha256_cases"] as! [String: Any]
        let cases = section["cases"] as! [[String: Any]]
        for c in cases {
            let input = dataFromHex(c["input_hex"] as! String)
            XCTAssertEqual(sha256Hex(input), c["expected_sha256_hex"] as! String,
                           "case \(c["id"] as! String)")
            XCTAssertEqual(input.count, c["length_bytes"] as! Int)
        }
        // FIPS known answers must be the published constants
        let kat = sha256Hex(Data())
        XCTAssertEqual(kat, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        XCTAssertEqual(sha256Hex(Data("abc".utf8)),
                       "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        XCTAssertEqual(kat, sha256Hex(Data()), "empty string KAT")
        _ = kat
    }

    // MARK: - 2. Base64 vectors

    func testBase64Vectors() throws {
        let section = vectors["base64_cases"] as! [String: Any]
        let cases = section["cases"] as! [[String: Any]]
        for c in cases {
            let id = c["id"] as! String
            if id.hasPrefix("b") {
                let raw = dataFromHex(c["input_hex"] as! String)
                XCTAssertEqual(raw.base64EncodedString(), c["expected_base64"] as! String, id)
                XCTAssertEqual(base64DecodeStrict(c["expected_base64"] as! String), raw, id)
            } else {
                XCTAssertNil(base64DecodeStrict(c["input_base64"] as! String),
                             "\(id) must be rejected by strict base64 decoding")
            }
        }
        // the DER signature reference must decode to 0x30
        let ref = (vectors["base64_cases"] as! [String: Any])["signature_base64_reference"] as! String
        XCTAssertEqual(base64DecodeStrict(ref)!.first, 0x30)
    }

    // MARK: - 3. Public key encoding vectors

    func testPublicKeyEncodingVectors() throws {
        let section = vectors["public_key_cases"] as! [String: Any]
        let valid = section["valid"] as! [String: Any]
        let raw = try decodePublicKey(valid["base64"] as! String)
        XCTAssertEqual(raw.count, 65)
        XCTAssertEqual(raw.first, 0x04)

        for var_case in section["known_variants"] as! [[String: Any]] {
            let b64: String
            if let hex = var_case["hex"] as? String {
                b64 = dataFromHex(hex).base64EncodedString()
            } else {
                b64 = (var_case["pem"] as! String).replacingOccurrences(of: "\n", with: "")
            }
            XCTAssertThrowsError(try decodePublicKey(b64),
                                 "\(var_case["id"] as! String) must be rejected by the format check")
        }
        for rej in section["reject_cases"] as! [[String: Any]] {
            XCTAssertThrowsError(try decodePublicKey(rej["base64"] as! String),
                                 "\(rej["id"] as! String) must be rejected")
        }
        // compressed form must also be rejected by CryptoKit's x963 init
        let variants = section["known_variants"] as! [[String: Any]]
        let compressed = variants.first { ($0["id"] as! String) == "pkv-sec1-compressed" }!
        XCTAssertThrowsError(try P256.Signing.PublicKey(
            x963Representation: dataFromHex(compressed["hex"] as! String)))
    }

    // MARK: - 4. DER structure vectors

    func testDerStructureVectors() throws {
        let section = vectors["der_cases"] as! [String: Any]
        let valid = section["valid_signature"] as! [String: Any]
        let sig = dataFromHex(valid["der_hex"] as! String)
        try derStructureCheck(sig)
        XCTAssertEqual(base64DecodeStrict(valid["base64"] as! String), sig)

        for rej in section["reject_cases"] as! [[String: Any]] {
            let id = rej["id"] as! String
            let expected = (rej["expected_structure_check"] as? String)
                ?? (rej["expected"] as! String)
            let data = dataFromHex(rej["hex"] as! String)
            do {
                try derStructureCheck(data)
                XCTAssertFalse(expected.hasPrefix("rejected"),
                               "\(id): expected structural rejection")
            } catch {
                XCTAssertTrue(expected.hasPrefix("rejected"),
                               "\(id): unexpected rejection \(error)")
            }
        }
    }

    // MARK: - 5. Exact payload byte contract

    func testPayloadContract() throws {
        let contract = vectors["payload_contract"] as! [String: Any]
        let payload = dataFromHex(contract["reference_payload_hex"] as! String)
        let caseRef = findSignedCase(contract["reference_case"] as! String)

        let rebuilt = try buildCanonicalPayload(caseRef["manifest_json"] as! [String: Any])
        XCTAssertEqual(rebuilt, payload, "payload rebuild mismatch")
        XCTAssertEqual(sha256Hex(payload), contract["reference_payload_sha256"] as! String)
        XCTAssertTrue(payload.last == 0x0a, "exactly one trailing LF")
        XCTAssertFalse(payload.contains(0x0d), "no CR anywhere")

        // line map consistency
        var offset = 0
        for entry in contract["reference_line_map"] as! [[String: Any]] {
            XCTAssertEqual(entry["byte_offset"] as! Int, offset, "line \(entry["line"] as! Int)")
            let lineLen = entry["byte_length_including_lf"] as! Int
            let line = payload.subdata(in: offset..<(offset + lineLen))
            XCTAssertEqual(line.last, 0x0a)
            XCTAssertEqual(String(data: line.dropLast(), encoding: .utf8),
                           entry["content_utf8"] as? String)
            offset += lineLen
        }
        XCTAssertEqual(offset, payload.count)

        // record_hash rule
        let sig = (caseRef["signature"] as! [String: Any])["base64"] as! String
        XCTAssertEqual(recordHash(payload: payload, signatureB64: sig),
                       caseRef["record_hash_hex"] as! String)
    }

    // MARK: - 6. Signed manifest cases (the core cross-language check)

    func testSignedManifestCases() throws {
        for c in signedCases() {
            let id = c["id"] as! String
            let manifest = c["manifest_json"] as! [String: Any]
            let payload = try buildCanonicalPayload(manifest)
            let stored = dataFromHex((c["canonical_payload"] as! [String: Any])["hex"] as! String)
            XCTAssertEqual(payload, stored, "\(id): payload bytes differ between Python and Swift")
            XCTAssertEqual(sha256Hex(payload),
                           (c["canonical_payload"] as! [String: Any])["sha256_hex"] as! String)

            let sigInfo = c["signature"] as! [String: Any]
            let sig = try XCTUnwrap(base64DecodeStrict(sigInfo["base64"] as! String))
            try derStructureCheck(sig)
            XCTAssertTrue(verifyECDSA(pubBytes: Self.testPublicKey, sigDer: sig, payload: payload),
                          "\(id): signature does not verify on Swift")
            XCTAssertEqual(recordHash(payload: payload, signatureB64: sigInfo["base64"] as! String),
                           c["record_hash_hex"] as! String)
        }
    }

    func testSignedCaseLocationBitPatterns() throws {
        for c in signedCases() {
            guard let bits = c["location_bits_hex"] as? [String: String] else { continue }
            let loc = (c["manifest_json"] as! [String: Any])["location"] as! [String: Any]
            for (name, bitsHex) in bits {
                let value = loc[name] as! Double
                XCTAssertEqual(UInt64(bitPattern: value), UInt64(bitsHex, radix: 16),
                               "\(c["id"] as! String) \(name) IEEE-754 bits")
                XCTAssertEqual(doubleFromBitsHex(bitsHex), value)
            }
        }
    }

    // MARK: - 7. Unicode cases

    func testUnicodeCases() throws {
        let section = vectors["unicode_cases"] as! [String: Any]
        let cases = section["cases"] as! [[String: Any]]
        for c in cases {
            let text = c["text"] as! String
            let raw = Data(text.utf8)
            XCTAssertEqual(hexString(raw), c["utf8_hex"] as! String, c["id"] as! String)
            XCTAssertEqual(raw.count, c["length_bytes"] as! Int)
            XCTAssertEqual(sha256Hex(raw), c["sha256_hex"] as! String)
        }
        // NFC vs NFD must be different bytes with different hashes
        let byId = Dictionary(uniqueKeysWithValues: cases.map { ($0["id"] as! String, $0) })
        XCTAssertNotEqual(byId["u2-nfc-e"]!["utf8_hex"] as! String,
                          byId["u3-nfd-e"]!["utf8_hex"] as! String)
    }

    // MARK: - 8. Newline discipline

    func testNewlineRejects() throws {
        let section = vectors["newline_cases"] as! [String: Any]
        for c in section["reject_cases"] as! [[String: Any]] {
            let payload = dataFromHex(c["tampered_payload_hex"] as! String)
            let sig = try XCTUnwrap(base64DecodeStrict(c["signature_base64_used"] as! String))
            XCTAssertFalse(verifyECDSA(pubBytes: Self.testPublicKey, sigDer: sig, payload: payload),
                           "\(c["id"] as! String): newline variant must not verify")
        }
        // field values with newlines must be rejected before crypto
        let base = findSignedCase("v11-minimal-no-location")["manifest_json"] as! [String: Any]
        var bad = base
        bad["record_id"] = "bad\nid"
        XCTAssertThrowsError(try buildCanonicalPayload(bad))
        var bad2 = base
        bad2["device_id"] = "bad\rid"
        XCTAssertThrowsError(try buildCanonicalPayload(bad2))
    }

    // MARK: - 9. Float formatting (THE Mac-side cross-validation core)

    func testFloatV1_1Table() throws {
        let section = vectors["float_cases"] as! [String: Any]
        let table = section["v1_1_format_table"] as! [[String: Any]]
        for row in table {
            let value = doubleFromBitsHex(row["input_bits_hex"] as! String)
            let expected = row["expected_v1_1_string"] as! String
            let actual = try formatFloatV11(value)
            XCTAssertEqual(actual, expected,
                           "bits \(row["input_bits_hex"] as! String): Swift '\(actual)' != Python '\(expected)'")
        }
    }

    func testFloatV1_0RiskTablePendingMacOutput() throws {
        // v1.0 repr semantics: this test documents the drift risk. The
        // Swift values are intentionally NOT asserted against Python str()
        // here - the whole point is that v1.0 floats are unsafe to share
        // across languages. Only the bits must round-trip.
        let section = vectors["float_cases"] as! [String: Any]
        let table = section["v1_0_repr_risk_table"] as! [[String: Any]]
        for row in table {
            let value = doubleFromBitsHex(row["input_bits_hex"] as! String)
            let bits = UInt64(bitPattern: value)
            XCTAssertEqual(String(format: "%016x", bits),
                           row["input_bits_hex"] as! String)
        }
    }

    // MARK: - 10. null vs absent

    func testNullVsAbsentEquivalence() throws {
        let section = vectors["null_absent_cases"] as! [String: Any]
        let eq = section["equivalence_case"] as! [String: Any]
        let withNulls = eq["manifest_json_with_nulls"] as! [String: Any]
        let withAbsent = eq["manifest_json_with_absent_keys"] as! [String: Any]

        // JSON layer: both forms must serialize back to equivalent models
        let jsonNull = try JSONSerialization.data(withJSONObject: withNulls)
        let jsonAbsent = try JSONSerialization.data(withJSONObject: withAbsent)
        let mNull = try JSONSerialization.jsonObject(with: jsonNull) as! [String: Any]
        let mAbsent = try JSONSerialization.jsonObject(with: jsonAbsent) as! [String: Any]

        let pNull = try buildCanonicalPayload(mNull)
        let pAbsent = try buildCanonicalPayload(mAbsent)
        XCTAssertEqual(pNull, pAbsent,
                       "JSON null and absent keys must rebuild identical payloads")
        XCTAssertEqual(pNull, dataFromHex(eq["canonical_payload_hex"] as! String))

        let sig = try XCTUnwrap(base64DecodeStrict(
            (eq["signature"] as! [String: Any])["base64"] as! String))
        XCTAssertTrue(verifyECDSA(pubBytes: Self.testPublicKey, sigDer: sig, payload: pNull))
    }

    // MARK: - 11. GPS cases

    func testGpsValidCases() throws {
        let section = vectors["gps_cases"] as! [String: Any]
        for c in section["valid_cases"] as! [[String: Any]] {
            let loc = c["location"] as! [String: Any]
            try gpsBoundsCheck(name: "latitude", value: loc["latitude"] as? Double)
            try gpsBoundsCheck(name: "longitude", value: loc["longitude"] as? Double)
            try gpsBoundsCheck(name: "horizontal_accuracy",
                              value: loc["horizontal_accuracy"] as? Double)
            try gpsBoundsCheck(name: "altitude", value: loc["altitude"] as? Double)
            let rebuilt = try locationString(loc, version: "1.1")
            XCTAssertEqual(rebuilt, c["expected_v1_1_string"] as! String,
                           "\(c["id"] as! String)")
        }
    }

    func testGpsRejectCases() throws {
        let rejectSpecs: [String: (Double?, Double?, Double?, Double?)] = [
            "gps-r1-lat-above-90": (90.00001, 0.0, 1.0, nil),
            "gps-r2-lon-below-180": (0.0, -180.001, 1.0, nil),
            "gps-r3-acc-zero": (0.0, 0.0, 0.0, nil),
            "gps-r4-acc-negative": (0.0, 0.0, -1.0, nil),
            "gps-r5-alt-below-floor": (0.0, 0.0, 1.0, -1000.1),
            "gps-r6-alt-above-ceiling": (0.0, 0.0, 1.0, 100001.0),
            "gps-r7-nan-lat": (Double.nan, 0.0, 1.0, nil),
            "gps-r8-inf-lon": (0.0, Double.infinity, 1.0, nil),
        ]
        let section = vectors["gps_cases"] as! [String: Any]
        for c in section["reject_cases"] as! [[String: Any]] {
            let id = c["id"] as! String
            let (lat, lon, acc, alt) = rejectSpecs[id]!
            XCTAssertThrowsError(try gpsBoundsCheck(name: "latitude", value: lat), id)
            XCTAssertThrowsError(try gpsBoundsCheck(name: "longitude", value: lon), id)
            XCTAssertThrowsError(try gpsBoundsCheck(name: "horizontal_accuracy", value: acc), id)
            if alt != nil {
                XCTAssertThrowsError(try gpsBoundsCheck(name: "altitude", value: alt), id)
            }
        }
    }

    // MARK: - 12. ISO 8601 shapes

    func testIso8601Cases() throws {
        let section = vectors["iso8601_cases"] as! [String: Any]
        for c in section["valid_cases"] as! [[String: Any]] {
            XCTAssertTrue(iso8601ShapeMatches(c["value"] as! String), c["id"] as! String)
        }
        for c in section["invalid_cases"] as! [[String: Any]] {
            XCTAssertFalse(iso8601ShapeMatches(c["value"] as! String), c["id"] as! String)
        }
        // documented finding: shape check accepts month 13
        let shape = section["shape_only_accepted_example"] as! [String: Any]
        XCTAssertTrue(iso8601ShapeMatches(shape["value"] as! String))
    }

    // MARK: - 13. Timezone spellings

    func testTimezoneCases() throws {
        let section = vectors["timezone_cases"] as! [String: Any]
        let reps = section["same_instant_representations"] as! [[String: Any]]
        var hashes = Set<String>()
        for rep in reps {
            let raw = dataFromHex(rep["utf8_hex"] as! String)
            XCTAssertEqual(String(data: raw, encoding: .utf8), rep["value"] as! String)
            XCTAssertEqual(sha256Hex(raw), rep["sha256_of_utf8"] as! String)
            hashes.insert(rep["sha256_of_utf8"] as! String)
        }
        XCTAssertEqual(hashes.count, 3, "distinct spellings must hash differently")
        // signed cases with different timezone spellings must carry different signatures
        let z = findSignedCase("v11-timestamp-z-suffix")
        let off = findSignedCase("v11-timestamp-offset-plus-8")
        XCTAssertNotEqual((z["signature"] as! [String: Any])["der_hex"] as! String,
                          (off["signature"] as! [String: Any])["der_hex"] as! String)
    }

    // MARK: - 14. Field order

    func testFieldOrderCase() throws {
        let section = vectors["field_order_cases"] as! [String: Any]
        let eq = section["shuffled_json_equivalence_case"] as! [String: Any]
        let shuffled = eq["manifest_json_key_order_shuffled"] as! [String: Any]
        let rebuilt = try buildCanonicalPayload(shuffled)
        XCTAssertEqual(rebuilt, dataFromHex(eq["canonical_payload_hex"] as! String),
                       "JSON key order must not affect the canonical payload")
        let sig = try XCTUnwrap(base64DecodeStrict(
            (eq["signature"] as! [String: Any])["base64"] as! String))
        XCTAssertTrue(verifyECDSA(pubBytes: Self.testPublicKey, sigDer: sig, payload: rebuilt))
    }

    // MARK: - 15. Tamper cases

    func testTamperCases() throws {
        let section = vectors["tamper_cases"] as! [String: Any]
        for c in section["reject_cases"] as! [[String: Any]] {
            let id = c["id"] as! String
            if let payloadHex = c["tampered_payload_hex"] as? String {
                let payload = dataFromHex(payloadHex)
                let sig = try XCTUnwrap(base64DecodeStrict(
                    c["signature_base64_used"] as! String))
                XCTAssertFalse(verifyECDSA(pubBytes: Self.testPublicKey, sigDer: sig,
                                           payload: payload),
                               "\(id): tampered payload must not verify")
            } else if let sigB64 = c["tampered_signature_base64"] as? String {
                // signature-layer tampering: reject at base64/DER or fail crypto
                if let sig = base64DecodeStrict(sigB64),
                   (try? derStructureCheck(sig)) != nil {
                    let base = findSignedCase(c["base_case"] as! String)
                    let payload = dataFromHex(
                        (base["canonical_payload"] as! [String: Any])["hex"] as! String)
                    XCTAssertFalse(verifyECDSA(pubBytes: Self.testPublicKey, sigDer: sig,
                                               payload: payload),
                                   "\(id): tampered signature must not verify")
                }
                // else: rejected at decode layer - acceptable outcome
            }
        }
    }

    // MARK: - 16. Version compatibility

    func testVersionCases() throws {
        let section = vectors["version_cases"] as! [String: Any]
        let binding = section["binding_table"] as! [String: String]
        XCTAssertEqual(binding["1.0"], "WorkCam-Sig-v1")
        XCTAssertEqual(binding["1.1"], "WorkCam-Sig-v1.1")

        let unknown = section["unknown_version_behavior"] as! [String: Any]
        var bad = findSignedCase("v11-minimal-no-location")["manifest_json"] as! [String: Any]
        bad["version"] = unknown["version"] as! String
        XCTAssertThrowsError(try buildCanonicalPayload(bad),
                             "unknown versions must be rejected BEFORE serialization")

        // domain/version binding over the positive cases
        let v10 = findSignedCase("v10-minimal-no-location")
        let v10Payload = try buildCanonicalPayload(v10["manifest_json"] as! [String: Any])
        XCTAssertTrue(String(data: v10Payload.prefix(14), encoding: .utf8)!.hasPrefix("WorkCam-Sig-v1\n"))
        let v11 = findSignedCase("v11-minimal-no-location")
        let v11Payload = try buildCanonicalPayload(v11["manifest_json"] as! [String: Any])
        XCTAssertTrue(String(data: v11Payload.prefix(16), encoding: .utf8)!.hasPrefix("WorkCam-Sig-v1.1\n"))
    }

    // MARK: - 17. Test key self-check

    func testTestKeyDerivation() throws {
        let key = try P256.Signing.PublicKey(x963Representation: Self.testPublicKey)
        XCTAssertEqual(key.x963Representation, Self.testPublicKey)
        // RFC 6979 reproduction on Swift is NOT required for verification
        // (Swift only verifies); the Python verifier already reproduces
        // every signature byte-for-byte.
        let tk = vectors["test_key"] as! [String: Any]
        let hex = tk["public_key_hex"] as! String
        XCTAssertEqual(hexString(Self.testPublicKey), hex)
        XCTAssertEqual(Self.testPublicKeyB64, Self.testPublicKey.base64EncodedString())
    }
}
