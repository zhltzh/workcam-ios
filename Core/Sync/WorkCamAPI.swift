import CryptoKit
import Foundation
import Security

struct WorkCamAPIError: LocalizedError, Sendable {
    let status: Int
    let code: String
    let detail: String
    var errorDescription: String? { "HTTP \(status) · \(code) · \(detail)" }
}

actor WorkCamAPI {
    struct Receipt: Sendable {
        let mapping: [String: String]
        let result: [String: String]
    }
    private let base: URL
    private let session: URLSession
    private let credential: TokenSlot

    init(base: URL) throws {
        let loopback = ["127.0.0.1", "localhost", "::1", "[::1]"].contains(base.host ?? "")
        guard base.host != nil, base.user == nil, base.password == nil, base.query == nil, base.fragment == nil,
              base.path.isEmpty || base.path == "/",
              base.scheme == "https" || (loopback && base.scheme == "http") else {
            throw URLError(.unsupportedURL)
        }
        self.base = base
        credential = TokenSlot(account: evidenceHash(Data(base.absoluteString.utf8)))
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 55
        config.httpShouldSetCookies = false
        session = URLSession(configuration: config, delegate: RefuseRedirects(), delegateQueue: nil)
    }

    func connect(code: String) async throws {
        let key = try EvidenceKey.load()
        let publicKey = key.publicKey.x963Representation
        if let existing = try credential.read() {
            do {
                try await challenge(token: existing, key: key)
                return
            } catch let error as WorkCamAPIError where error.status == 401 {
                try credential.clear()
            }
        }
        guard !code.isEmpty else { throw WorkCamAPIError(status: 401, code: "enrollment_required", detail: "Enter an enrollment code.") }
        let enrolled = try await request("POST", "/api/v1/devices/enroll", json: [
            "public_key_b64": publicKey.base64EncodedString(), "device_id": evidenceHash(publicKey),
            "enrollment_code": code
        ])
        let token = try string(enrolled, "device_token")
        try await challenge(token: token, key: key)
        try credential.save(token)
    }

    private func challenge(token: String, key: SecureEnclave.P256.Signing.PrivateKey) async throws {
        let result = try await request("POST", "/api/v1/devices/challenge", token: token)
        let nonce = try string(result, "nonce")
        let signature = try key.signature(for: Data(nonce.utf8)).derRepresentation
        let result2 = try await request("POST", "/api/v1/devices/challenge/verify", json: [
            "device_token": token, "nonce": nonce, "signature_b64": signature.base64EncodedString()
        ], token: token)
        guard result2["verified"] as? Bool == true else { throw URLError(.cannotParseResponse) }
    }

    func submit(kind: String, id: String, payload: [String: String], integers: [String: Int] = [:]) async throws -> Receipt {
        var body: [String: Any] = payload
        for (key, value) in integers { body[key] = value }
        body["operation_id"] = id
        let response = try await authorized("POST", "/api/v1/sync/batch", json: [
            "operations": [["op": kind, "operation_id": id, "payload": body]]
        ])
        guard let receipts = response["receipts"] as? [[String: Any]], receipts.count == 1,
              let receipt = receipts.first, receipt["operation_id"] as? String == id else {
            throw URLError(.cannotParseResponse)
        }
        guard ["applied", "duplicate"].contains(receipt["status"] as? String ?? ""),
              receipt["error"] == nil || receipt["error"] is NSNull,
              let result = receipt["result"] as? [String: Any], result["status"] as? String != "rejected" else {
            let error = receipt["error"] as? [String: Any] ?? [:]
            let detail = (try? JSONSerialization.data(withJSONObject: error, options: [.sortedKeys]))
                .flatMap { String(data: $0, encoding: .utf8) } ?? ""
            throw WorkCamAPIError(status: 409, code: error["code"] as? String ?? "sync_rejected", detail: detail)
        }
        return Receipt(mapping: response["id_map"] as? [String: String] ?? [:], result: result.compactMapValues { $0 as? String })
    }

    func pullEvents(after cursor: Int64, filter: SyncInbox.Stream, limit: Int = 100) async throws -> SyncInbox.Page {
        guard cursor >= 0, (1...200).contains(limit) else { throw EvidenceError.invalid }
        var query = [URLQueryItem(name: "cursor", value: String(cursor)),
                     URLQueryItem(name: "limit", value: String(limit)),
                     URLQueryItem(name: "include_state", value: "true")]
        if filter != .all { query.append(URLQueryItem(name: "entity_type", value: filter.rawValue)) }
        let response = try await authorized("GET", "/api/v1/sync/events", query: query)
        guard let raw = response["items"] as? [[String: Any]],
              let next = response["cursor"] as? Int64, let more = response["has_more"] as? Bool else {
            throw URLError(.cannotParseResponse)
        }
        let entries = try raw.map { event -> SyncInbox.Event in
            guard let sequence = event["seq"] as? Int64, let kind = event["entity_type"] as? String,
                  let id = event["entity_id"] as? String, let action = event["action"] as? String,
                  let tombstone = event["tombstone"] as? Bool,
                  filter == .all || filter.rawValue == kind else { throw URLError(.cannotParseResponse) }
            let snapshot = try (event["state"] as? [String: Any]).map {
                try JSONSerialization.data(withJSONObject: $0, options: [.sortedKeys])
            }
            return .init(sequence: sequence, kind: kind, id: id, action: action,
                         tombstone: tombstone, snapshot: snapshot)
        }
        return .init(events: entries, cursor: next, more: more)
    }

    /// The server owns the resumable session. Retry opens it again and reconciles its received indexes.
    func upload(photoID: UUID, original: URL, expectedHash: String, stopAfter: Int? = nil) async throws {
        let file = try FileHandle(forReadingFrom: original)
        defer { try? file.close() }
        let chunkSize = 128 * 1024
        var digest = SHA256()
        var count = 0
        while let data = try file.read(upToCount: chunkSize), !data.isEmpty {
            try Task.checkCancellation()
            digest.update(data: data)
            count += data.count
        }
        let hash = digest.finalize().map { String(format: "%02x", $0) }.joined()
        guard hash == expectedHash, count > 0 else { throw EvidenceError.invalid }
        let path = "/api/v1/photos/\(photoID.uuidString.lowercased())"
        let integrity = try await authorized("GET", path + "/original/integrity")
        if integrityConfirmed(integrity, hash: hash) { return }
        let total = (count + chunkSize - 1) / chunkSize
        let opened = try await authorized("POST", path + "/original-session", json: [
            "claimed_sha256": hash, "chunk_size": chunkSize, "total_chunks": total, "total_bytes": count
        ])
        let uploadID = try string(opened, "upload_id")
        guard uploadID.utf8.count == 32, uploadID.utf8.allSatisfy({
            (48...57).contains($0) || (97...102).contains($0)
        }) else { throw URLError(.cannotParseResponse) }
        let route = path + "/original-session/" + uploadID
        guard opened["chunk_size"] as? Int == chunkSize, opened["total_chunks"] as? Int == total,
              let received = opened["received_chunks"] as? [Int],
              received.allSatisfy({ (0..<total).contains($0) }) else { throw URLError(.cannotParseResponse) }
        let done = Set(received)
        var sent = 0
        for index in 0..<total where !done.contains(index) {
            try Task.checkCancellation()
            try file.seek(toOffset: UInt64(index * chunkSize))
            let size = min(chunkSize, count - index * chunkSize)
            guard let bytes = try file.read(upToCount: size), bytes.count == size else { throw EvidenceError.invalid }
            let ack = try await authorized("PUT", route + "/chunk/\(index)", bytes: bytes,
                                           headers: ["X-Chunk-SHA256": evidenceHash(bytes)])
            guard ack["stored"] as? Bool == true || ack["duplicate"] as? Bool == true else {
                throw URLError(.cannotParseResponse)
            }
            sent += 1
            if sent == stopAfter { throw CancellationError() }
        }
        let completed = try await authorized("POST", route + "/complete")
        guard completed["bound"] as? Bool == true, completed["status"] as? String == "completed",
              integrityConfirmed(try await authorized("GET", path + "/original/integrity"), hash: hash) else {
            throw URLError(.cannotParseResponse)
        }
    }

    private func integrityConfirmed(_ json: [String: Any], hash: String) -> Bool {
        (json["original"] as? [String: Any])?["sha256"] as? String == hash && json["bound_to_photo"] as? Bool == true &&
            (json["claims"] as? [String: Any])?["file_integrity_verified"] as? Bool == true
    }

    private func authorized(_ method: String, _ path: String, json: [String: Any]? = nil,
                            bytes: Data? = nil, headers: [String: String] = [:],
                            query: [URLQueryItem] = []) async throws -> [String: Any] {
        guard let token = try credential.read() else {
            throw WorkCamAPIError(status: 401, code: "enrollment_required", detail: "Connect the device first.")
        }
        do { return try await request(method, path, json: json, bytes: bytes, headers: headers, token: token, query: query) }
        catch let error as WorkCamAPIError where error.status == 401 {
            if try credential.read() == token { try credential.clear() }
            throw error
        }
    }

    private func request(_ method: String, _ path: String, json: [String: Any]? = nil,
                         bytes: Data? = nil, headers: [String: String] = [:],
                         token: String? = nil, query: [URLQueryItem] = []) async throws -> [String: Any] {
        try Task.checkCancellation()
        let endpoint = base.appendingPathComponent(String(path.dropFirst()))
        guard var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false) else {
            throw URLError(.badURL)
        }
        if !query.isEmpty { components.queryItems = query }
        guard let url = components.url else { throw URLError(.badURL) }
        var request = URLRequest(url: url, timeoutInterval: 15)
        request.httpMethod = method
        if let token { request.setValue(token, forHTTPHeaderField: "X-Device-Token") }
        for (key, value) in headers { request.setValue(value, forHTTPHeaderField: key) }
        if let json {
            request.httpBody = try JSONSerialization.data(withJSONObject: json, options: [.sortedKeys])
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        } else if let bytes {
            request.httpBody = bytes
            request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        guard (200..<300).contains(http.statusCode) else {
            let detail = object?["detail"] as? [String: Any]
            throw WorkCamAPIError(status: http.statusCode, code: detail?["code"] as? String ?? "request_failed",
                                 detail: detail?["message"] as? String ?? "Server rejected the request.")
        }
        guard let object else { throw URLError(.cannotParseResponse) }
        return object
    }

    private func string(_ json: [String: Any], _ key: String) throws -> String {
        guard let value = json[key] as? String, !value.isEmpty else { throw URLError(.cannotParseResponse) }
        return value
    }


}

private final class RefuseRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

private struct TokenSlot {
    let account: String
    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "WorkCam.BackendToken",
         kSecAttrAccount as String: account]
    }
    func read() throws -> String? {
        var args = query
        args[kSecReturnData as String] = true
        var result: CFTypeRef?
        let status = SecItemCopyMatching(args as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw EvidenceError.keychain(status) }
        guard let data = result as? Data, let token = String(data: data, encoding: .utf8) else { throw EvidenceError.invalid }
        return token
    }
    func save(_ token: String) throws {
        let value = Data(token.utf8)
        let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: value] as CFDictionary)
        if status == errSecSuccess { return }
        guard status == errSecItemNotFound else { throw EvidenceError.keychain(status) }
        var args = query
        args[kSecValueData as String] = value
        args[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let inserted = SecItemAdd(args as CFDictionary, nil)
        guard inserted == errSecSuccess else { throw EvidenceError.keychain(inserted) }
    }
    func clear() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw EvidenceError.keychain(status) }
    }
}

/// Per endpoint, durable FIFO with explicit dependencies. Credentials are not part of the journal.
actor ProjectSyncQueue {
    struct Job: Codable, Sendable {
        let id: String
        let kind: String
        var text: [String: String] = [:]
        var integers: [String: Int] = [:]
        var references: [String: String] = [:]
        var consumes: String? = nil
        var original: URL? = nil
        var hash: String? = nil
        var attempts: Int? = nil
        var phase: Phase? = nil
        var issue: String? = nil
    }

    enum Phase: String, Codable, Sendable {
        case pending, sending, retry, conflict, failed
    }

    private struct Journal: Codable {
        var jobs: [Job] = []
        var completed: Set<String> = []
        var mappings: [String: String] = [:]
    }

    private let file: URL
    private var journal: Journal
    private var running = false

    init(file: URL) throws {
        self.file = file
        do { journal = try JSONDecoder().decode(Journal.self, from: Data(contentsOf: file)) }
        catch let error as CocoaError where error.code == .fileReadNoSuchFile { journal = Journal() }
    }

    func enqueue(_ jobs: [Job]) throws {
        var next = journal
        var ids = journal.completed.union(journal.jobs.map(\.id))
        for job in jobs where ids.insert(job.id).inserted { next.jobs.append(job) }
        try persist(next)
    }

    func pending() -> Int { journal.jobs.count }

    func pendingDetails() -> String {
        guard let job = journal.jobs.first else { return "Queue is empty." }
        let phase = job.phase == .sending ? Phase.retry : job.phase ?? .pending
        return "\(journal.jobs.count) pending · \(phase.rawValue.capitalized) · attempt \(job.attempts ?? 0)"
    }

    func flush(using api: WorkCamAPI) async throws {
        guard !running else { throw CocoaError(.fileLocking) }
        running = true
        defer { running = false }
        while let job = journal.jobs.first {
            try Task.checkCancellation()
            var attempt = journal
            attempt.jobs[0].attempts = (job.attempts ?? 0) + 1
            attempt.jobs[0].phase = .sending
            attempt.jobs[0].issue = nil
            try persist(attempt)
            do {
                try await send(job, using: api)
            } catch {
                var failure = journal
                if let apiError = error as? WorkCamAPIError {
                    failure.jobs[0].phase = apiError.status == 409 ? .conflict :
                        (apiError.status == 401 || apiError.status >= 500 ? .retry : .failed)
                    failure.jobs[0].issue = apiError.code
                } else {
                    failure.jobs[0].phase = error is EvidenceError ? .failed : .retry
                }
                try persist(failure)
                throw error
            }
        }
    }

    private func send(_ job: Job, using api: WorkCamAPI) async throws {
        var mapping: [String: String] = [:]
        if job.kind == "upload" {
            guard let consumes = job.consumes, let id = journal.mappings[consumes].flatMap(UUID.init(uuidString:)),
                  let file = job.original, let hash = job.hash else { throw EvidenceError.invalid }
            try await api.upload(photoID: id, original: file, expectedHash: hash)
        } else {
            var payload = job.text
            for (key, dependency) in job.references {
                guard let remote = journal.mappings[dependency] else { throw EvidenceError.invalid }
                payload[key] = remote
            }
            let receipt = try await api.submit(kind: job.kind, id: job.id, payload: payload, integers: job.integers)
            mapping = receipt.mapping
            if let local = job.text["local_id"], mapping[local] == nil {
                let keys = ["server_photo_id", "server_issue_id", "server_project_id"]
                mapping[local] = keys.compactMap { receipt.result[$0] }.first
                guard mapping[local].flatMap(UUID.init(uuidString:)) != nil else { throw URLError(.cannotParseResponse) }
            }
        }
        var next = journal
        next.mappings.merge(mapping) { _, new in new }
        next.completed.insert(job.id)
        next.jobs.removeFirst()
        try persist(next)
    }

    private func persist(_ next: Journal) throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(next).write(to: file, options: [.atomic, .completeFileProtection])
        journal = next
    }
}

extension ProjectSyncQueue.Job {
    /// Queue first publication and subsequent forward issue transitions. An initial operation keeps
    /// its original idempotency body; existing project edits can be reconciled separately.
    static func publication(project: WorkProject, photos: [WorkPhoto], issues: [InspectionIssue]) throws -> [Self] {
        let projectID = project.id.uuidString
        var jobs = [Self(id: "project-" + projectID, kind: "project_create",
                         text: ["local_id": projectID, "name": project.name])]
        for photo in photos {
            let data = try Data(contentsOf: photo.url, options: .mappedIfSafe)
            let hash = evidenceHash(data)
            let local = projectID + ":" + photo.id
            let key = evidenceHash(Data(local.utf8))
            jobs.append(Self(id: "photo-" + key, kind: "photo_register",
                             text: ["local_id": local, "original_sha256": hash, "idempotency_key": key,
                                    "original_filename": "photo.jpg", "mime_type": "image/jpeg",
                                    "captured_at": ISO8601DateFormatter().string(from: photo.capturedAt),
                                    "device_id": "WorkCam iOS"], integers: ["byte_size": data.count],
                             references: ["server_project_id": projectID]))
            jobs.append(Self(id: "upload-" + key, kind: "upload", consumes: local, original: photo.url, hash: hash))
        }
        for issue in issues {
            let id = issue.id.uuidString
            jobs.append(Self(id: "issue-" + id, kind: "issue_create",
                             text: ["local_id": id, "title": "Issue \(issue.number)",
                                    "description": issue.description, "severity": issue.severity.rawValue],
                             references: ["server_project_id": projectID]))
            for (role, photoID) in issue.photos.sorted(by: { $0.key < $1.key }) {
                guard photos.contains(where: { $0.id == photoID }) else { continue }
                let linkHash = evidenceHash(Data(photoID.utf8))
                jobs.append(Self(id: "issue-link-\(id)-\(role)-\(linkHash)", kind: "issue_photo_link",
                                 text: ["role": role], references: ["server_issue_id": id,
                                     "server_photo_id": projectID + ":" + photoID]))
            }
            let ladder: [InspectionIssue.Status] = [.open, .inProgress, .resolved, .closed]
            if let end = ladder.firstIndex(of: issue.status), end > 0 {
                for index in 1...end {
                    jobs.append(Self(id: "transition-\(id)-\(index)", kind: "issue_transition",
                                     text: ["target_status": ladder[index].rawValue],
                                     integers: ["expected_revision": index],
                                     references: ["server_issue_id": id]))
                }
            }
        }
        return jobs
    }
}

#if DEBUG && targetEnvironment(simulator)
enum SwiftNetworkFixture {
    static func run() async throws {
        let endpoint = URL(string: "http://127.0.0.1:18765")!
        let api = try WorkCamAPI(base: endpoint)
        try await api.connect(code: "workcam-int02-local-fixture")
        let sameCredentials = try WorkCamAPI(base: endpoint)
        try await sameCredentials.connect(code: "")
        let slot = TokenSlot(account: evidenceHash(Data(endpoint.absoluteString.utf8)))
        try slot.save("invalid-local-test-token")
        try await sameCredentials.connect(code: "workcam-int02-local-fixture")
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("Int02-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let original = root.appendingPathComponent("photo.jpg")
        let bytes = Data((0..<280_000).map { UInt8(truncatingIfNeeded: $0) })
        try bytes.write(to: original)
        let project = WorkProject(id: UUID(), name: "Swift simulator integration")
        let photo = WorkPhoto(url: original, capturedAt: .now, localEntry: original)
        let issue = InspectionIssue(id: UUID(), number: 1, description: "Synthetic defect", severity: .high,
                                    status: .inProgress, photos: ["before": photo.id], history: [])
        let jobs = try ProjectSyncQueue.Job.publication(project: project, photos: [photo], issues: [issue])
        let projectJob = jobs[0]
        let photoJob = jobs[1]
        let projectReceipt = try await api.submit(kind: projectJob.kind, id: projectJob.id, payload: projectJob.text)
        let projectID = try requiredID(projectReceipt.result["server_project_id"])
        var metadata = photoJob.text
        metadata["server_project_id"] = projectID.uuidString.lowercased()
        let photoReceipt = try await api.submit(kind: photoJob.kind, id: photoJob.id, payload: metadata, integers: photoJob.integers)
        let photoID = try requiredID(photoReceipt.result["server_photo_id"])
        let hash = evidenceHash(bytes)
        do {
            try await api.upload(photoID: photoID, original: original, expectedHash: hash, stopAfter: 1)
            throw EvidenceError.invalid
        } catch is CancellationError {}
        let journal = root.appendingPathComponent("outbox.json")
        let first = try ProjectSyncQueue(file: journal)
        try await first.enqueue(jobs)
        let restarted = try ProjectSyncQueue(file: journal)
        guard await restarted.pending() == jobs.count else { throw EvidenceError.invalid }
        try await restarted.flush(using: sameCredentials)
        try await restarted.enqueue(jobs)
        let unchanged = try Data(contentsOf: original)
        guard await restarted.pending() == 0, unchanged == bytes else { throw EvidenceError.invalid }
        let projectRemote = projectID.uuidString.lowercased()
        let rename = UUID().uuidString
        _ = try await api.submit(kind: "project_update", id: rename,
            payload: ["server_project_id": projectRemote, "name": "Renamed simulator project"],
            integers: ["expected_revision": 1])
        let archive = UUID().uuidString
        _ = try await api.submit(kind: "project_archive", id: archive,
            payload: ["server_project_id": projectRemote], integers: ["expected_revision": 2])
        let inboxFile = root.appendingPathComponent("projects.json")
        let fullInbox = try SyncInbox(file: root.appendingPathComponent("all.json"))
        let projectsInbox = try SyncInbox(file: inboxFile)
        try await fullInbox.synchronize(using: api, filter: .all, limit: 1)
        try await projectsInbox.synchronize(using: api, filter: .project, limit: 1)
        let reopenedInbox = try SyncInbox(file: inboxFile)
        let snapshots = await reopenedInbox.snapshots()
        let savedCursor = await reopenedInbox.cursor()
        guard savedCursor > 0, snapshots.allSatisfy({ $0.kind == "project" }),
              snapshots.contains(where: {
                $0.id == projectRemote && $0.summary == "Renamed simulator project · archived"
              }) else { throw EvidenceError.invalid }
        try await reopenedInbox.synchronize(using: api, filter: .project, limit: 1)
        guard await reopenedInbox.cursor() == savedCursor else { throw EvidenceError.invalid }
        let issueJob = jobs[3]
        var issuePayload = issueJob.text
        issuePayload["server_project_id"] = projectID.uuidString.lowercased()
        let issueReceipt = try await api.submit(kind: issueJob.kind, id: issueJob.id, payload: issuePayload)
        let issueID = try requiredID(issueReceipt.result["server_issue_id"])
        let conflict = UUID().uuidString
        for _ in 0..<2 {
            do {
                _ = try await api.submit(kind: "issue_transition", id: conflict, payload: [
                    "server_issue_id": issueID.uuidString.lowercased(), "target_status": "resolved"
                ], integers: ["expected_revision": 1])
                throw EvidenceError.invalid
            } catch let error as WorkCamAPIError where error.code == "revision_conflict" {}
        }
    }

    private static func requiredID(_ value: Any?) throws -> UUID {
        guard let string = value as? String, let id = UUID(uuidString: string) else { throw EvidenceError.invalid }
        return id
    }
}
#endif

/// Durable server inbox. Snapshot data and cursor are one atomic file per stream.
/// The held server state is intentionally separate from editable local project records.
actor SyncInbox {
    enum Stream: String, CaseIterable, Identifiable, Sendable {
        case all, project, issue, photo, annotation
        var id: String { rawValue }
    }
    struct Event: Codable, Sendable, Identifiable {
        let sequence: Int64
        let kind: String
        let id: String
        let action: String
        let tombstone: Bool
        let snapshot: Data?
        var key: String { kind + ":" + id }

        var projectRevision: Int? {
            guard kind == "project", let snapshot,
                  let item = (try? JSONSerialization.jsonObject(with: snapshot)) as? [String: Any],
                  item["status"] as? String == "active" else { return nil }
            return item["revision"] as? Int
        }

        var summary: String {
            guard let snapshot, let item = (try? JSONSerialization.jsonObject(with: snapshot)) as? [String: Any] else {
                return action.replacingOccurrences(of: "_", with: " ").capitalized
            }
            let title = item["name"] as? String ?? item["title"] as? String ??
                item["original_filename"] as? String ?? action
            let state = item["status"] as? String
            return [title, state].compactMap { $0 }.joined(separator: " · ")
        }
    }
    struct Page: Sendable {
        let events: [Event]
        let cursor: Int64
        let more: Bool
    }
    private struct State: Codable {
        var cursor: Int64 = 0
        var entities: [String: Event] = [:]
    }

    private let file: URL
    private var state: State
    private var pulling = false

    init(file: URL) throws {
        self.file = file
        do { state = try JSONDecoder().decode(State.self, from: Data(contentsOf: file)) }
        catch let error as CocoaError where error.code == .fileReadNoSuchFile { state = State() }
    }

    func cursor() -> Int64 { state.cursor }
    func snapshots() -> [Event] { state.entities.values.sorted { $0.sequence > $1.sequence } }

    func synchronize(using api: WorkCamAPI, filter: Stream, limit: Int = 100) async throws {
        guard !pulling else { throw CocoaError(.fileLocking) }
        pulling = true
        defer { pulling = false }
        for _ in 0..<500 {
            let page = try await api.pullEvents(after: state.cursor, filter: filter, limit: limit)
            try Task.checkCancellation()
            try apply(page)
            if !page.more { return }
        }
        throw WorkCamAPIError(status: 0, code: "page_limit", detail: "Partial download saved. Pull again to continue.")
    }

    func apply(_ page: Page) throws {
        let sequences = page.events.map(\.sequence)
        guard page.cursor >= state.cursor, !page.more || page.cursor > state.cursor,
              sequences == sequences.sorted(), Set(sequences).count == sequences.count,
              sequences.allSatisfy({ $0 > state.cursor && $0 <= page.cursor }),
              !page.more || sequences.last == page.cursor else { throw URLError(.cannotParseResponse) }
        var next = state
        for event in page.events { next.entities[event.key] = event }
        next.cursor = page.cursor
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(next).write(to: file, options: [.atomic, .completeFileProtection])
        state = next
    }
}
