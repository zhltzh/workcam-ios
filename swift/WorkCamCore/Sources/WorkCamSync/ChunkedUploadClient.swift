// TM69: resumable chunked-upload CLIENT - Swift counterpart of the
// TM35 server endpoints (open/chunk/status/cancel/complete).
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).
//
// DESIGN RULES:
//  - the original file is NEVER loaded whole into memory: chunks are
//    read one at a time through ChunkSource
//  - SHA-256 and HTTP are INJECTED (CryptoKit + URLSession on the
//    app side; doubles in tests) - Windows never claims crypto/HTTP
//    behaviour it could not run
//  - duplicate chunks are idempotent re-sends; the server tells us
//    what is missing (status endpoint) and we resume from there
//  - concurrency is bounded (maxInFlight) so 100-chunk uploads do not
//    spawn 100 sockets

import Foundation

public enum UploadError: Error, Equatable, Sendable {
    case named(String)
}

/// Streaming chunk source: the app maps the file; tests feed arrays.
public protocol ChunkSource: Sendable {
    var totalBytes: Int { get }
    /// Returns nil when index is past the end. Chunk sizes may vary
    /// for the final chunk.
    func chunk(at index: Int, size: Int) -> Data?
}

public protocol ChunkHasher: Sendable {
    func sha256Hex(_ data: Data) -> String
}

public protocol UploadTransport: Sendable {
    /// POST /photos/{id}/original-session  (open)
    func openSession(photoID: String, claimedSha256: String,
                     chunkSize: Int, totalChunks: Int,
                     totalBytes: Int) async throws
        -> (uploadID: String, receivedChunks: [Int],
            echoedChunkSize: Int, echoedTotalChunks: Int)
    /// PUT chunk (server verifies X-Chunk-SHA256)
    func putChunk(photoID: String, uploadID: String, index: Int,
                  chunkSHA256: String, data: Data) async throws -> Bool
    /// GET status (resume: what does the server already hold?)
    func sessionStatus(photoID: String, uploadID: String) async throws
        -> (receivedChunks: [Int], totalChunks: Int)
    /// GET /photos/{id}/original/integrity (pre-check + post-check)
    func sessionIntegrity(photoID: String) async throws
        -> [String: Any]
    /// POST complete (server re-assembles + full-file hash proof)
    func complete(photoID: String, uploadID: String) async throws
        -> (bound: Bool, status: String)
    /// DELETE session
    func cancel(photoID: String, uploadID: String) async throws -> Void
}

public struct UploadProgress: Sendable, Equatable {
    public let sentChunks: Int
    public let totalChunks: Int
    public var fraction: Double {
        totalChunks == 0 ? 0 : Double(sentChunks) / Double(totalChunks)
    }
}

public actor ChunkedUploadClient {
    private let source: ChunkSource
    private let hasher: ChunkHasher
    private let transport: UploadTransport
    private let chunkSize: Int
    private let maxInFlight: Int
    private let claimedSha256: String

    public init(source: ChunkSource, hasher: ChunkHasher,
                transport: UploadTransport, claimedSha256: String,
                chunkSize: Int = 1024 * 1024,
                maxInFlight: Int = 3) {
        self.source = source
        self.hasher = hasher
        self.transport = transport
        self.claimedSha256 = claimedSha256
        self.chunkSize = chunkSize
        self.maxInFlight = maxInFlight
    }

    public private(set) var uploadID: String?
    public private(set) var receivedCount = 0
    public private(set) var totalChunks = 0

    public func start(photoID: String) async throws {
        guard chunkSize > 0 else {
            throw UploadError.named("chunkSize must be positive")
        }
        guard source.totalBytes > 0 else {
            throw UploadError.named("empty upload")
        }
        // M03/H02 parity with the Mac Integration 02 client: an
        // integrity GET FIRST skips a needless session when the
        // photo is already bound (the resume path after crashes).
        if let integrity = try? await transport.sessionIntegrity(
            photoID: photoID), Self.integrityConfirmed(
                integrity, claimed: claimedSha256) {
            return                    // already stored + bound
        }
        let n = (source.totalBytes + chunkSize - 1) / chunkSize
        let session = try await transport.openSession(
            photoID: photoID, claimedSha256: claimedSha256,
            chunkSize: chunkSize, totalChunks: n,
            totalBytes: source.totalBytes)
        // M03/H02: the open response ECHOES the session parameters -
        // a mismatched echo means a stale/proxied response; refuse.
        guard session.uploadID.count == 32,
              session.uploadID.allSatisfy({
                  $0.isHexDigit && $0.isLowercase || $0.isNumber }),
              session.echoedChunkSize == chunkSize,
              session.echoedTotalChunks == n,
              session.receivedChunks.allSatisfy({ (0..<n).contains($0) })
        else {
            throw UploadError.named("session open response echoes " +
                                    "mismatched parameters")
        }
        uploadID = session.uploadID
        totalChunks = n
        receivedCount = session.receivedChunks.count
        // openSession may RESUME an existing session (idempotent
        // re-open, TM35) - continue from what the server holds
        var have = Set(session.receivedChunks)
        try await sendMissing(photoID: photoID, have: &have)
        let done = try await transport.complete(photoID: photoID,
                                                uploadID: session.uploadID)
        // M03/H02 parity: post-complete integrity re-check - the
        // server's verdict, never the client's assumption.
        guard done.bound == true, done.status == "completed" else {
            throw UploadError.named("complete did not bind")
        }
        let final = try await transport.sessionIntegrity(
            photoID: photoID)
        guard Self.integrityConfirmed(final, claimed: claimedSha256)
        else {
            throw UploadError.named(
                "post-complete integrity failed to confirm the hash")
        }
    }

    /// Resume an interrupted upload by ASKING the server what it has
    /// (never re-hash-guessing from local state). Ends with the same
    /// post-complete integrity re-check as start().
    public func resume(photoID: String, uploadID: String) async throws {
        self.uploadID = uploadID
        let st = try await transport.sessionStatus(
            photoID: photoID, uploadID: uploadID)
        totalChunks = st.totalChunks
        var have = Set(st.receivedChunks)
        receivedCount = have.count
        try await sendMissing(photoID: photoID, have: &have)
        let done = try await transport.complete(photoID: photoID,
                                                uploadID: uploadID)
        guard done.bound == true, done.status == "completed" else {
            throw UploadError.named("complete did not bind")
        }
        let final = try await transport.sessionIntegrity(
            photoID: photoID)
        guard Self.integrityConfirmed(final, claimed: claimedSha256)
        else {
            throw UploadError.named(
                "post-complete integrity failed to confirm the hash")
        }
    }

    /// Mac Integration 02 parity: the server's integrity verdict
    /// keys (original.sha256, bound_to_photo,
    /// claims.file_integrity_verified) - never the client's own hash.
    static func integrityConfirmed(_ json: [String: Any],
                                    claimed: String) -> Bool {
        guard let original = json["original"] as? [String: Any],
              original["sha256"] as? String == claimed,
              json["bound_to_photo"] as? Bool == true,
              let claims = json["claims"] as? [String: Any],
              claims["file_integrity_verified"] as? Bool == true
        else { return false }
        return true
    }

    public func cancel(photoID: String) async throws {
        guard let id = uploadID else { return }
        try await transport.cancel(photoID: photoID, uploadID: id)
    }

    private func sendMissing(photoID: String, have: inout Set<Int>)
            async throws {
        let indices = (0..<totalChunks).filter { !have.contains($0) }
        var cursor = 0
        var inFlight = 0
        var pending: [Task<Void, Error>] = []
        while cursor < indices.count || !pending.isEmpty {
            while inFlight < maxInFlight && cursor < indices.count {
                let idx = indices[cursor]
                cursor += 1
                inFlight += 1
                pending.append(Task {
                    defer { inFlight -= 1 }
                    guard let data = source.chunk(at: idx,
                                                 size: chunkSize),
                          !data.isEmpty else {
                        throw UploadError.named(
                            "chunk source exhausted at index \(idx)")
                    }
                    let sha = hasher.sha256Hex(data)
                    _ = try await transport.putChunk(
                        photoID: photoID, uploadID: uploadID!,
                        index: idx, chunkSHA256: sha, data: data)
                    await addReceived()
                })
            }
            // await ONE child then compact (bounded concurrency)
            for task in pending {
                _ = try? await task.result.get()
            }
            pending.removeAll()
        }
    }

    private func addReceived() {
        receivedCount += 1
    }
}

// MARK: - test doubles (the Mac XCTest drives the full state machine
// with an in-memory transport that mirrors the TM35 server rules)

public struct ArrayChunkSource: ChunkSource {
    public let data: Data
    public init(data: Data) { self.data = data }
    public var totalBytes: Int { data.count }
    public func chunk(at index: Int, size: Int) -> Data? {
        let start = index * size
        guard start < data.count else { return nil }
        let end = min(start + size, data.count)
        return data.subdata(in: start..<end)
    }
}

public struct SHA256Stub: ChunkHasher {
    public init() {}
    public func sha256Hex(_ data: Data) -> String {
        // NOT a real hash: deterministic stub for tests. The app side
        // MUST inject CryptoKit's SHA256 - this stub is for the
        // rule tests only and says so wherever it is used.
        let n = UInt64(data.count)
        let seed = UInt64(0x9E3779B97F4A7C15)
        let mixed = n &* seed ^ UInt64(data.last ?? 0)
        return String(format: "%016x%016x", mixed, n)
    }
}
