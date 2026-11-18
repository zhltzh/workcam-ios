import Foundation
import CryptoKit

let path = CommandLine.arguments[1]
let vector = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as! [String: Any]
var json = vector["manifest"] as! [String: Any]
json["public_key"] = (vector["public_key"] as! [String: Any])["base64"]
json["signature"] = (vector["signature"] as! [String: Any])["base64"]
let manifest = try JSONDecoder().decode(EvidenceManifest.self, from: JSONSerialization.data(withJSONObject: json))
let expected = (vector["canonical_payload"] as! [String: Any])["text"] as! String
let payload = try manifest.payload()
precondition(payload == Data(expected.utf8))
let photo = Data(repeating: 0x42, count: 1024)
let publicData = Data(base64Encoded: manifest.public_key)!
let record = EvidenceRecord(manifest: manifest, photoID: manifest.record_id, sha256: manifest.photo_hash, publicKeyID: evidenceHash(publicData), status: "test")
let verified = try EvidenceStore.verify(record, photo: photo)
precondition(verified.contains("passed"))
func fails(_ action: () throws -> Void) {
    do { try action(); fatalError("Tampering was accepted") } catch {}
}
fails { _ = try EvidenceStore.verify(record, photo: Data(repeating: 0x43, count: 1024)) }
var changed = record
changed.manifest!.device_id += "tampered"
fails { _ = try EvidenceStore.verify(changed, photo: photo) }
let restored = try JSONDecoder().decode(EvidenceRecord.self, from: JSONEncoder().encode(record))
let reverified = try EvidenceStore.verify(restored, photo: photo)
precondition(reverified.contains("passed"))
print("PASS canonical bytes, fixed-vector signature, photo tamper, field tamper, serialization roundtrip")
let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false)
defer { try? FileManager.default.removeItem(at: folder) }
try JSONEncoder().encode(record).write(to: folder.appendingPathComponent("record.json"), options: .atomic)
try photo.write(to: folder.appendingPathComponent("photo.jpg"), options: .atomic)
try Data(base64Encoded: manifest.signature)!.write(to: folder.appendingPathComponent("signature.der"), options: .atomic)
precondition(EvidenceStore.inspect(folder).contains("local verification passed"))
try Data([0]).write(to: folder.appendingPathComponent("signature.der"))
precondition(EvidenceStore.inspect(folder).contains("Verification Failed"))
print("PASS disk reload and detached DER tamper rejection")
