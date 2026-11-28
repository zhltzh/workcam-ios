import Foundation
import CryptoKit
import Security

struct EvidenceManifest: Codable {
    var version = "1.1"
    var record_id: String
    var sequence_number: Int
    var previous_record_hash: String?
    var photo_hash: String
    var capture_timestamp: String
    var device_reported_time: String
    var device_id: String
    var location: String? = nil
    var signature: String
    var public_key: String

    func payload() throws -> Data {
        guard version == "1.1", location == nil, sequence_number >= 0, sequence_number <= 1_000_000,
              photo_hash.count == 64, photo_hash.allSatisfy({ "0123456789abcdef".contains($0) }) else { throw EvidenceError.invalid }
        let fields = [version, record_id, photo_hash, capture_timestamp, device_reported_time, device_id, previous_record_hash ?? "none"]
        guard fields.allSatisfy({ !$0.utf8.contains(0x0a) && !$0.utf8.contains(0x0d) }) else { throw EvidenceError.invalid }
        return Data(([
            "WorkCam-Sig-v1.1", "version=\(version)", "record_id=\(record_id)",
            "sequence_number=\(sequence_number)", "previous_record_hash=\(previous_record_hash ?? "none")",
            "photo_hash=\(photo_hash)", "capture_timestamp=\(capture_timestamp)",
            "device_reported_time=\(device_reported_time)", "device_id=\(device_id)", "location=none"
        ].joined(separator: "\n") + "\n").utf8)
    }
}

enum EvidenceError: Error { case noExportableRecords, invalid, unavailable, keychain(OSStatus) }

func evidenceHash(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

struct EvidenceKey {
    // dataRepresentation is an opaque device-bound wrapped reference, never raw private scalar bytes.
    static func load(createIfMissing: Bool = true) throws -> SecureEnclave.P256.Signing.PrivateKey {
        guard SecureEnclave.isAvailable else { throw EvidenceError.unavailable }
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "WorkCam.M003", kSecAttrAccount as String: "signing-key"]
        var read = query
        read[kSecReturnData as String] = true
        var result: CFTypeRef?
        let status = SecItemCopyMatching(read as CFDictionary, &result)
        if status == errSecSuccess {
            guard let data = result as? Data else { throw EvidenceError.invalid }
            return try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: data)
        }
        guard status == errSecItemNotFound else { throw EvidenceError.keychain(status) }
        guard createIfMissing else { throw EvidenceError.unavailable }
        let key = try SecureEnclave.P256.Signing.PrivateKey()
        var insert = query
        insert[kSecValueData as String] = key.dataRepresentation
        insert[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let added = SecItemAdd(insert as CFDictionary, nil)
        guard added == errSecSuccess else { throw EvidenceError.keychain(added) }
        return key
    }
}

/// Local capture metadata. This is outside the v1.1 signed manifest.
struct CaptureContext: Codable, Sendable {
    let version: Int
    let capturedAt: Date
    let locationAuthorization: String
    let location: LocationFix?
    let locationMissingReason: String?

    struct LocationFix: Codable, Sendable {
        let latitude: Double
        let longitude: Double
        let sampledAt: Date
        let horizontalAccuracy: Double
        let verticalAccuracy: Double?
        let precision: String
        let source: String
    }
}

struct EvidenceRecord: Codable {
    var manifest: EvidenceManifest?
    var photoID: String
    var sha256: String
    var publicKeyID: String?
    var status: String
    var failure: String?
    var unsignedCaptureContext: CaptureContext? = nil
}

struct ProjectEvidenceLink: Codable, Sendable {
    let projectID: UUID
    let timestamp: Int64
}

struct EvidenceStore {
    private static let writeLock = NSLock()

    /// Resume promotion of complete transactions. Incomplete or unreadable stages
    /// remain on disk, including their original photo, for later inspection.
    @discardableResult
    static func recoverPending(in root: URL) throws -> Int {
        writeLock.lock()
        defer { writeLock.unlock() }
        return try promotePending(in: root)
    }

    private static func promotePending(in root: URL) throws -> Int {
        let files = FileManager.default
        let prefix = ".pending-"
        var promoted = 0
        for stage in try files.contentsOfDirectory(at: root, includingPropertiesForKeys: nil) {
            let name = stage.lastPathComponent
            guard name.hasPrefix(prefix) else { continue }
            let id = String(name.dropFirst(prefix.count))
            guard UUID(uuidString: id) != nil else { continue }
            let destination = root.appendingPathComponent(id, isDirectory: true)
            let metadataURL = stage.appendingPathComponent("record.json")
            let signatureFile = stage.appendingPathComponent("signature.der")
            guard !files.fileExists(atPath: destination.path),
                  let photo = try? Data(contentsOf: stage.appendingPathComponent("photo.jpg")) else { continue }
            if !files.fileExists(atPath: metadataURL.path),
               !files.fileExists(atPath: signatureFile.path),
               let intent = try? Data(contentsOf: stage.appendingPathComponent("capture-intent.sha256")),
               Data(evidenceHash(photo).utf8) == intent {
                let recovered = EvidenceRecord(photoID: id, sha256: evidenceHash(photo), status: "Unsigned",
                                               failure: "Capture interrupted before signing completed")
                guard (try? JSONEncoder().encode(recovered).write(to: metadataURL,
                      options: [.atomic, .completeFileProtection])) != nil else { continue }
            }
            guard let metadata = try? Data(contentsOf: metadataURL),
                  let record = try? JSONDecoder().decode(EvidenceRecord.self, from: metadata),
                  record.photoID == id,
                  (try? verify(record, photo: photo)) != nil else { continue }
            if let manifest = record.manifest {
                guard let signature = Data(base64Encoded: manifest.signature) else { continue }
                if !files.fileExists(atPath: signatureFile.path),
                   let intent = try? Data(contentsOf: stage.appendingPathComponent("capture-intent.sha256")),
                   intent == Data(record.sha256.utf8) {
                    // The manifest signature already verified against the original above.
                    try? signature.write(to: signatureFile, options: [.atomic, .completeFileProtection])
                }
                guard (try? Data(contentsOf: signatureFile)) == signature else { continue }
            } else if files.fileExists(atPath: signatureFile.path) {
                continue
            }
            // moveItem rejects an existing destination. A failed move leaves the
            // stage available for a future attempt.
            if (try? files.moveItem(at: stage, to: destination)) != nil {
                promoted += 1
            }
        }
        return promoted
    }

    static func root() throws -> URL {
        let base: URL
        #if DEBUG && targetEnvironment(simulator)
        if let fixture = Night04Fixture.root {
            base = fixture
        } else {
            base = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        }
        #else
        base = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        #endif
        var url = base.appendingPathComponent("Evidence", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try url.setResourceValues(values)
        return url
    }

    /// Exports copies of public evidence only; never reads or exports private-key references.
    static func exportPackages(sourceRoot: URL? = nil) throws -> URL {
        let source = try sourceRoot ?? root()
        try recoverPending(in: source)
        let output = FileManager.default.temporaryDirectory.appendingPathComponent("WorkCam-Evidence-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: false,
                                                attributes: [.protectionKey: FileProtectionType.complete])
        var finished = false
        defer { if !finished { try? FileManager.default.removeItem(at: output) } }
        var exportedCount = 0
        for item in try FileManager.default.contentsOfDirectory(at: source, includingPropertiesForKeys: nil) {
            guard UUID(uuidString: item.lastPathComponent) != nil else { continue }
            let record = try JSONDecoder().decode(EvidenceRecord.self, from: Data(contentsOf: item.appendingPathComponent("record.json")))
            let photo = try Data(contentsOf: item.appendingPathComponent("photo.jpg"))
            _ = try verifyCommitted(record, in: item, photo: photo)
            exportedCount += 1
            let destination = output.appendingPathComponent(item.lastPathComponent)
            try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
            try photo.write(to: destination.appendingPathComponent("photo.jpg"), options: .atomic)
            try JSONEncoder().encode(record).write(to: destination.appendingPathComponent("record.json"), options: .atomic)
            if let manifest = record.manifest {
                guard let key = Data(base64Encoded: manifest.public_key),
                      let signature = Data(base64Encoded: manifest.signature) else { throw EvidenceError.invalid }
                try manifest.payload().write(to: destination.appendingPathComponent("payload.bin"), options: .atomic)
                try key.write(to: destination.appendingPathComponent("public-key.x963"), options: .atomic)
                try signature.write(to: destination.appendingPathComponent("signature.der"), options: .atomic)
            }
        }
        guard exportedCount > 0 else { throw EvidenceError.noExportableRecords }
        finished = true
        return output
    }

    static func capture(
        _ data: Data,
        recordID: UUID = UUID(),
        projectLink: ProjectEvidenceLink? = nil,
        context: CaptureContext? = nil,
        storageRoot: URL? = nil,
        loadKey: () throws -> SecureEnclave.P256.Signing.PrivateKey = { try EvidenceKey.load() },
        writeOriginal: (Data, URL) throws -> Void = {
            try $0.write(to: $1, options: [.atomic, .completeFileProtection])
        }
    ) throws -> URL {
        writeLock.lock()
        defer { writeLock.unlock() }
        let root = try storageRoot ?? root()
        _ = try? promotePending(in: root)
        let id = recordID.uuidString.lowercased()
        let stage = root.appendingPathComponent(".pending-" + id, isDirectory: true)
        try FileManager.default.createDirectory(at: stage, withIntermediateDirectories: false)
        if let projectLink {
            try JSONEncoder().encode(projectLink).write(to: stage.appendingPathComponent("project-link.json"),
                                                        options: [.atomic, .completeFileProtection])
        }
        let photo = stage.appendingPathComponent("photo.jpg")
        let expectedHash = evidenceHash(data)
        try Data(expectedHash.utf8).write(to: stage.appendingPathComponent("capture-intent.sha256"),
                                         options: [.atomic, .completeFileProtection])
        try writeOriginal(data, photo)
        let hash = evidenceHash(try Data(contentsOf: photo))
        guard hash == expectedHash else { throw EvidenceError.invalid }
        var record = EvidenceRecord(photoID: id, sha256: hash, status: "Unsigned", unsignedCaptureContext: context)
        do {
            let key = try loadKey()
            let publicData = key.publicKey.x963Representation
            let time = ISO8601DateFormatter().string(from: Date())
            var manifest = EvidenceManifest(record_id: id, sequence_number: 0, photo_hash: hash,
                capture_timestamp: time, device_reported_time: time,
                device_id: evidenceHash(publicData), signature: "", public_key: publicData.base64EncodedString())
            let signature = try key.signature(for: manifest.payload())
            manifest.signature = signature.derRepresentation.base64EncodedString()
            record.manifest = manifest
            record.publicKeyID = evidenceHash(publicData)
            record.status = "Signed — local verification pending"
        } catch {
            record.failure = String(describing: error)
        }
        try JSONEncoder().encode(record).write(to: stage.appendingPathComponent("record.json"), options: [.atomic, .completeFileProtection])
        if let manifest = record.manifest, let signature = Data(base64Encoded: manifest.signature) {
            try signature.write(to: stage.appendingPathComponent("signature.der"), options: [.atomic, .completeFileProtection])
        }
        let final = root.appendingPathComponent(id, isDirectory: true)
        try FileManager.default.moveItem(at: stage, to: final)
        return final
    }

    static func verify(_ record: EvidenceRecord, photo: Data, expectedPublicKey: Data? = nil) throws -> String {
        guard evidenceHash(photo) == record.sha256 else { throw EvidenceError.invalid }
        guard let m = record.manifest else {
            guard record.publicKeyID == nil else { throw EvidenceError.invalid }
            return "Unsigned"
        }
        guard m.record_id == record.photoID, m.photo_hash == record.sha256,
              let publicData = Data(base64Encoded: m.public_key),
              evidenceHash(publicData) == record.publicKeyID,
              let signature = Data(base64Encoded: m.signature) else { throw EvidenceError.invalid }
        if let expectedPublicKey {
            guard publicData == expectedPublicKey, m.device_id == evidenceHash(expectedPublicKey) else { throw EvidenceError.invalid }
        }
        let key: P256.Signing.PublicKey
        let sig: P256.Signing.ECDSASignature
        do {
            key = try P256.Signing.PublicKey(x963Representation: publicData)
            sig = try P256.Signing.ECDSASignature(derRepresentation: signature)
        } catch { throw EvidenceError.invalid }
        guard key.isValidSignature(sig, for: try m.payload()) else { throw EvidenceError.invalid }
        return expectedPublicKey == nil ? "Signed — local verification passed; device key not checked" : "Signed — local verification passed; matches this installation key"
    }

    static func verifyCommitted(_ record: EvidenceRecord, in directory: URL, photo: Data,
                                expectedPublicKey: Data? = nil) throws -> String {
        guard let directoryID = UUID(uuidString: directory.lastPathComponent),
              UUID(uuidString: record.photoID) == directoryID else { throw EvidenceError.invalid }
        let result = try verify(record, photo: photo, expectedPublicKey: expectedPublicKey)
        let detached = directory.appendingPathComponent("signature.der")
        if let manifest = record.manifest {
            guard let expected = Data(base64Encoded: manifest.signature),
                  (try? Data(contentsOf: detached)) == expected else { throw EvidenceError.invalid }
        } else {
            guard !FileManager.default.fileExists(atPath: detached.path) else { throw EvidenceError.invalid }
        }
        return result
    }

    static func inspect(_ directory: URL, expectedPublicKey: Data? = nil) -> String {
        do {
            let record = try JSONDecoder().decode(EvidenceRecord.self, from: Data(contentsOf: directory.appendingPathComponent("record.json")))
            let trustedKey = try record.manifest.map { _ in
                try expectedPublicKey ?? EvidenceKey.load(createIfMissing: false).publicKey.x963Representation
            }
            if record.manifest == nil, FileManager.default.fileExists(atPath: directory.appendingPathComponent("signature.der").path) {
                throw EvidenceError.invalid
            }
            let result = try verify(record, photo: Data(contentsOf: directory.appendingPathComponent("photo.jpg")), expectedPublicKey: trustedKey)
            if let m = record.manifest {
                guard try Data(contentsOf: directory.appendingPathComponent("signature.der")) == Data(base64Encoded: m.signature) else { throw EvidenceError.invalid }
            }
            try Data(result.utf8).write(to: directory.appendingPathComponent("local-status.txt"), options: [.atomic, .completeFileProtection])
            return "Photo ID: \(record.photoID)\nSHA-256: \(record.sha256)\nSecure Enclave: \(record.manifest == nil ? "Unavailable or failed" : "Matches installation key; hardware acceptance Pending")\nSignature / Local Verification: \(result)\n\(record.failure ?? "")"
        } catch { return "Verification Failed: \(directory.lastPathComponent) — \(error)" }
    }

    #if DEBUG
    static func tamperReport() -> String {
        do {
            let directory = try root()
            try recoverPending(in: directory)
            let directories = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            guard let directory = directories.first(where: { !$0.lastPathComponent.hasPrefix(".") }),
                  var record = try? JSONDecoder().decode(EvidenceRecord.self, from: Data(contentsOf: directory.appendingPathComponent("record.json"))),
                  record.manifest != nil else { return "Tamper test requires signed evidence" }
            let photo = try Data(contentsOf: directory.appendingPathComponent("photo.jpg"))
            _ = try verify(record, photo: photo)
            var changedPhoto = photo
            changedPhoto.append(0)
            do {
                _ = try verify(record, photo: changedPhoto)
                return "FAIL: photo tampering accepted"
            } catch EvidenceError.invalid {
                // Expected mismatch. Other errors propagate to setup failure.
            }
            record.manifest?.device_id.append("tampered")
            do {
                _ = try verify(record, photo: photo)
                return "FAIL: field tampering accepted"
            } catch EvidenceError.invalid {
                // Expected mismatch. Other errors propagate to setup failure.
            }
            return "PASS: photo and signed-field tampering rejected (in-memory copies)"
        } catch { return "Tamper test setup failed: \(error)" }
    }

    #endif

    static func report() -> String {
        do {
            let directory = try root()
            try recoverPending(in: directory)
            let directories = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil).sorted { $0.lastPathComponent < $1.lastPathComponent }
            if directories.isEmpty { return "No evidence records" }
            return directories.map { url in
                url.lastPathComponent.hasPrefix(".pending-") ? "Incomplete record isolated: \(url.lastPathComponent)" : inspect(url)
            }.joined(separator: "\n\n")
        } catch { return "Evidence read failed: \(error)" }
    }
}
