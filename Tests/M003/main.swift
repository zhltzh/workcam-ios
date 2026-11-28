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

// Public fixed-vector export fixture; never hardware evidence.
let exportRoot = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
let committed = exportRoot.appendingPathComponent(record.photoID)
try FileManager.default.createDirectory(at: committed, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: exportRoot) }
try photo.write(to: committed.appendingPathComponent("photo.jpg"))
try JSONEncoder().encode(record).write(to: committed.appendingPathComponent("record.json"))
try Data("must never export".utf8).write(to: committed.appendingPathComponent("private-key-test-sentinel"))
let committedDER = committed.appendingPathComponent("signature.der")
fails { _ = try EvidenceStore.exportPackages(sourceRoot: exportRoot) }
try Data([0]).write(to: committedDER)
fails { _ = try EvidenceStore.exportPackages(sourceRoot: exportRoot) }
try Data(base64Encoded: manifest.signature)!.write(to: committedDER)
let exported = try EvidenceStore.exportPackages(sourceRoot: exportRoot)
defer { try? FileManager.default.removeItem(at: exported) }
let package = exported.appendingPathComponent(committed.lastPathComponent)
let names = try FileManager.default.contentsOfDirectory(atPath: package.path)
precondition(Set(names) == Set(["photo.jpg", "record.json", "payload.bin", "public-key.x963", "signature.der"]))
let exportedPhoto = try Data(contentsOf: package.appendingPathComponent("photo.jpg"))
let exportedPayload = try Data(contentsOf: package.appendingPathComponent("payload.bin"))
let exportedKey = try Data(contentsOf: package.appendingPathComponent("public-key.x963"))
let exportedSignature = try Data(contentsOf: package.appendingPathComponent("signature.der"))
precondition(exportedPhoto == photo && exportedPayload == payload && exportedKey == publicData)
let exportKey = try P256.Signing.PublicKey(x963Representation: exportedKey)
let exportSig = try P256.Signing.ECDSASignature(derRepresentation: exportedSignature)
precondition(exportKey.isValidSignature(exportSig, for: exportedPayload))
var oneByteChanged = exportedPhoto
oneByteChanged[0] ^= 1
fails { _ = try EvidenceStore.verify(record, photo: oneByteChanged) }
try oneByteChanged.write(to: committed.appendingPathComponent("photo.jpg"))
fails { _ = try EvidenceStore.exportPackages(sourceRoot: exportRoot) }
print("PASS export exact allowlist/bytes/signature; one-byte tamper rejected (desktop fixture only)")
let emptyExportRoot = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try FileManager.default.createDirectory(at: emptyExportRoot, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: emptyExportRoot) }
do { _ = try EvidenceStore.exportPackages(sourceRoot: emptyExportRoot); fatalError("empty export accepted") }
catch EvidenceError.noExportableRecords { print("PASS empty export reports explicit error") }
let pendingID = UUID().uuidString.lowercased()
let complete = emptyExportRoot.appendingPathComponent(".pending-" + pendingID)
let recovered = emptyExportRoot.appendingPathComponent(pendingID)
let incomplete = emptyExportRoot.appendingPathComponent(".pending-" + UUID().uuidString)
for directory in [complete, incomplete] {
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
    try photo.write(to: directory.appendingPathComponent("photo.jpg"))
}
let pendingRecord = EvidenceRecord(photoID: pendingID, sha256: evidenceHash(photo), status: "Unsigned")
try JSONEncoder().encode(pendingRecord).write(to: complete.appendingPathComponent("record.json"))
let promoted = try EvidenceStore.recoverPending(in: emptyExportRoot)
let rerun = try EvidenceStore.recoverPending(in: emptyExportRoot)
let recoveredPhoto = try Data(contentsOf: recovered.appendingPathComponent("photo.jpg"))
let incompletePhoto = try Data(contentsOf: incomplete.appendingPathComponent("photo.jpg"))
precondition(promoted == 1 && rerun == 0 && recoveredPhoto == photo && incompletePhoto == photo)
print("PASS complete capture restored, retry idempotent, incomplete original preserved")

let signedStage = emptyExportRoot.appendingPathComponent(".pending-" + record.photoID)
let signedFinal = emptyExportRoot.appendingPathComponent(record.photoID)
let signatureFile = signedStage.appendingPathComponent("signature.der")
let metadataFile = signedStage.appendingPathComponent("record.json")
try FileManager.default.createDirectory(at: signedStage, withIntermediateDirectories: false)
try photo.write(to: signedStage.appendingPathComponent("photo.jpg"))
try JSONEncoder().encode(record).write(to: metadataFile)

func expectStagePreserved() throws {
    let count = try EvidenceStore.recoverPending(in: emptyExportRoot)
    let original = try Data(contentsOf: signedStage.appendingPathComponent("photo.jpg"))
    precondition(count == 0 && original == photo)
}

try expectStagePreserved()
try Data([0]).write(to: signatureFile)
try expectStagePreserved()
try exportedSignature.write(to: signatureFile)
try FileManager.default.createDirectory(at: signedFinal, withIntermediateDirectories: false)
try oneByteChanged.write(to: signedFinal.appendingPathComponent("photo.jpg"))
try expectStagePreserved()
let destinationOriginal = try Data(contentsOf: signedFinal.appendingPathComponent("photo.jpg"))
precondition(destinationOriginal == oneByteChanged)
try FileManager.default.removeItem(at: signedFinal)
try JSONEncoder().encode(unsigned).write(to: metadataFile)
try expectStagePreserved()
try JSONEncoder().encode(record).write(to: metadataFile)
let signedPromoted = try EvidenceStore.recoverPending(in: emptyExportRoot)
let signedRerun = try EvidenceStore.recoverPending(in: emptyExportRoot)
let restoredSignature = try Data(contentsOf: signedFinal.appendingPathComponent("signature.der"))
precondition(signedPromoted == 1 && signedRerun == 0 && restoredSignature == exportedSignature)
print("PASS signed recovery, missing/mismatched/orphan DER, collision, and retry")

let interruptedID = UUID().uuidString.lowercased()
let interruptedFolder = emptyExportRoot.appendingPathComponent(".pending-" + interruptedID)
try FileManager.default.createDirectory(at: interruptedFolder, withIntermediateDirectories: false)
try photo.write(to: interruptedFolder.appendingPathComponent("photo.jpg"))
let intentURL = interruptedFolder.appendingPathComponent("capture-intent.sha256")
try Data(evidenceHash(Data()).utf8).write(to: intentURL)
let rejectedIntent = try EvidenceStore.recoverPending(in: emptyExportRoot)
precondition(rejectedIntent == 0)
try Data(evidenceHash(photo).utf8).write(to: intentURL)
let recoveredIntent = try EvidenceStore.recoverPending(in: emptyExportRoot)
precondition(recoveredIntent == 1)
let recoveredFolder = emptyExportRoot.appendingPathComponent(interruptedID)
let interruptedMetadata = try JSONDecoder().decode(EvidenceRecord.self, from: Data(contentsOf: recoveredFolder.appendingPathComponent("record.json")))
let interruptedPhoto = try Data(contentsOf: recoveredFolder.appendingPathComponent("photo.jpg"))
precondition(interruptedMetadata.manifest == nil && interruptedPhoto == photo)
print("PASS interrupted photo intent rejects mismatch and recovers matching original as unsigned")

try FileManager.default.createDirectory(at: signedStage, withIntermediateDirectories: false)
try photo.write(to: signedStage.appendingPathComponent("photo.jpg"))
try Data(evidenceHash(photo).utf8).write(to: signedStage.appendingPathComponent("capture-intent.sha256"))
try JSONEncoder().encode(record).write(to: metadataFile)
try expectStagePreserved()
try FileManager.default.removeItem(at: signedFinal)
let repairedSidecar = try EvidenceStore.recoverPending(in: emptyExportRoot)
let repairedDER = try Data(contentsOf: signedFinal.appendingPathComponent("signature.der"))
let repairedOriginal = try Data(contentsOf: signedFinal.appendingPathComponent("photo.jpg"))
let repeatedRepair = try EvidenceStore.recoverPending(in: emptyExportRoot)
precondition(repairedSidecar == 1 && repairedDER == exportedSignature && repairedOriginal == photo && repeatedRepair == 0)
print("PASS matching signed intent restores only missing DER after verification, no duplicate")

let failedWriteID = UUID()
let projectLink = ProjectEvidenceLink(projectID: UUID(), timestamp: 42)
do {
    _ = try EvidenceStore.capture(photo, recordID: failedWriteID, projectLink: projectLink,
                                  storageRoot: emptyExportRoot) { _, _ in
        throw CocoaError(.fileWriteOutOfSpace)
    }
    fatalError("Write failure was accepted")
} catch let error as CocoaError where error.code == .fileWriteOutOfSpace {}
let failedWriteStage = emptyExportRoot.appendingPathComponent(".pending-" + failedWriteID.uuidString.lowercased())
let failedWriteFinal = emptyExportRoot.appendingPathComponent(failedWriteID.uuidString.lowercased())
let failedRecovery = try EvidenceStore.recoverPending(in: emptyExportRoot)
precondition(failedRecovery == 0 && !FileManager.default.fileExists(atPath: failedWriteFinal.path))
precondition(!FileManager.default.fileExists(atPath: failedWriteStage.appendingPathComponent("photo.jpg").path))
let stagedLink = try JSONDecoder().decode(ProjectEvidenceLink.self, from: Data(contentsOf:
    failedWriteStage.appendingPathComponent("project-link.json")))
precondition(stagedLink.projectID == projectLink.projectID)
print("PASS injected original write failure never publishes project association or evidence")

precondition(restored.unsignedCaptureContext == nil)
let context = CaptureContext(version: 1, capturedAt: Date(timeIntervalSince1970: 10),
    locationAuthorization: "authorized", location: .init(latitude: 0, longitude: 0,
        sampledAt: Date(timeIntervalSince1970: 9), horizontalAccuracy: 5,
        verticalAccuracy: nil, precision: "reduced", source: "simulated"), locationMissingReason: nil)
var withContext = record
withContext.unsignedCaptureContext = context
let reopened = try JSONDecoder().decode(EvidenceRecord.self, from: JSONEncoder().encode(withContext))
precondition(reopened.unsignedCaptureContext?.location?.source == "simulated")
precondition(reopened.unsignedCaptureContext?.location?.verticalAccuracy == nil)
let unchangedPayload = try reopened.manifest?.payload()
precondition(unchangedPayload == payload)
let contextVerification = try EvidenceStore.verify(reopened, photo: photo)
precondition(contextVerification.contains("passed"))
print("PASS capture context roundtrip and unchanged legacy signing payload")
let contextRoot = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try FileManager.default.createDirectory(at: contextRoot, withIntermediateDirectories: false)
defer { try? FileManager.default.removeItem(at: contextRoot) }
let firstCapture = try EvidenceStore.capture(photo, context: context, storageRoot: contextRoot,
    loadKey: { throw EvidenceError.unavailable })
let deniedContext = CaptureContext(version: 1, capturedAt: Date(timeIntervalSince1970: 11),
    locationAuthorization: "denied", location: nil, locationMissingReason: "denied")
let secondCapture = try EvidenceStore.capture(photo, context: deniedContext, storageRoot: contextRoot,
    loadKey: { throw EvidenceError.unavailable })
func readContext(_ directory: URL) throws -> CaptureContext? {
    try JSONDecoder().decode(EvidenceRecord.self,
        from: Data(contentsOf: directory.appendingPathComponent("record.json"))).unsignedCaptureContext
}
let firstContext = try readContext(firstCapture)
let secondContext = try readContext(secondCapture)
let originalBytes = try Data(contentsOf: firstCapture.appendingPathComponent("photo.jpg"))
precondition(originalBytes == photo)
precondition(firstContext?.location?.source == "simulated")
precondition(secondContext?.location == nil && secondContext?.locationMissingReason == "denied")
print("PASS committed capture snapshots stay distinct and original bytes are preserved")
