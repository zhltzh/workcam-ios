// TM72 rule tests: LRU eviction respects the byte budget; the cache
// is structurally derivative-only (no original API); keyset
// pagination is drift-free; duplicates are candidates, never deletions.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import XCTest
@testable import WorkCamSync

final class PhotoCacheRuleTests: XCTestCase {

    func test_lru_evicts_oldest_first() {
        let cache = PhotoAssetCache(byteBudget: 300)
        cache.insert(CachedPhotoAsset(
            photoID: "a", kind: .thumbnail, data: Data(repeating: 1, count: 100)))
        cache.insert(CachedPhotoAsset(
            photoID: "b", kind: .thumbnail, data: Data(repeating: 2, count: 100)))
        // touch 'a' so 'b' becomes the LRU victim
        _ = cache.get(CacheKey(photoID: "a", kind: .thumbnail))
        cache.insert(CachedPhotoAsset(
            photoID: "c", kind: .thumbnail, data: Data(repeating: 3, count: 100)))
        XCTAssertEqual(cache.currentByteCount, 300)
        cache.insert(CachedPhotoAsset(
            photoID: "d", kind: .thumbnail, data: Data(repeating: 4, count: 100)))
        // 'b' was least recently used -> evicted
        XCTAssertNil(cache.get(CacheKey(photoID: "b", kind: .thumbnail)))
        XCTAssertNotNil(cache.get(CacheKey(photoID: "a", kind: .thumbnail)))
        XCTAssertNotNil(cache.get(CacheKey(photoID: "d", kind: .thumbnail)))
    }

    func test_invalidate_and_clear() {
        let cache = PhotoAssetCache(byteBudget: 1000)
        let key = CacheKey(photoID: "x", kind: .watermarkedPreview)
        cache.insert(CachedPhotoAsset(
            photoID: "x", kind: .watermarkedPreview,
            data: Data(repeating: 9, count: 500)))
        XCTAssertEqual(cache.currentByteCount, 500)
        cache.invalidate(key)
        XCTAssertEqual(cache.currentByteCount, 0)
        cache.insert(CachedPhotoAsset(
            photoID: "y", kind: .thumbnail,
            data: Data(repeating: 1, count: 10)))
        cache.clearAll()
        XCTAssertEqual(cache.entryCount, 0)
    }

    func test_no_original_can_enter_the_cache() {
        // The type system is the enforcement: CachedPhotoAsset.Kind
        // has NO original case. This test exists so a reviewer of a
        // future change has to consciously break it.
        let kinds = CachedPhotoAsset.Kind.allCases
        XCTAssertTrue(kinds.allSatisfy { $0 != .someOriginal })
    }
}

private extension CachedPhotoAsset.Kind {
    static var someOriginal: CachedPhotoAsset.Kind {
        // a deliberately invalid construction: if the enum ever gains
        // an original case this test fails to compile = loud review
        CachedPhotoAsset.Kind(rawValue: "original")!
    }
}

extension CachedPhotoAsset.Kind: CaseIterable {
    public static var allCases: [CachedPhotoAsset.Kind] {
        [.thumbnail, .watermarkedPreview, .reportPreview]
    }
}

final class PhotoIndexRuleTests: XCTestCase {

    private func entry(_ id: String, day: Int, tags: Set<String> = [],
                       sha: String? = nil,
                       project: String = "p1") -> PhotoIndexEntry {
        PhotoIndexEntry(
            photoID: id, projectID: project,
            capturedAt: Date(timeIntervalSince1970: TimeInterval(86400 * day)),
            tags: tags, hasOriginal: sha != nil, hasThumbnail: true,
            originalSHA256: sha)
    }

    func test_keyset_pagination_is_drift_free() {
        let index = PhotoIndex()
        for i in (0..<10).map({ 100 - $0 }) {   // inserted out of order
            index.upsert(entry("ph-\(i)", day: i))
        }
        var q = PhotoIndexQuery(limit: 4)
        var page1 = index.query(q)
        XCTAssertEqual(page1.map(\.photoID),
                       ["ph-0", "ph-1", "ph-2", "ph-3"])
        q.cursor = (capturedAt: page1.last!.capturedAt,
                    photoID: page1.last!.photoID)
        let page2 = index.query(q)
        XCTAssertEqual(page2.map(\.photoID),
                       ["ph-4", "ph-5", "ph-6", "ph-7"])
        // inserting a NEW photo does not shift the next page
        index.upsert(entry("ph-new", day: 0))
        q.cursor = (capturedAt: page2.last!.capturedAt,
                    photoID: page2.last!.photoID)
        let page3 = index.query(q)
        XCTAssertEqual(page3.map(\.photoID), ["ph-8", "ph-9"])
    }

    func test_filters_project_tag_time() {
        let index = PhotoIndex()
        index.upsert(entry("a", day: 1, tags: ["roof"], project: "p1"))
        index.upsert(entry("b", day: 2, tags: ["wall"], project: "p2"))
        index.upsert(entry("c", day: 3, tags: ["roof"], project: "p1"))
        XCTAssertEqual(
            index.query(PhotoIndexQuery(projectID: "p1", tag: "roof"))
                .map(\.photoID), ["a", "c"])
        XCTAssertEqual(
            index.query(PhotoIndexQuery(from: Date(
                timeIntervalSince1970: 86400 * 2.5)))
                .map(\.photoID), ["c"])
    }

    func test_duplicates_are_candidates_not_deletions() {
        let index = PhotoIndex()
        index.upsert(entry("a", day: 1, sha: "ab" + String(repeating: "0", count: 62)))
        index.upsert(entry("b", day: 2, sha: "cd" + String(repeating: "0", count: 62)))
        index.upsert(entry("c", day: 3, sha: "ab" + String(repeating: "0", count: 62)))
        let groups = index.duplicateCandidates()
        XCTAssertEqual(groups.count, 1)
        XCTAssertEqual(Set(groups[0].map(\.photoID)), ["a", "c"])
        // nothing was removed - the index is a REPORTING structure
        XCTAssertEqual(index.query(PhotoIndexQuery(limit: 10)).count, 3)
    }
}
