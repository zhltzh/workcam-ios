import Foundation

enum SyncJSON: Codable {
    case null, bool(Bool), int(Int), double(Double), string(String)
    case array([SyncJSON]), object([String: SyncJSON])

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let v = try? value.decode(Bool.self) { self = .bool(v) }
        else if let v = try? value.decode(Int.self) { self = .int(v) }
        else if let v = try? value.decode(Double.self) { self = .double(v) }
        else if let v = try? value.decode(String.self) { self = .string(v) }
        else if let v = try? value.decode([Self].self) { self = .array(v) }
        else { self = .object(try value.decode([String: Self].self)) }
    }

    func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .null: try value.encodeNil()
        case .bool(let v): try value.encode(v)
        case .int(let v): try value.encode(v)
        case .double(let v): try value.encode(v)
        case .string(let v): try value.encode(v)
        case .array(let v): try value.encode(v)
        case .object(let v): try value.encode(v)
        }
    }
}

struct SyncFailure: Codable {
    let code: String
    let message: String
    let details: [String: SyncJSON]?
}

struct SyncOperation: Codable {
    let op: String
    let operation_id: String
    let payload: [String: SyncJSON]
}

struct SyncBatch: Codable {
    let operations: [SyncOperation]
}

struct SyncReceipt: Codable {
    enum Status: String, Codable { case applied, duplicate, rejected }
    let operation_id: String
    let status: Status
    let result: [String: SyncJSON]
    let error: SyncFailure?

    var confirmed: Bool {
        guard status != .rejected, error == nil else { return false }
        if case .string("rejected")? = result["status"] { return false }
        return true
    }
}

struct SyncEventPage: Codable {
    struct Event: Codable {
        let seq: Int64
        let entity_type: String
        let entity_id: String
        let action: String
        let revision: Int?
        let payload_hint: String?
        let created_at: String
        let state: [String: SyncJSON]?
        let tombstone: Bool?
    }
    let items: [Event]
    let cursor: Int64
    let has_more: Bool
    let limit: Int
}

struct OriginalSession: Codable {
    let upload_id: String
    let chunk_size: Int
    let total_chunks: Int
    let received_chunks: [Int]
    let status: String
}
