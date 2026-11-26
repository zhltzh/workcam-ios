import CryptoKit
import Foundation

struct Vectors: Decodable {
    let test_public_key_b64: String
    let cases: [Vector]
}

struct Vector: Decodable {
    let name: String
    let record: Input
    let canonical_payload_text: String?
    let payload_sha256: String?
    let signature_der_b64: String?
}

// The fixture set includes deliberately invalid field types and empty signatures.
struct Input: Decodable {
    let protocol_version: String?
    let record_id: String
    let sequence_number: Int
    let previous_record_hash: String?
    let photo_hash: String
    let capture_timestamp: String
    let device_reported_time: String
    let device_id: String
    let location: Location?
    let invalidLocation: Bool

    struct Location: Decodable {
        let latitude: Double
        let longitude: Double
        let accuracy: Double
        let altitude: Double?
    }

    enum CodingKeys: String, CodingKey {
        case protocol_version, record_id, sequence_number, previous_record_hash, photo_hash
        case capture_timestamp, device_reported_time, device_id, location
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        protocol_version = try values.decodeIfPresent(String.self, forKey: .protocol_version)
        record_id = try values.decode(String.self, forKey: .record_id)
        sequence_number = try values.decode(Int.self, forKey: .sequence_number)
        previous_record_hash = try values.decodeIfPresent(String.self, forKey: .previous_record_hash)
        photo_hash = try values.decode(String.self, forKey: .photo_hash)
        capture_timestamp = try values.decode(String.self, forKey: .capture_timestamp)
        device_reported_time = try values.decode(String.self, forKey: .device_reported_time)
        device_id = try values.decode(String.self, forKey: .device_id)
        invalidLocation = values.contains(.location) && (try? values.decodeNil(forKey: .location)) == false &&
            (try? values.decode(Location.self, forKey: .location)) == nil
        location = try? values.decode(Location.self, forKey: .location)
    }

    func appPayload() throws -> Data {
        guard !invalidLocation else { throw EvidenceError.invalid }
        return try EvidenceManifest(version: protocol_version ?? "1.0", record_id: record_id,
            sequence_number: sequence_number, previous_record_hash: previous_record_hash,
            photo_hash: photo_hash, capture_timestamp: capture_timestamp, device_reported_time: device_reported_time,
            device_id: device_id, location: location == nil ? nil : "present", signature: "", public_key: "").payload()
    }
}

let vectors = try JSONDecoder().decode(Vectors.self,
    from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
guard let publicBytes = Data(base64Encoded: vectors.test_public_key_b64) else { throw EvidenceError.invalid }
let key = try P256.Signing.PublicKey(x963Representation: publicBytes)
var signatures = 0
var appPayloads = 0
var negatives = 0
for vector in vectors.cases {
    guard let encoded = vector.signature_der_b64 else {
        precondition((try? vector.record.appPayload()) == nil, vector.name)
        negatives += 1
        continue
    }
    guard let text = vector.canonical_payload_text, let der = Data(base64Encoded: encoded) else {
        throw EvidenceError.invalid
    }
    let payload = Data(text.utf8)
    let signature = try P256.Signing.ECDSASignature(derRepresentation: der)
    precondition(key.isValidSignature(signature, for: payload), vector.name)
    precondition(evidenceHash(payload) == vector.payload_sha256, vector.name)
    signatures += 1
    if vector.record.protocol_version == "1.1" && vector.record.location == nil && vector.record.sequence_number <= 1_000_000 {
        let canonical = try vector.record.appPayload()
        precondition(canonical == payload, vector.name)
        appPayloads += 1
    }
}
precondition(signatures == 96 && negatives == 4 && appPayloads > 0)
print("PASS TM02: \(signatures) supplied DER signatures and hashes, \(appPayloads) app canonical bytes, \(negatives) rejected inputs")
