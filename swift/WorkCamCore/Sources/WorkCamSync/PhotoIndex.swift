// TM72: photo index & cache management - Swift reference component.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).
//
// THE SACRED RULE (task brief, enforced structurally here):
//   cache eviction NEVER deletes trusted originals. The LRU below is
//   TYPED to derivative/thumbnail entries; there is no API to insert
//   an original into it, so the failure mode cannot be expressed.

import Foundation

/// A cacheable item is BY CONSTRUCTION a derivative (thumbnail /
/// watermarked preview / report preview). Originals live in the
/// object store and are never handed to this cache.
public struct CachedPhotoAsset: Sendable {
    public enum Kind: String, Sendable {
        case thumbnail
        case watermarkedPreview = "watermarked_preview"
        case reportPreview = "report_preview"
    }
    public let photoID: String
    public let kind: Kind
    public let data: Data
    public let byteCount: Int

    public init(photoID: String, kind: Kind, data: Data) {
        self.photoID = photoID
        self.kind = kind
        self.data = data
        self.byteCount = data.count
    }

    public var key: CacheKey { CacheKey(photoID: photoID, kind: kind) }
}

public struct CacheKey: Hashable, Sendable {
    public let photoID: String
    public let kind: CachedPhotoAsset.Kind
    public init(photoID: String, kind: CachedPhotoAsset.Kind) {
        self.photoID = photoID
        self.kind = kind
    }
}

/// Thread-safe LRU with a byte budget. Eviction order is pure
/// least-recently-used; ties break by insertion order (deterministic
/// for tests).
public final class PhotoAssetCache: @unchecked Sendable {
    private var store: [CacheKey: (asset: CachedPhotoAsset,
                                   lastUsed: Int)] = [:]
    private var useCounter = 0
    private var currentBytes = 0
    private let byteBudget: Int
    private let lock = NSLock()

    public init(byteBudget: Int = 64 * 1024 * 1024) {
        self.byteBudget = byteBudget
    }

    public func get(_ key: CacheKey) -> CachedPhotoAsset? {
        lock.lock(); defer { lock.unlock() }
        guard var entry = store[key] else { return nil }
        useCounter += 1
        entry.lastUsed = useCounter
        store[key] = entry
        return entry.asset
    }

    public func insert(_ asset: CachedPhotoAsset) {
        lock.lock(); defer { lock.unlock() }
        let key = asset.key
        if let old = store[key] {
            currentBytes -= old.asset.byteCount
        }
        useCounter += 1
        store[key] = (asset, useCounter)
        currentBytes += asset.byteCount
        evictIfNeeded()
    }

    public func invalidate(_ key: CacheKey) {
        lock.lock(); defer { lock.unlock() }
        if let old = store.removeValue(forKey: key) {
            currentBytes -= old.asset.byteCount
        }
    }

    /// Cache clear (user-initiated storage cleanup). Derivatives only
    /// by construction; originals are structurally out of scope.
    public func clearAll() {
        lock.lock(); defer { lock.unlock() }
        store.removeAll()
        currentBytes = 0
    }

    public var currentByteCount: Int {
        lock.lock(); defer { lock.unlock() }
        return currentBytes
    }

    public var entryCount: Int {
        lock.lock(); defer { lock.unlock() }
        return store.count
    }

    private func evictIfNeeded() {
        while currentBytes > byteBudget {
            guard let (key, _) = store.min(by: {
                $0.value.lastUsed < $1.value.lastUsed }) else {
                return     // single oversized item: kept, caller
            }             // sized the budget - named at init
            let old = store.removeValue(forKey: key)!
            currentBytes -= old.asset.byteCount
        }
    }
}

// MARK: - photo index (query model; the app persists it, the engine
// defines the shape + in-memory default)

public struct PhotoIndexEntry: Sendable, Equatable {
    public let photoID: String
    public let projectID: String
    public let capturedAt: Date
    public let tags: Set<String>
    public let hasOriginal: Bool
    public let hasThumbnail: Bool
    /// SHA-256 of the original when present; duplicate candidates
    /// share this digest.
    public let originalSHA256: String?

    public init(photoID: String, projectID: String, capturedAt: Date,
                tags: Set<String>, hasOriginal: Bool,
                hasThumbnail: Bool, originalSHA256: String?) {
        self.photoID = photoID
        self.projectID = projectID
        self.capturedAt = capturedAt
        self.tags = tags
        self.hasOriginal = hasOriginal
        self.hasThumbnail = hasThumbnail
        self.originalSHA256 = originalSHA256
    }
}

public struct PhotoIndexQuery: Sendable {
    public var projectID: String?
    public var tag: String?
    public var from: Date?
    public var to: Date?
    /// keyset pagination (TM34 stable-order rule: capturedAt +
    /// photoID; no OFFSET drift)
    public var cursor: (capturedAt: Date, photoID: String)?
    public var limit: Int = 50

    public init(projectID: String? = nil, tag: String? = nil,
                from: Date? = nil, to: Date? = nil,
                cursor: (capturedAt: Date, photoID: String)? = nil,
                limit: Int = 50) {
        self.projectID = projectID
        self.tag = tag
        self.from = from
        self.to = to
        self.cursor = cursor
        self.limit = limit
    }
}

/// In-memory index with the SAME keyset semantics as the server's
/// stable sort - the Mac persistence layer swaps in SQLite with the
/// documented schema (TM71) and this contract stays.
public final class PhotoIndex: @unchecked Sendable {
    private var entries: [PhotoIndexEntry] = []
    private let lock = NSLock()

    public init() {}

    public func upsert(_ entry: PhotoIndexEntry) {
        lock.lock(); defer { lock.unlock() }
        entries.removeAll { $0.photoID == entry.photoID }
        entries.append(entry)
    }

    public func query(_ q: PhotoIndexQuery) -> [PhotoIndexEntry] {
        lock.lock(); defer { lock.unlock() }
        var out = entries.filter { e in
            if let p = q.projectID, e.projectID != p { return false }
            if let t = q.tag, !e.tags.contains(t) { return false }
            if let f = q.from, e.capturedAt < f { return false }
            if let t = q.to, e.capturedAt > t { return false }
            if let c = q.cursor {
                // strict keyset: strictly AFTER (capturedAt, photoID)
                if e.capturedAt == c.capturedAt {
                    return e.photoID > c.photoID
                }
                return e.capturedAt > c.capturedAt
            }
            return true
        }
        // stable order: capturedAt ASC, photoID ASC (server contract)
        out.sort {
            $0.capturedAt == $1.capturedAt
                ? $0.photoID < $1.photoID
                : $0.capturedAt < $1.capturedAt
        }
        return Array(out.prefix(q.limit))
    }

    /// Exact duplicates (same original digest) - CANDIDATES for human
    /// review, never auto-delete (TM27 rule carried into the index).
    public func duplicateCandidates() -> [[PhotoIndexEntry]] {
        lock.lock(); defer { lock.unlock() }
        var byDigest: [String: [PhotoIndexEntry]] = [:]
        for e in entries {
            guard let sha = e.originalSHA256 else { continue }
            byDigest[sha, default: []].append(e)
        }
        return byDigest.values.filter { $0.count > 1 }
    }
}
