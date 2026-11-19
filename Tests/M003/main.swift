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
    do { try action(); fatalError("Tampering was accepted") } catch EvidenceError.invalid {
    } catch { fatalError("Unexpected error: \(error)") }
}
fails { _ = try EvidenceStore.verify(record, photo: Data(repeating: 0x43, count: 1024)) }
var changed = record
changed.manifest!.device_id += "tampered"
fails { _ = try EvidenceStore.verify(changed, photo: photo) }
let restored = try JSONDecoder().decode(EvidenceRecord.self, from: JSONEncoder().encode(record))
let reverified = try EvidenceStore.verify(restored, photo: photo)
precondition(reverified.contains("passed"))
print("PASS canonical bytes, fixed-vector signature, photo tamper, field tamper, serialization roundtrip")
var missing = record
missing.manifest = nil
fails { _ = try EvidenceStore.verify(missing, photo: photo) }
var unsigned = missing
unsigned.publicKeyID = nil
unsigned.status = "Signed — local verification passed"
precondition(tryUnsigned(unsigned) == "Unsigned")
func tryUnsigned(_ r: EvidenceRecord) -> String {
    do { return try EvidenceStore.verify(r, photo: photo) }
    catch { fatalError("Unexpected unsigned error: \(error)") }
}
var corrupt = record
corrupt.manifest!.signature = Data([0]).base64EncodedString()
fails { _ = try EvidenceStore.verify(corrupt, photo: photo) }
corrupt.manifest!.signature = ""
fails { _ = try EvidenceStore.verify(corrupt, photo: photo) }
let softwareKey = P256.Signing.PrivateKey() // Test fixture only, never used by app capture.
let fixturePublic = softwareKey.publicKey.x963Representation
var local = record
local.manifest!.public_key = fixturePublic.base64EncodedString()
local.manifest!.device_id = evidenceHash(fixturePublic)
local.publicKeyID = evidenceHash(fixturePublic)
local.manifest!.signature = try softwareKey.signature(for: local.manifest!.payload()).derRepresentation.base64EncodedString()
_ = try EvidenceStore.verify(local, photo: photo, expectedPublicKey: fixturePublic)
fails { _ = try EvidenceStore.verify(local, photo: photo, expectedPublicKey: publicData) }
let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false)
defer { try? FileManager.default.removeItem(at: folder) }
func saveRecord(_ r: EvidenceRecord) throws {
    try JSONEncoder().encode(r).write(to: folder.appendingPathComponent("record.json"), options: .atomic)
}
try saveRecord(local)
try photo.write(to: folder.appendingPathComponent("photo.jpg"), options: .atomic)
let signatureURL = folder.appendingPathComponent("signature.der")
try Data(base64Encoded: local.manifest!.signature)!.write(to: signatureURL)
precondition(EvidenceStore.inspect(folder, expectedPublicKey: fixturePublic).contains("matches this installation key"))
try Data([0]).write(to: signatureURL)
precondition(EvidenceStore.inspect(folder, expectedPublicKey: fixturePublic).contains("Verification Failed"))
try FileManager.default.removeItem(at: signatureURL)
precondition(EvidenceStore.inspect(folder, expectedPublicKey: fixturePublic).contains("Verification Failed"))
try saveRecord(missing)
precondition(EvidenceStore.inspect(folder, expectedPublicKey: fixturePublic).contains("Verification Failed"))
try saveRecord(unsigned)
precondition(EvidenceStore.inspect(folder, expectedPublicKey: fixturePublic).contains("Signature / Local Verification: Unsigned"))
print("PASS missing/corrupt/deleted signature, stripped manifest, forged status, genuine unsigned and independent key mismatch")
try saveRecord(local)
var missingSignatureJSON = try JSONSerialization.jsonObject(with: JSONEncoder().encode(local)) as! [String: Any]
var missingSignatureManifest = missingSignatureJSON["manifest"] as! [String: Any]
missingSignatureManifest.removeValue(forKey: "signature")
missingSignatureJSON["manifest"] = missingSignatureManifest
try JSONSerialization.data(withJSONObject: missingSignatureJSON).write(to: folder.appendingPathComponent("record.json"))
precondition(EvidenceStore.inspect(folder, expectedPublicKey: fixturePublic).contains("Verification Failed"))
var forged = local
forged.manifest!.device_id += "modified"
forged.status = "Signed — local verification passed"
try saveRecord(forged)
try Data("Signed — local verification passed".utf8).write(to: folder.appendingPathComponent("local-status.txt"))
precondition(EvidenceStore.inspect(folder, expectedPublicKey: fixturePublic).contains("Verification Failed"))
print("PASS deleted manifest signature property and forged persisted verification status")
