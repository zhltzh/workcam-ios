# WorkCam Swift Codable 模型（B02-W026 文本交付）

版本 v1 · 分支 `dev/windows-b02-w026-api-contract` · 基线 `31917e3`（W013-R1）

本文是 **Mac 端接入用的模型文本**，不修改任何 Mac 生产代码（App/ 与 Core/ 由 Mac 侧维护）。命名体系沿用 B02-W014 交付的 `b02-w014-offline-sync-spec.md` §6/§7 与 `design/swiftui/swiftui-mapping.md`：camelCase 属性 + **显式 CodingKeys**（不依赖 `.convertFromSnakeCase` 解码策略，避免编码器差异）+ `Sendable`。

字段与 **基线真实 JSON 逐一对齐**：来源为 `docs/api/openapi.json`（机器导出）与 `docs/api/b02-w026-mock-data.json`（70 次真实调用的响应），并由 `backend/tests/test_b02_w026_contract.py` 的结构重放兜底——服务端字段改名会先红测试。

标注 `⚠️` 的行为差异点在文末「接入注意」集中列出。

```swift
import Foundation

// MARK: - Error envelope (§1.2 of the API reference)

struct ApiErrorEnvelope: Codable, Sendable {
    var detail: ApiErrorBody
}

struct ApiErrorBody: Codable, Sendable {
    var code: String            // machine code, e.g. "revision_conflict"
    var message: String
    var details: [String: StringValue]   // free-form typed values; usually empty

    enum CodingKeys: String, CodingKey {
        case code, message, details
    }
}

/// Heterogeneous JSON value for open dicts (details, payload, result).
enum StringValue: Codable, Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else { self = .string(try c.decode(String.self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .null: try c.encodeNil()
        }
    }
}

// MARK: - Device & auth (F1)

struct EnrollResponse: Codable, Sendable {
    var deviceToken: String
    var keyFingerprint: String       // sha256(token)[:16], log-safe
    var note: String

    enum CodingKeys: String, CodingKey {
        case deviceToken = "device_token"
        case keyFingerprint = "key_fingerprint"
        case note
    }
}

struct DeviceStatus: Codable, Sendable {
    var status: String               // "active" | "revoked"
    var deviceId: String
    var keyFingerprint: String
    var enrolledAt: String?
    var revokedAt: String?
    var source: String               // "sqlite" | "legacy_file"

    enum CodingKeys: String, CodingKey {
        case status, source
        case deviceId = "device_id"
        case keyFingerprint = "key_fingerprint"
        case enrolledAt = "enrolled_at"
        case revokedAt = "revoked_at"
    }
}

struct ChallengeResponse: Codable, Sendable {
    var nonce: String
    var expiresS: Int                // 60

    enum CodingKeys: String, CodingKey {
        case nonce
        case expiresS = "expires_s"
    }
}

struct ChallengeVerifyResponse: Codable, Sendable {
    var deviceId: String
    var verified: Bool

    enum CodingKeys: String, CodingKey {
        case deviceId = "device_id"
        case verified
    }
}

// MARK: - Project (F2)

struct Project: Codable, Sendable, Identifiable {
    var projectId: String
    var name: String
    var description: String
    var siteAddress: String
    var status: String               // "active" | "archived"
    var createdAt: String
    var updatedAt: String
    var archivedAt: String?
    var revision: Int?              // present on sync receipts; NOT on the
                                     // plain project JSON (row_to_project
                                     // omits it) - decode defensively

    var id: String { projectId }

    enum CodingKeys: String, CodingKey {
        case projectId = "project_id"
        case name, description, status
        case siteAddress = "site_address"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case archivedAt = "archived_at"
        case revision
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        projectId = try c.decode(String.self, forKey: .projectId)
        name = try c.decode(String.self, forKey: .name)
        description = try c.decode(String.self, forKey: .description)
        siteAddress = try c.decode(String.self, forKey: .siteAddress)
        status = try c.decode(String.self, forKey: .status)
        createdAt = try c.decode(String.self, forKey: .createdAt)
        updatedAt = try c.decode(String.self, forKey: .updatedAt)
        archivedAt = try c.decodeIfPresent(String.self, forKey: .archivedAt)
        revision = try c.decodeIfPresent(Int.self, forKey: .revision)
    }
}

struct ProjectList: Codable, Sendable {
    var items: [Project]
    var total: Int
    var limit: Int
    var offset: Int
}

// MARK: - Photo (F2)

struct Photo: Codable, Sendable, Identifiable {
    var photoId: String
    var projectId: String
    var originalSha256: String
    var originalFilename: String
    var mimeType: String
    var byteSize: Int
    var capturedAt: String          // client-claimed, untrusted
    var receivedAt: String          // server UTC - the only trusted clock
    var deviceId: String            // client-claimed, untrusted
    var evidenceId: String?
    var createdAt: String
    var trustNotes: TrustNotes

    var id: String { photoId }

    enum CodingKeys: String, CodingKey {
        case photoId = "photo_id"
        case projectId = "project_id"
        case originalSha256 = "original_sha256"
        case originalFilename = "original_filename"
        case mimeType = "mime_type"
        case byteSize = "byte_size"
        case capturedAt = "captured_at"
        case receivedAt = "received_at"
        case deviceId = "device_id"
        case evidenceId = "evidence_id"
        case createdAt = "created_at"
        case trustNotes = "trust_notes"
    }
}

struct TrustNotes: Codable, Sendable {
    var capturedAt: String          // literal: "client-claimed, untrusted"
    var deviceId: String            // literal: "client-claimed, untrusted"

    enum CodingKeys: String, CodingKey {
        case capturedAt = "captured_at"
        case deviceId = "device_id"
    }
}

struct PhotoList: Codable, Sendable {
    var items: [Photo]
    var total: Int
    var limit: Int
    var offset: Int
}

// MARK: - Original bytes (F2)

struct OriginalUploadReceipt: Codable, Sendable {
    var photoId: String
    var sha256: String
    var byteSize: Int
    var stored: Bool
    var idempotentReplay: Bool
    var note: String

    enum CodingKeys: String, CodingKey {
        case photoId = "photo_id"
        case sha256 = "sha256"
        case byteSize = "byte_size"
        case stored
        case idempotentReplay = "idempotent_replay"
        case note
    }
}

struct OriginalIntegrityView: Codable, Sendable {
    var photoId: String
    var original: OriginalIntegrity
    var boundToPhoto: Bool
    var evidence: EvidenceChainState
    var claims: IntegrityClaims

    enum CodingKeys: String, CodingKey {
        case photoId = "photo_id"
        case original, evidence, claims
        case boundToPhoto = "bound_to_photo"
    }
}

struct OriginalIntegrity: Codable, Sendable {
    var sha256: String
    var objectStatus: String        // "registered" | "verified" | "not_registered"
    var byteSize: Int?
    var filePresent: Bool
    var fileHashValid: Bool?
    var verifiedAt: String?
    var note: String

    enum CodingKeys: String, CodingKey {
        case sha256 = "sha256"
        case objectStatus = "object_status"
        case byteSize = "byte_size"
        case filePresent = "file_present"
        case fileHashValid = "file_hash_valid"
        case verifiedAt = "verified_at"
        case note
    }
}

struct EvidenceChainState: Codable, Sendable {
    var linkCount: Int
    var highestStatus: String
    var links: [EvidenceLinkState]

    enum CodingKeys: String, CodingKey {
        case linkCount = "link_count"
        case highestStatus = "highest_status"
        case links
    }
}

struct EvidenceLinkState: Codable, Sendable {
    var verificationStatus: String
    var failureReason: String?
    var verifiedAt: String?

    enum CodingKeys: String, CodingKey {
        case verificationStatus = "verification_status"
        case failureReason = "failure_reason"
        case verifiedAt = "verified_at"
    }
}

struct IntegrityClaims: Codable, Sendable {
    var fileIntegrityVerified: Bool
    var photoAuthenticityProven: Bool   // always false by design
    var authenticityNote: String

    enum CodingKeys: String, CodingKey {
        case fileIntegrityVerified = "file_integrity_verified"
        case photoAuthenticityProven = "photo_authenticity_proven"
        case authenticityNote = "authenticity_note"
    }
}

// MARK: - Evidence links (F3)

struct EvidenceLink: Codable, Sendable, Identifiable {
    var evidenceLinkId: String
    var photoId: String
    var evidenceReference: String
    var evidenceType: String        // "signature_manifest" | "time_anchor"
    var originalSha256: String      // bound at link time (anti misbinding)
    var verificationStatus: String  // unverified | signature_valid |
                                     // cryptographically_valid |
                                     // server_anchored | verification_failed
    var failureReason: String?
    var verifiedAt: String?
    var createdAt: String

    var id: String { evidenceLinkId }

    enum CodingKeys: String, CodingKey {
        case evidenceLinkId = "evidence_link_id"
        case photoId = "photo_id"
        case evidenceReference = "evidence_reference"
        case evidenceType = "evidence_type"
        case originalSha256 = "original_sha256"
        case verificationStatus = "verification_status"
        case failureReason = "failure_reason"
        case verifiedAt = "verified_at"
        case createdAt = "created_at"
    }
}

struct EvidenceLinkList: Codable, Sendable {
    var items: [EvidenceLink]
    var total: Int
}

// MARK: - Derivatives (F4)

struct WatermarkDerivative: Codable, Sendable, Identifiable {
    var derivativeId: String
    var photoId: String
    var templateName: String        // minimal|construction|inspection|corporate|evidence
    var templateVersion: String     // "1" on baseline
    var originalSha256: String
    var derivativeSha256: String
    var imageWidth: Int
    var imageHeight: Int
    var createdAt: String
    var idempotentReplay: Bool      // ⚠️ present on create responses only
    var note: String
    var geometry: WatermarkGeometry? // ⚠️ create response only; absent on list

    var id: String { derivativeId }

    enum CodingKeys: String, CodingKey {
        case derivativeId = "derivative_id"
        case photoId = "photo_id"
        case templateName = "template_name"
        case templateVersion = "template_version"
        case originalSha256 = "original_sha256"
        case derivativeSha256 = "derivative_sha256"
        case imageWidth = "image_width"
        case imageHeight = "image_height"
        case createdAt = "created_at"
        case idempotentReplay = "idempotent_replay"
        case note, geometry
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        derivativeId = try c.decode(String.self, forKey: .derivativeId)
        photoId = try c.decode(String.self, forKey: .photoId)
        templateName = try c.decode(String.self, forKey: .templateName)
        templateVersion = try c.decode(String.self, forKey: .templateVersion)
        originalSha256 = try c.decode(String.self, forKey: .originalSha256)
        derivativeSha256 = try c.decode(String.self, forKey: .derivativeSha256)
        imageWidth = try c.decode(Int.self, forKey: .imageWidth)
        imageHeight = try c.decode(Int.self, forKey: .imageHeight)
        createdAt = try c.decode(String.self, forKey: .createdAt)
        idempotentReplay = try c.decodeIfPresent(Bool.self,
                                                 forKey: .idempotentReplay)
                ?? false
        note = try c.decode(String.self, forKey: .note)
        geometry = try c.decodeIfPresent(WatermarkGeometry.self,
                                         forKey: .geometry)
    }
}

struct WatermarkGeometry: Codable, Sendable {
    var template: String
    var templateVersion: String
    var imageWidth: Int
    var imageHeight: Int
    var exifOrientationApplied: Int
    var fontSize: Int
    var fontScale: Double
    var panel: WatermarkPanel
    var textColorRgb: [Int]
    var backgroundDark: Bool
    var regionLuminance: Double
    var lineCount: Int
    var logo: String?               // logo path when rendered, else null
    var position: String
    var margin: Int

    enum CodingKeys: String, CodingKey {
        case template
        case templateVersion = "template_version"
        case imageWidth = "image_width"
        case imageHeight = "image_height"
        case exifOrientationApplied = "exif_orientation_applied"
        case fontSize = "font_size"
        case fontScale = "font_scale"
        case panel
        case textColorRgb = "text_color_rgb"
        case backgroundDark = "background_dark"
        case regionLuminance = "region_luminance"
        case lineCount = "line_count"
        case logo, position, margin
    }
}

struct WatermarkPanel: Codable, Sendable {
    var x: Int
    var y: Int
    var w: Int
    var h: Int
    var opacity: Double
    var colorRgb: [Int]

    enum CodingKeys: String, CodingKey {
        case x, y, w, h, opacity
        case colorRgb = "color_rgb"
    }
}

// MARK: - Annotations (F5)

struct Annotation: Codable, Sendable, Identifiable {
    var annotationId: String
    var photoId: String
    var annotationType: String     // arrow|rectangle|circle|freehand|text|number_marker
    var normX1: Double
    var normY1: Double
    var normX2: Double?
    var normY2: Double?
    var colorHex: String            // "#RRGGBB"
    var strokeWidth: Int
    var textContent: String?
    var markerNumber: Int?
    var zOrder: Int
    var revision: Int
    var isActive: Bool
    var createdAt: String
    var updatedAt: String
    var note: String                // "human-added annotation content"

    var id: String { annotationId }

    enum CodingKeys: String, CodingKey {
        case annotationId = "annotation_id"
        case photoId = "photo_id"
        case annotationType = "annotation_type"
        case normX1 = "norm_x1", normY1 = "norm_y1"
        case normX2 = "norm_x2", normY2 = "norm_y2"
        case colorHex = "color_hex"
        case strokeWidth = "stroke_width"
        case textContent = "text_content"
        case markerNumber = "marker_number"
        case zOrder = "z_order"
        case revision
        case isActive = "is_active"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case note
    }
}

struct AnnotationList: Codable, Sendable {
    var items: [Annotation]
    var total: Int
    var note: String
}

// MARK: - Inspection issues (F5)

struct Issue: Codable, Sendable, Identifiable {
    var issueId: String
    var projectId: String
    var title: String
    var description: String
    var severity: String            // low|medium|high|critical
    var status: String              // open|in_progress|resolved|closed
    var locationDescription: String
    var assignedTo: String
    var revision: Int
    var createdAt: String
    var updatedAt: String
    var resolvedAt: String?
    var note: String                // human records; no AI assessment implied

    var id: String { issueId }

    enum CodingKeys: String, CodingKey {
        case issueId = "issue_id"
        case projectId = "project_id"
        case title, description, severity, status
        case locationDescription = "location_description"
        case assignedTo = "assigned_to"
        case revision
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case resolvedAt = "resolved_at"
        case note
    }
}

struct IssueList: Codable, Sendable {
    var items: [Issue]
    var total: Int
}

/// GET /issues/{iid} - detail with full history and linked photos.
struct IssueDetail: Codable, Sendable {
    var issue: Issue                // flattened: decode Issue keys + extras
    var history: [IssueHistoryEntry]
    var photos: [IssuePhotoLink]

    enum CodingKeys: String, CodingKey { case history, photos }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        issue = try Issue(from: decoder)
        history = try c.decode([IssueHistoryEntry].self, forKey: .history)
        photos = try c.decode([IssuePhotoLink].self, forKey: .photos)
    }
}

struct IssueHistoryEntry: Codable, Sendable {
    var id: Int
    var actorToken: String          // token of the actor (fingerprint not
                                     // applied here - full token; do NOT
                                     // display, use for ordering only)
    var action: String              // created|severity_changed|status_changed|photo_linked|photo_unlinked
    var fromValue: String?
    var toValue: String?
    var detail: String
    var createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, action, detail
        case actorToken = "actor_token"
        case fromValue = "from_value"
        case toValue = "to_value"
        case createdAt = "created_at"
    }
}

struct IssuePhotoLink: Codable, Sendable {
    var photoId: String
    var role: String                // before|progress|after
    var addedAt: String

    enum CodingKeys: String, CodingKey {
        case photoId = "photo_id"
        case role, addedAt = "added_at"
    }
}

struct IssuePhotoLinkReceipt: Codable, Sendable {
    var issueId: String
    var photoId: String
    var role: String
    var addedAt: String?            // POST receipt only
    var unlinked: Bool?             // DELETE receipt only
    var note: String?               // POST receipt only

    enum CodingKeys: String, CodingKey {
        case issueId = "issue_id"
        case photoId = "photo_id"
        case role
        case addedAt = "added_at"
        case unlinked, note
    }
}

// MARK: - Reports (F6)

struct ReportCreated: Codable, Sendable {
    var reportId: String
    var reportNumber: String         // "WC-YYYYMMDD-XXXXXXXX"
    var reportType: String           // site_photo|inspection|evidence
    var templateVersion: String      // "1" on baseline
    var pageSize: String             // "a4" | "letter"
    var perPage: Int
    var photoCount: Int
    var pdfSha256: String
    var pageCount: Int
    var createdAt: String
    var note: String

    enum CodingKeys: String, CodingKey {
        case reportId = "report_id"
        case reportNumber = "report_number"
        case reportType = "report_type"
        case templateVersion = "template_version"
        case pageSize = "page_size"
        case perPage = "per_page"
        case photoCount = "photo_count"
        case pdfSha256 = "pdf_sha256"
        case pageCount = "page_count"
        case createdAt = "created_at"
        case note
    }
}

struct ReportSummary: Codable, Sendable {
    var reportId: String
    var projectId: String
    var reportNumber: String
    var reportType: String
    var templateVersion: String
    var pageSize: String
    var perPage: Int
    var pdfSha256: String
    var pageCount: Int
    var status: String               // "complete"
    var createdAt: String

    enum CodingKeys: String, CodingKey {
        case reportId = "report_id"
        case projectId = "project_id"
        case reportNumber = "report_number"
        case reportType = "report_type"
        case templateVersion = "template_version"
        case pageSize = "page_size"
        case perPage = "per_page"
        case pdfSha256 = "pdf_sha256"
        case pageCount = "page_count"
        case status, createdAt = "created_at"
    }
}

struct ReportList: Codable, Sendable {
    var items: [ReportSummary]
    var total: Int
}

struct ReportDetail: Codable, Sendable {
    var reportId: String
    var projectId: String
    var reportNumber: String
    var reportType: String
    var templateVersion: String
    var pageSize: String
    var perPage: Int
    var pdfSha256: String
    var pageCount: Int
    var status: String
    var createdAt: String
    var photoIds: [String]
    var snapshot: [String: ReportPhotoSnapshot]   // keyed by photo UUID
    var issueSnapshot: [ReportIssueSnapshot]

    enum CodingKeys: String, CodingKey {
        case reportId = "report_id"
        case projectId = "project_id"
        case reportNumber = "report_number"
        case reportType = "report_type"
        case templateVersion = "template_version"
        case pageSize = "page_size"
        case perPage = "per_page"
        case pdfSha256 = "pdf_sha256"
        case pageCount = "page_count"
        case status, createdAt = "created_at"
        case photoIds = "photo_ids"
        case snapshot, issueSnapshot = "issue_snapshot"
    }
}

struct ReportPhotoSnapshot: Codable, Sendable {
    var photoId: String
    var originalSha256: String
    var derivativeSha256: String?
    var imageWidth: Int?            // 0 when the bytes could not be loaded
    var imageHeight: Int?
    var capturedTime: String
    var timeSource: String          // "device"
    var gpsLatitude: Double?
    var gpsLongitude: Double?
    var gpsSource: String?
    var gpsAccuracy: Double?
    var notes: String?
    var evidenceStatus: String
    var evidenceReference: String?
    var evidenceVerifiedAt: String?

    enum CodingKeys: String, CodingKey {
        case photoId = "photo_id"
        case originalSha256 = "original_sha256"
        case derivativeSha256 = "derivative_sha256"
        case imageWidth = "image_width"
        case imageHeight = "image_height"
        case capturedTime = "captured_time"
        case timeSource = "time_source"
        case gpsLatitude = "gps_latitude"
        case gpsLongitude = "gps_longitude"
        case gpsSource = "gps_source"
        case gpsAccuracy = "gps_accuracy"
        case notes, evidenceStatus = "evidence_status"
        case evidenceReference = "evidence_reference"
        case evidenceVerifiedAt = "evidence_verified_at"
    }
}

struct ReportIssueSnapshot: Codable, Sendable {
    var issueId: String
    var title: String
    var description: String
    var severity: String
    var status: String
    var locationDescription: String
    var assignedTo: String
    var createdAt: String
    var updatedAt: String
    var resolvedAt: String?
    var history: [ReportIssueHistoryEntry]
    var photos: [ReportIssuePhotoRef]

    enum CodingKeys: String, CodingKey {
        case issueId = "issue_id"
        case title, description, severity, status
        case locationDescription = "location_description"
        case assignedTo = "assigned_to"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case resolvedAt = "resolved_at"
        case history, photos
    }
}

struct ReportIssueHistoryEntry: Codable, Sendable {
    var action: String
    var fromValue: String?
    var toValue: String?
    var detail: String
    var createdAt: String

    enum CodingKeys: String, CodingKey {
        case action, detail
        case fromValue = "from_value"
        case toValue = "to_value"
        case createdAt = "created_at"
    }
}

struct ReportIssuePhotoRef: Codable, Sendable {
    var photoId: String
    var role: String
    var originalSha256: String

    enum CodingKeys: String, CodingKey {
        case photoId = "photo_id"
        case role
        case originalSha256 = "original_sha256"
    }
}

// MARK: - Sync (F6) - baseline W008/W013-R1 protocol

struct SyncBatchResponse: Codable, Sendable {
    var batchReceived: Int
    var receipts: [SyncReceipt]
    var idMap: [String: String]     // local_id -> server_id

    enum CodingKeys: String, CodingKey {
        case batchReceived = "batch_received"
        case receipts
        case idMap = "id_map"
    }
}

struct SyncReceipt: Codable, Sendable {
    var operationId: String
    var status: String               // applied | duplicate | rejected
    var result: [String: StringValue]?
    var error: SyncReceiptError?

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case status, result, error
    }
}

struct SyncReceiptError: Codable, Sendable {
    var code: String                 // e.g. revision_conflict, op_id_conflict
    var message: String
}

struct SyncEventsPage: Codable, Sendable {
    var items: [SyncEvent]
    var cursor: Int
    var hasMore: Bool
    var limit: Int

    enum CodingKeys: String, CodingKey {
        case items, cursor, limit
        case hasMore = "has_more"
    }
}

struct SyncEvent: Codable, Sendable {
    var seq: Int
    var entityType: String           // baseline emits: project | photo
    var entityId: String
    var action: String                // baseline emits: created | updated
    var revision: Int?
    var payloadHint: String?
    var createdAt: String

    enum CodingKeys: String, CodingKey {
        case seq, action, revision
        case entityType = "entity_type"
        case entityId = "entity_id"
        case payloadHint = "payload_hint"
        case createdAt = "created_at"
    }
}

// MARK: - Strongly typed sync payloads (recommended)
// NOTE: every payload MUST repeat operation_id (baseline executor
// contract - see API reference §4.1 and the mismatch report).

struct SyncOperationEnvelope<Payload: Codable & Sendable>:
        Codable, Sendable {
    var op: String                    // frozen discriminator
    var operationId: String           // ledger key (owner-scoped)
    var payload: Payload

    enum CodingKeys: String, CodingKey {
        case op
        case operationId = "operation_id"
        case payload
    }
}

struct ProjectCreatePayload: Codable, Sendable {
    var operationId: String
    var localId: String
    var name: String
    var description: String?
    var siteAddress: String?

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case localId = "local_id"
        case name, description
        case siteAddress = "site_address"
    }
}

struct ProjectUpdatePayload: Codable, Sendable {
    var operationId: String
    var serverProjectId: String
    var expectedRevision: Int
    var name: String?
    var description: String?
    var siteAddress: String?

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case serverProjectId = "server_project_id"
        case expectedRevision = "expected_revision"
        case name, description
        case siteAddress = "site_address"
    }
}

struct PhotoRegisterPayload: Codable, Sendable {
    var operationId: String
    var localId: String
    var serverProjectId: String
    var idempotencyKey: String
    var originalSha256: String       // 64 lowercase hex
    var originalFilename: String
    var mimeType: String             // image/*
    var byteSize: Int                // 1...100_000_000
    var capturedAt: String           // DATA, untrusted
    var deviceId: String             // DATA, untrusted

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case localId = "local_id"
        case serverProjectId = "server_project_id"
        case idempotencyKey = "idempotency_key"
        case originalSha256 = "original_sha256"
        case originalFilename = "original_filename"
        case mimeType = "mime_type"
        case byteSize = "byte_size"
        case capturedAt = "captured_at"
        case deviceId = "device_id"
    }
}

struct AnnotationCreatePayload: Codable, Sendable {
    var operationId: String
    var localId: String
    var serverPhotoId: String
    var annotationType: String
    var normX1: Double
    var normY1: Double
    var normX2: Double?
    var normY2: Double?
    var colorHex: String?
    var strokeWidth: Int?
    var textContent: String?
    var markerNumber: Int?
    var zOrder: Int?

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case localId = "local_id"
        case serverPhotoId = "server_photo_id"
        case annotationType = "annotation_type"
        case normX1 = "norm_x1", normY1 = "norm_y1"
        case normX2 = "norm_x2", normY2 = "norm_y2"
        case colorHex = "color_hex"
        case strokeWidth = "stroke_width"
        case textContent = "text_content"
        case markerNumber = "marker_number"
        case zOrder = "z_order"
    }
}

struct AnnotationUpdatePayload: Codable, Sendable {
    var operationId: String
    var serverAnnotationId: String
    var expectedRevision: Int
    var normX1: Double?
    var normY1: Double?
    var normX2: Double?
    var normY2: Double?
    var colorHex: String?
    var strokeWidth: Int?
    var textContent: String?
    var markerNumber: Int?
    var zOrder: Int?

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case serverAnnotationId = "server_annotation_id"
        case expectedRevision = "expected_revision"
        case normX1 = "norm_x1", normY1 = "norm_y1"
        case normX2 = "norm_x2", normY2 = "norm_y2"
        case colorHex = "color_hex"
        case strokeWidth = "stroke_width"
        case textContent = "text_content"
        case markerNumber = "marker_number"
        case zOrder = "z_order"
    }
}

struct AnnotationDeletePayload: Codable, Sendable {
    var operationId: String
    var serverAnnotationId: String

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case serverAnnotationId = "server_annotation_id"
    }
}

struct IssueCreatePayload: Codable, Sendable {
    var operationId: String
    var localId: String
    var serverProjectId: String
    var title: String
    var description: String?
    var severity: String
    var locationDescription: String?
    var assignedTo: String?

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case localId = "local_id"
        case serverProjectId = "server_project_id"
        case title, description, severity
        case locationDescription = "location_description"
        case assignedTo = "assigned_to"
    }
}

struct IssueUpdatePayload: Codable, Sendable {
    var operationId: String
    var serverIssueId: String
    var expectedRevision: Int
    var title: String?
    var description: String?
    var severity: String?
    var locationDescription: String?
    var assignedTo: String?

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case serverIssueId = "server_issue_id"
        case expectedRevision = "expected_revision"
        case title, description, severity
        case locationDescription = "location_description"
        case assignedTo = "assigned_to"
    }
}

struct IssueTransitionPayload: Codable, Sendable {
    var operationId: String
    var serverIssueId: String
    var expectedRevision: Int
    var targetStatus: String        // open|in_progress|resolved|closed

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case serverIssueId = "server_issue_id"
        case expectedRevision = "expected_revision"
        case targetStatus = "target_status"
    }
}

struct IssuePhotoLinkPayload: Codable, Sendable {
    var operationId: String
    var serverIssueId: String
    var serverPhotoId: String
    var role: String               // before|progress|after

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case serverIssueId = "server_issue_id"
        case serverPhotoId = "server_photo_id"
        case role
    }
}

struct IssuePhotoUnlinkPayload: Codable, Sendable {
    var operationId: String
    var serverIssueId: String
    var serverPhotoId: String
    var role: String

    enum CodingKeys: String, CodingKey {
        case operationId = "operation_id"
        case serverIssueId = "server_issue_id"
        case serverPhotoId = "server_photo_id"
        case role
    }
}

// MARK: - Verify core (frozen surface, F3)

struct VerifyRequest: Codable, Sendable {
    var manifest: SignatureManifest
    var photoBase64: String?

    enum CodingKeys: String, CodingKey {
        case manifest
        case photoBase64 = "photo_base64"
    }
}

struct SignatureManifest: Codable, Sendable {
    var version: String              // "1.0" | "1.1"
    var recordId: String
    var sequenceNumber: Int
    var previousRecordHash: String?
    var photoHash: String
    var captureTimestamp: String
    var deviceReportedTime: String
    var deviceId: String
    var location: LocationData?
    var signature: String            // base64(DER ECDSA-P256-SHA256)
    var publicKey: String            // base64(X9.63 uncompressed point)

    enum CodingKeys: String, CodingKey {
        case version
        case recordId = "record_id"
        case sequenceNumber = "sequence_number"
        case previousRecordHash = "previous_record_hash"
        case photoHash = "photo_hash"
        case captureTimestamp = "capture_timestamp"
        case deviceReportedTime = "device_reported_time"
        case deviceId = "device_id"
        case location, signature
        case publicKey = "public_key"
    }
}

struct LocationData: Codable, Sendable {
    var latitude: Double             // -90...90
    var longitude: Double            // -180...180
    var horizontalAccuracy: Double
    var altitude: Double?

    enum CodingKeys: String, CodingKey {
        case latitude, longitude, altitude
        case horizontalAccuracy = "horizontal_accuracy"
    }
}

struct VerifyResponse: Codable, Sendable {
    var evidenceStatus: String       // fully_attested|cryptographically_valid|
                                     // signature_valid|partial|unverified|invalid
    var cryptographicSignatureValid: Bool
    var photoHashValid: Bool?
    var keyRegistrationStatus: String   // registered|unregistered|unknown
    var deviceIdentityConsistent: Bool?
    var deviceAttestationStatus: String  // attested|not_attested|unknown
    var timeTrustLevel: String           // server_anchored|device_reported|unknown
    var summary: String
    var details: [VerifyDetail]
    var warnings: [String]

    enum CodingKeys: String, CodingKey {
        case evidenceStatus = "evidence_status"
        case cryptographicSignatureValid = "cryptographic_signature_valid"
        case photoHashValid = "photo_hash_valid"
        case keyRegistrationStatus = "key_registration_status"
        case deviceIdentityConsistent = "device_identity_consistent"
        case deviceAttestationStatus = "device_attestation_status"
        case timeTrustLevel = "time_trust_level"
        case summary, details, warnings
    }
}

struct VerifyDetail: Codable, Sendable {
    var check: String                // structure|version|photo_hash|ecdsa_signature
    var status: String               // passed|failed|not_checked
    var message: String
}

// MARK: - Time anchor (EXPERIMENTAL surface, baseline semantics, F3)

struct TimeAnchorRequest: Codable, Sendable {
    var batchId: String              // guard: no control chars, no '='
    var chainHeadHash: String        // 64 hex
    var chainTailHash: String
    var recordCount: Int             // 1...10000
    var deviceReportedStartTime: String
    var deviceReportedEndTime: String

    enum CodingKeys: String, CodingKey {
        case batchId = "batch_id"
        case chainHeadHash = "chain_head_hash"
        case chainTailHash = "chain_tail_hash"
        case recordCount = "record_count"
        case deviceReportedStartTime = "device_reported_start_time"
        case deviceReportedEndTime = "device_reported_end_time"
    }
}

struct TimeAnchorResponse: Codable, Sendable {
    var anchorId: String
    var keyId: String
    var batchId: String
    var serverReceivedAt: String     // SIGNED
    var challenge: String            // SIGNED
    var serverSignature: String      // SIGNED
    var chainHeadHash: String        // SIGNED
    var chainTailHash: String        // SIGNED
    var recordCount: Int             // SIGNED
    var verificationStatus: String   // UNSIGNED: digest_anchored|chain_verified|photo_verified|record_count_verified

    enum CodingKeys: String, CodingKey {
        case anchorId = "anchor_id"
        case keyId = "key_id"
        case batchId = "batch_id"
        case serverReceivedAt = "server_received_at"
        case challenge, serverSignature = "server_signature"
        case chainHeadHash = "chain_head_hash"
        case chainTailHash = "chain_tail_hash"
        case recordCount = "record_count"
        case verificationStatus = "verification_status"
    }
}

/// The CLIENT-ASSEMBLED receipt (anchor response + unsigned device times
/// + stored_at). Submitting the raw anchor response to /verify is a 422.
struct TimeAnchorReceipt: Codable, Sendable {
    var anchorId: String
    var keyId: String
    var batchId: String
    var serverReceivedAt: String
    var challenge: String
    var serverSignature: String
    var chainHeadHash: String
    var chainTailHash: String
    var recordCount: Int
    var deviceReportedStartTime: String   // UNSIGNED
    var deviceReportedEndTime: String     // UNSIGNED
    var verificationStatus: String        // UNSIGNED
    var storedAt: String                  // UNSIGNED

    enum CodingKeys: String, CodingKey {
        case anchorId = "anchor_id"
        case keyId = "key_id"
        case batchId = "batch_id"
        case serverReceivedAt = "server_received_at"
        case challenge, serverSignature = "server_signature"
        case chainHeadHash = "chain_head_hash"
        case chainTailHash = "chain_tail_hash"
        case recordCount = "record_count"
        case deviceReportedStartTime = "device_reported_start_time"
        case deviceReportedEndTime = "device_reported_end_time"
        case verificationStatus = "verification_status"
        case storedAt = "stored_at"
    }
}

struct TimeAnchorVerification: Codable, Sendable {
    var valid: Bool
    var anchorId: String
    var keyId: String
    var serverReceivedAt: String
    var signatureValid: Bool
    var challengeMatches: Bool
    var storageMatches: Bool
    var verificationStatus: String
    var trustLevel: String              // server_anchored|device_reported|unverified
    var message: String
    var warnings: [String]
    var signedFieldsVerified: Bool
    var unsignedFieldsNote: String

    enum CodingKeys: String, CodingKey {
        case valid, message, warnings
        case anchorId = "anchor_id"
        case keyId = "key_id"
        case serverReceivedAt = "server_received_at"
        case signatureValid = "signature_valid"
        case challengeMatches = "challenge_matches"
        case storageMatches = "storage_matches"
        case verificationStatus = "verification_status"
        case trustLevel = "trust_level"
        case signedFieldsVerified = "signed_fields_verified"
        case unsignedFieldsNote = "unsigned_fields_note"
    }
}
```

## 接入注意（⚠️ 逐条）

1. **`payload.operation_id` 必填**：每个 sync op 的 payload 里必须重复 `operation_id`（与顶层同值）；基线实现缺它 → HTTP 500（见不一致清单 #3）。`SyncOperationEnvelope` 已强制同源生成。
2. **选择性出现的字段**：`WatermarkDerivative` 的 `idempotent_replay` / `geometry` 只在 **POST create** 响应出现，列表响应没有——decode 已做 defensive。
3. **`report_detail.snapshot` 的键是 photo UUID**（动态键），值是 `ReportPhotoSnapshot`。
4. **两类错误体并存**：业务面 `ApiErrorEnvelope`；pydantic 422 的 `detail` 是**数组**（`[{loc, msg, type}]`）——解码前先判 `detail` 类型；实验面还有第三类（裸字符串 detail）。
5. **`received_at` 是唯一服务端时间**；UI 显示 `captured_at`/`device_id` 必须带 client-claimed 标注（swiftui-mapping 诚实性检查表）。
6. **`IssueHistoryEntry.actorToken` 是完整 token**：仅用于排序，不要在 UI 显示或写日志。
7. **`VerifyResponse.photoHashValid` / `deviceIdentityConsistent` 可为 null**：三态（通过/失败/未检）。
8. **binary 端点**（original/derivative/comparison/PDF/ZIP）返回字节流，完整性靠 `X-*-Sha256` 响应头自行校验，不走 Codable。
9. **`Project.revision` 基线不在普通项目 JSON 里**（`row_to_project` 不含）；sync 回执的 `result` 里有 `revision`。合并 W014 后列表亦会带——模型已 defensive。
10. **Not in baseline**：`LocalRef`（`"local:<id>"`）、`PhotoBusiness`/`TagEnvelope`、`PhotoSearchPage`（keyset cursor）、W015 的 `remediation_note`/`inspector_name`/`lidar_measurements` 等——合入对应分支后再建模，本基线上这些字段一律按未知字段忽略（`Codable` 默认行为）。
