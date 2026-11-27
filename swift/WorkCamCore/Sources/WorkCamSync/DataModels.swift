// TM71: unified local data models + repository protocols - Swift
// reference implementation for the client data layer.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).
//
// The models mirror the SERVER wire contracts (docs/api/openapi.json)
// for sync fields and the TM70 recovery facts for pipeline state -
// with one deliberate difference: pipeline state is DERIVED (see
// EvidenceRecovery), never stored, so no model here carries it.

import Foundation
import WorkCamEvidence

// MARK: - domain models

public struct ProjectRecord: Codable, Sendable, Identifiable,
                             Equatable {
    public let projectID: String
    public var name: String
    public var description: String
    public var siteAddress: String
    public var status: String            // active | archived
    public var revision: Int
    public let createdAt: Date
    public var updatedAt: Date

    /// Identifiable (TM-04 fix: declared but never synthesized).
    public var id: String { projectID }

    public init(projectID: String, name: String,
                description: String = "", siteAddress: String = "",
                status: String = "active", revision: Int = 1,
                createdAt: Date, updatedAt: Date) {
        self.projectID = projectID
        self.name = name
        self.description = description
        self.siteAddress = siteAddress
        self.status = status
        self.revision = revision
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case name, description
        case siteAddress = "site_address"
        case status, revision
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

public struct PhotoRecord: Codable, Sendable, Identifiable, Equatable {
    public let photoID: String
    public let projectID: String
    public let originalSHA256: String?
    public var originalFilename: String
    public var mimeType: String
    public var byteSize: Int
    public var capturedAt: Date?          // client-claimed (untrusted)
    public var tags: Set<String>
    public var category: String
    public var note: String
    public var hasOriginalOnDisk: Bool    // F2 fact (TM70)
    public var isBound: Bool               // F3 fact
    public let createdAt: Date

    /// Identifiable (TM-04 fix: declared but never synthesized).
    public var id: String { photoID }

    public init(photoID: String, projectID: String,
                originalSHA256: String? = nil,
                originalFilename: String, mimeType: String,
                byteSize: Int, capturedAt: Date? = nil,
                tags: Set<String> = [], category: String = "",
                note: String = "", hasOriginalOnDisk: Bool = false,
                isBound: Bool = false, createdAt: Date) {
        self.photoID = photoID
        self.projectID = projectID
        self.originalSHA256 = originalSHA256
        self.originalFilename = originalFilename
        self.mimeType = mimeType
        self.byteSize = byteSize
        self.capturedAt = capturedAt
        self.tags = tags
        self.category = category
        self.note = note
        self.hasOriginalOnDisk = hasOriginalOnDisk
        self.isBound = isBound
        self.createdAt = createdAt
    }

    enum CodingKeys: String, CodingKey {
        case photoID = "photo_id"
        case projectID = "project_id"
        case originalSHA256 = "original_sha256"
        case originalFilename = "original_filename"
        case mimeType = "mime_type"
        case byteSize = "byte_size"
        case capturedAt = "captured_at"
        case tags, category, note
        case hasOriginalOnDisk = "has_original_on_disk"
        case isBound = "is_bound"
        case createdAt = "created_at"
    }
}

public struct IssueRecord: Codable, Sendable, Identifiable, Equatable {
    public enum Status: String, Codable, Sendable {
        case open, inProgress = "in_progress", resolved, reopened
    }
    public let issueID: String
    public let projectID: String
    public var title: String
    public var severity: String          // low | medium | high | critical
    public var status: Status
    public var remediationNote: String   // human-entered (v12)
    public var revision: Int
    public let createdAt: Date

    /// Identifiable (TM-04 fix: declared but never synthesized).
    public var id: String { issueID }

    public init(issueID: String, projectID: String, title: String,
                severity: String, status: Status = .open,
                remediationNote: String = "", revision: Int = 1,
                createdAt: Date) {
        self.issueID = issueID
        self.projectID = projectID
        self.title = title
        self.severity = severity
        self.status = status
        self.remediationNote = remediationNote
        self.revision = revision
        self.createdAt = createdAt
    }

    enum CodingKeys: String, CodingKey {
        case issueID = "issue_id"
        case projectID = "project_id"
        case title, severity, status
        case remediationNote = "remediation_note"
        case revision
        case createdAt = "created_at"
    }
}

public struct UploadSessionRecord: Codable, Sendable, Equatable {
    public let uploadID: String
    public let photoID: String
    public var claimedSHA256: String
    public var chunkSize: Int
    public var totalChunks: Int
    public var receivedChunks: Set<Int>
    public var status: String            // active | completed | cancelled
    public var updatedAt: Date

    public init(uploadID: String, photoID: String,
                claimedSHA256: String, chunkSize: Int, totalChunks: Int,
                receivedChunks: Set<Int> = [], status: String = "active",
                updatedAt: Date) {
        self.uploadID = uploadID
        self.photoID = photoID
        self.claimedSHA256 = claimedSHA256
        self.chunkSize = chunkSize
        self.totalChunks = totalChunks
        self.receivedChunks = receivedChunks
        self.status = status
        self.updatedAt = updatedAt
    }
}

// MARK: - repository protocols (TM73)

/// Local persistence. The Mac app implements over SQLite/GRDB; tests
/// use the in-memory doubles below. All single-object mutations are
/// UPSERTS (idempotent - crash-safe by the same discipline as the
/// server ledger).
public protocol LocalDataSource: AnyObject, Sendable {
    func projects() throws -> [ProjectRecord]
    func save(project: ProjectRecord) throws
    func photos(projectID: String?) throws -> [PhotoRecord]
    func save(photo: PhotoRecord) throws
    func issues(projectID: String?) throws -> [IssueRecord]
    func save(issue: IssueRecord) throws
    func uploadSessions(photoID: String) throws -> [UploadSessionRecord]
    func save(uploadSession: UploadSessionRecord) throws
}

/// Server-facing operations are the API client (TM66); the repository
/// composes local + remote behind an offline-first facade.
public protocol RemoteDataSource: Sendable {
    var api: WorkCamAPIClient { get }
}

/// Offline-first facade contract: reads always answer from LOCAL
/// (instant, offline); writes go local-first then through the Outbox;
/// sync is explicit (the app calls sync() when the network exists).
public protocol Repository: Sendable {
    func projects() throws -> [ProjectRecord]
    func save(project: ProjectRecord) throws
    func photos(projectID: String?) throws -> [PhotoRecord]
    func save(photo: PhotoRecord) throws
    func issues(projectID: String?) throws -> [IssueRecord]
    func save(issue: IssueRecord) throws
}

/// The reference offline-first repository: local store + outbox, no
/// hidden network magic. The Mac persistence layer plugs in via
/// LocalDataSource.
public final class OfflineFirstRepository: Repository,
                                          @unchecked Sendable {
    private let local: LocalDataSource
    private let outbox: OutboxEngine

    public init(local: LocalDataSource, outbox: OutboxEngine) {
        self.local = local
        self.outbox = outbox
    }

    public func projects() throws -> [ProjectRecord] {
        try local.projects()
    }

    public func save(project: ProjectRecord) throws {
        try local.save(project: project)
        // queue the sync op (upsert = idempotent replay is safe)
        try outbox.enqueue(OutboxOperation(
            operationID: "proj-upsert-\(project.projectID)",
            op: "project_update",
            payloadJSON: encodeOrEmpty(project)))
    }

    public func photos(projectID: String?) throws -> [PhotoRecord] {
        try local.photos(projectID: projectID)
    }

    public func save(photo: PhotoRecord) throws {
        try local.save(photo: photo)
        // photo metadata changes ride the sync batch; ORIGINAL BYTES
        // ride the chunked uploader (TM69) - never the outbox.
    }

    public func issues(projectID: String?) throws -> [IssueRecord] {
        try local.issues(projectID: projectID)
    }

    public func save(issue: IssueRecord) throws {
        try local.save(issue: issue)
        try outbox.enqueue(OutboxOperation(
            operationID: "issue-upsert-\(issue.issueID)",
            op: "issue_update",
            payloadJSON: encodeOrEmpty(issue)))
    }

    private func encodeOrEmpty(_ v: some Encodable) -> String {
        let enc = JSONEncoder()
        enc.dateEncodingStrategy = .iso8601
        return (try? String(
            data: enc.encode(AnyEncodableBox(v)),
            encoding: .utf8)) ?? "{}"
    }
}

struct AnyEncodableBox: Encodable {
    private let f: (Encoder) throws -> Void
    init(_ v: some Encodable) { f = v.encode }
    func encode(to e: Encoder) throws { try f(e) }
}

// MARK: - in-memory local double (tests / previews)

public final class InMemoryLocalDataSource: LocalDataSource,
                                             @unchecked Sendable {
    private var _projects: [ProjectRecord] = []
    private var _photos: [PhotoRecord] = []
    private var _issues: [IssueRecord] = []
    private var _uploads: [UploadSessionRecord] = []
    private let lock = NSLock()

    public init() {}

    public func projects() throws -> [ProjectRecord] {
        lock.lock(); defer { lock.unlock() }
        return _projects
    }
    public func save(project: ProjectRecord) {
        lock.lock(); defer { lock.unlock() }
        _projects.removeAll { $0.projectID == project.projectID }
        _projects.append(project)
    }
    public func photos(projectID: String?) throws -> [PhotoRecord] {
        lock.lock(); defer { lock.unlock() }
        return _photos.filter {
            projectID == nil || $0.projectID == projectID
        }
    }
    public func save(photo: PhotoRecord) {
        lock.lock(); defer { lock.unlock() }
        _photos.removeAll { $0.photoID == photo.photoID }
        _photos.append(photo)
    }
    public func issues(projectID: String?) throws -> [IssueRecord] {
        lock.lock(); defer { lock.unlock() }
        return _issues.filter {
            projectID == nil || $0.projectID == projectID
        }
    }
    public func save(issue: IssueRecord) {
        lock.lock(); defer { lock.unlock() }
        _issues.removeAll { $0.issueID == issue.issueID }
        _issues.append(issue)
    }
    public func uploadSessions(photoID: String) throws
            -> [UploadSessionRecord] {
        lock.lock(); defer { lock.unlock() }
        return _uploads.filter { $0.photoID == photoID }
    }
    public func save(uploadSession: UploadSessionRecord) {
        lock.lock(); defer { lock.unlock() }
        _uploads.removeAll { $0.uploadID == uploadSession.uploadID }
        _uploads.append(uploadSession)
    }
}
