// TM66: unified API client - typed Swift access to the RC2+
// backend (75 paths / 88 ops, docs/api/openapi.json).
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).
//
// DESIGN RULES:
//  - the transport is INJECTED (URLSession on the app side; the same
//    interface doubles as the weak-network/loopback test fake)
//  - the token is NEVER hardcoded: TokenProvider injects it per call
//  - every server error decodes into the typed envelope
//    {code, message, details} - silent-string errors are impossible
//  - request bodies/params are Codable structs mirroring the backend
//    models; the OpenAPI file is the source of truth for drift checks
//  - async/await throughout; cancellation via Swift's structured
//    concurrency (Task cancellation propagates to the transport call)

import Foundation
import WorkCamEvidence

// MARK: - error envelope (the server's unified ApiError shape)

public struct APIErrorBody: Codable, Sendable, Equatable {
    public let code: String
    public let message: String
    public let details: APIErrorDetails

    public struct APIErrorDetails: Codable, Sendable, Equatable {
        public var currentRevision: Int?
        public var missingChunks: [Int]?
        public var problems: [String]?
        // open bag for forward compatibility
        public var extra: [String: String]?

        public static let empty = APIErrorDetails(
            currentRevision: nil, missingChunks: nil,
            problems: nil, extra: nil)

        public init(currentRevision: Int?,
                    missingChunks: [Int]?,
                    problems: [String]?,
                    extra: [String: String]?) {
            self.currentRevision = currentRevision
            self.missingChunks = missingChunks
            self.problems = problems
            self.extra = extra
        }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(
                keyedBy: CodingKeys.self)
            currentRevision = try c.decodeIfPresent(
                Int.self, forKey: .currentRevision)
            missingChunks = try c.decodeIfPresent(
                [Int].self, forKey: .missingChunks)
            problems = try c.decodeIfPresent(
                [String].self, forKey: .problems)
            extra = try c.decodeIfPresent(
                [String: String].self, forKey: .extra)
        }

        enum CodingKeys: String, CodingKey {
            case currentRevision = "current_revision"
            case missingChunks = "missing_chunks"
            case problems
            case extra
        }
        // manual encode omitted: details are server -> client only
    }
}

public enum APIError: Error, Sendable, Equatable {
    case http(status: Int, body: APIErrorBody)
    case transport(String)
    case decoding(String)
}

// MARK: - injected transport + token

public protocol HTTPTransport: Sendable {
    func request(_ method: String, _ path: String,
                 headers: [String: String],
                 body: Data?) async throws
        -> (status: Int, data: Data)
}

public protocol TokenProvider: Sendable {
    func currentToken() async -> String?
}

// MARK: - request/response models (backend-mirroring subset:
// every model here maps 1:1 to docs/api/openapi.json)

public struct EnrollRequest: Codable, Sendable {
    public let publicKeyB64: String
    public let deviceID: String
    public let enrollmentCode: String

    public init(publicKeyB64: String, deviceID: String,
                enrollmentCode: String) {
        self.publicKeyB64 = publicKeyB64
        self.deviceID = deviceID
        self.enrollmentCode = enrollmentCode
    }

    enum CodingKeys: String, CodingKey {
        case publicKeyB64 = "public_key_b64"
        case deviceID = "device_id"
        case enrollmentCode = "enrollment_code"
    }
}

public struct ProjectCreateRequest: Codable, Sendable {
    public let name: String
    public var description: String?
    public var siteAddress: String?

    public init(name: String, description: String? = nil,
                siteAddress: String? = nil) {
        self.name = name
        self.description = description
        self.siteAddress = siteAddress
    }

    enum CodingKeys: String, CodingKey {
        case name, description
        case siteAddress = "site_address"
    }
}

public struct Project: Codable, Sendable, Identifiable {
    public let projectID: String
    public let name: String
    public let status: String

    public var id: String { projectID }

    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case name, status
    }
}

public struct SyncBatchRequest: Codable, Sendable {
    public struct Op: Codable, Sendable {
        public let op: String
        public let operationID: String
        public let payload: JSONValue

        public init(op: String, operationID: String,
                    payload: JSONValue) {
            self.op = op
            self.operationID = operationID
            self.payload = payload
        }

        enum CodingKeys: String, CodingKey {
            case op
            case operationID = "operation_id"
            case payload
        }
    }
    public let operations: [Op]

    public init(operations: [Op]) {
        self.operations = operations
    }
}

public struct SyncBatchResponse: Codable, Sendable {
    public struct Receipt: Codable, Sendable {
        public let operationID: String
        public let status: String
        public let result: JSONValue
        public let error: APIErrorBody?

        enum CodingKeys: String, CodingKey {
            case operationID = "operation_id"
            case status, result, error
        }
    }
    public let receipts: [Receipt]
}

// MARK: - the client

public final class WorkCamAPIClient: Sendable {
    private let transport: HTTPTransport
    private let tokenProvider: TokenProvider
    private let baseURL: String
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    public init(baseURL: String, transport: HTTPTransport,
                tokenProvider: TokenProvider) {
        self.baseURL = baseURL
        self.transport = transport
        self.tokenProvider = tokenProvider
        encoder = JSONEncoder()
        decoder = JSONDecoder()
    }

    /// Bodyless overload. `(some Encodable)? = nil` cannot resolve
    /// the opaque type at GET-style call sites (TM-04 fix: caught
    /// by the first real Windows compile); Never is Codable, so the
    /// generic Body stays inferable.
    private func call<Response: Decodable>(
        _ method: String, _ path: String
    ) async throws -> Response {
        try await call(method, path, body: nil as Never?)
    }

    private func call<Response: Decodable, Body: Encodable>(
        _ method: String, _ path: String, body: Body?
    ) async throws -> Response {
        let token = await tokenProvider.currentToken()
        var headers = ["Accept": "application/json"]
        if let token { headers["X-Device-Token"] = token }
        var bodyData: Data?
        if let body {
            bodyData = try encoder.encode(AnyEncodable(body))
            headers["Content-Type"] = "application/json"
        }
        let (status, data): (Int, Data)
        do {
            (status, data) = try await transport.request(
                method, baseURL + path, headers: headers,
                body: bodyData)
        } catch {
            throw APIError.transport(String(describing: error))
        }
        guard !(400...599).contains(status) else {
            let body = try? decoder.decode(APIErrorBody.self,
                                           from: data)
            throw APIError.http(
                status: status,
                body: body ?? APIErrorBody(
                    code: "invalid_error_envelope",
                    message: String(data: data.prefix(200),
                                    encoding: .utf8) ?? "",
                    details: .empty))
        }
        do {
            return try decoder.decode(Response.self, from: data)
        } catch {
            throw APIError.decoding(String(describing: error))
        }
    }

    // ---- endpoints (P0 subset; the full 88-op surface expands the
    // same three-line pattern from the OpenAPI file) ----------------

    public func enroll(_ request: EnrollRequest) async throws
            -> [String: JSONValue] {
        try await call("POST", "/api/v1/devices/enroll",
                       body: request)
    }

    public func createProject(
        _ request: ProjectCreateRequest) async throws -> Project {
        try await call("POST", "/api/v1/projects", body: request)
    }

    public func projects() async throws
            -> Paginated<Project> {
        try await call("GET", "/api/v1/projects?limit=100")
    }

    public func syncBatch(_ request: SyncBatchRequest) async throws
            -> SyncBatchResponse {
        try await call("POST", "/api/v1/sync/batch", body: request)
    }

    /// M03/H02 parity with Mac Integration 02: a receipt is a SUCCESS
    /// only when status is applied/duplicate AND the result carries
    /// no embedded rejection. A REJECTED-REPLAY looks like
    /// status="duplicate" with result.status="rejected" + error -
    /// surfacing that as success would silently swallow conflicts.
    public static func receiptSucceeded(
        _ receipt: SyncBatchResponse.Receipt) -> Result<
            SyncBatchResponse.Receipt, APIError> {
        if ["applied", "duplicate"].contains(receipt.status) {
            if let st = receipt.result.objectValue?["status"],
               case .string(let embedded) = st, embedded == "rejected" {
                return .failure(.http(
                    status: 409,
                    body: receipt.error ?? APIErrorBody(
                        code: "sync_rejected",
                        message: "the server preserved a prior " +
                                "rejection for this operation id",
                        details: .empty)))
            }
            return .success(receipt)
        }
        return .failure(.http(
            status: 409,
            body: receipt.error ?? APIErrorBody(
                code: "sync_rejected", message: receipt.status,
                details: .empty)))
    }

    // ---- incremental downlink (WIN-HOTFIX-02/03) -----------------

    public struct PullEvent: Codable, Sendable, Equatable {
        public let seq: Int
        public let entityType: String
        public let entityID: String
        public let action: String
        public let revision: Int?
        public let payloadHint: String?
        public let createdAt: String
        public let state: JSONValue?
        public let tombstone: Bool?

        enum CodingKeys: String, CodingKey {
            case seq, action, revision, state, tombstone
            case entityType = "entity_type"
            case entityID = "entity_id"
            case payloadHint = "payload_hint"
            case createdAt = "created_at"
        }
    }

    public struct PullPage: Codable, Sendable, Equatable {
        public let items: [PullEvent]
        public let cursor: Int
        public let hasMore: Bool
        public let limit: Int

        enum CodingKeys: String, CodingKey {
            case items, cursor, limit
            case hasMore = "has_more"
        }
    }

    /// GET /api/v1/sync/events - incremental pull for THIS owner.
    /// Each entity_type filter needs its OWN cursor (the server
    /// counts matching rows, not global slots).
    public func pullEvents(cursor: Int, limit: Int = 100,
                            includeState: Bool = false,
                            entityType: String? = nil) async throws
            -> PullPage {
        var path = "/api/v1/sync/events?cursor=\(cursor)&limit=\(limit)"
        if includeState { path += "&include_state=true" }
        if let et = entityType { path += "&entity_type=\(et)" }
        return try await call("GET", path)
    }

    public func uploadSessionStatus(photoID: String,
                                    uploadID: String) async throws
            -> [String: JSONValue] {
        try await call("GET", "/api/v1/photos/\(photoID)" +
                       "/original-session/\(uploadID)")
    }
}

public struct Paginated<Item: Codable & Sendable>: Codable, Sendable {
    public let items: [Item]
    public let total: Int
    public let nextCursor: String?

    enum CodingKeys: String, CodingKey {
        case items, total
        case nextCursor = "next_cursor"
    }
}

// MARK: - helpers

/// Type-erasing encode wrapper.
struct AnyEncodable: Encodable {
    private let encodeFunc: (Encoder) throws -> Void
    init(_ wrapped: some Encodable) {
        encodeFunc = wrapped.encode
    }
    func encode(to encoder: Encoder) throws {
        try encodeFunc(encoder)
    }
}
