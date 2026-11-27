// TM-04 C03/C13/C14 tests: repositories + tombstones + DI +
// availability gates + injectable UUIDs. REAL Windows execution.

import XCTest
import WorkCamSync
import WorkCamModels

final class RepositoryTests: XCTestCase {

    private func project(_ id: String, rev: Int) -> ProjectRecord {
        ProjectRecord(projectID: id, name: "n", description: "",
                      siteAddress: "", status: "active",
                      revision: rev,
                      createdAt: Date(timeIntervalSince1970: 0),
                      updatedAt: Date(timeIntervalSince1970: 0))
    }

    func test_stale_revision_save_is_a_typed_refusal() async throws {
        let repo = MemoryProjectRepository()
        try repo.save(project("p1", rev: 3))
        XCTAssertThrowsError(
            try repo.save(project("p1", rev: 2))) { error in
            XCTAssertEqual(
                error as? RepositoryError,
                .staleRevision(id: "p1", have: 2, need: 3))
        }
        // the stored row was NOT overwritten
        let stored = repo.get("p1")
        XCTAssertEqual(stored?.revision, 3)
    }

    func test_matching_revision_save_applies() async throws {
        let repo = MemoryProjectRepository()
        try repo.save(project("p1", rev: 3))
        try repo.save(project("p1", rev: 3))   // CAS-match
        let stored = repo.get("p1")
        XCTAssertEqual(stored?.revision, 3)
    }

    func test_tombstone_hides_from_listing_keeps_get_resolvable()
            async throws {
        let repo = MemoryProjectRepository()
        try repo.save(project("p1", rev: 1))
        try repo.save(project("p2", rev: 1))
        try repo.tombstone("p1",
                                 at: Date(timeIntervalSince1970: 99))

        let visible = repo.list(includeDeleted: false)
        XCTAssertEqual(visible.map(\.projectID), ["p2"])
        let history = repo.list(includeDeleted: true)
        XCTAssertEqual(Set(history.map(\.projectID)), ["p1", "p2"])
        // last state still resolvable for evidence screens
        let got = repo.get("p1")
        XCTAssertEqual(got?.name, "n")
    }

    func test_tombstone_of_unknown_id_is_notFound() async {
        let repo = MemoryProjectRepository()
        do {
            try repo.tombstone("ghost",
                                     at: Date(timeIntervalSince1970: 0))
            XCTFail("expected notFound")
        } catch {
            XCTAssertEqual(error as? RepositoryError,
                           .notFound(id: "ghost"))
        }
    }

    func test_photo_repository_scopes_by_project() async throws {
        let repo = MemoryPhotoRepository()
        let base = Date(timeIntervalSince1970: 0)
        try repo.save(PhotoRecord(
            photoID: "a", projectID: "p1", originalFilename: "a.jpg",
            mimeType: "image/jpeg", byteSize: 1, createdAt: base))
        try repo.save(PhotoRecord(
            photoID: "b", projectID: "p2", originalFilename: "b.jpg",
            mimeType: "image/jpeg", byteSize: 1, createdAt: base))
        let inP1 = repo.list(projectID: "p1",
                                   includeDeleted: false)
        XCTAssertEqual(inP1.map(\.photoID), ["a"])
        try repo.tombstone("a", at: base)
        let afterDelete = repo.list(projectID: "p1",
                                          includeDeleted: false)
        XCTAssertEqual(afterDelete, [])
    }

    func test_service_registry_roundtrip() {
        let registry = ServiceRegistry()
        let repo = MemoryProjectRepository()
        registry.register(repo, for: ProjectRepository.self)
        let resolved: ProjectRepository? = registry.resolve()
        XCTAssertTrue(resolved is MemoryProjectRepository)
        // type-mismatched resolution is nil, not a crash
        let wrong: PhotoRepository? = registry.resolve()
        XCTAssertNil(wrong)
    }
}

final class AvailabilityTests: XCTestCase {

    func test_offline_never_blocks_the_shutter() {
        let gate = FeatureGate.captureAvailability(
            camera: .granted, offline: .offline)
        XCTAssertEqual(gate, .available)   // TM67 red line
    }

    func test_denied_camera_is_unavailable_with_named_reason() {
        let gate = FeatureGate.captureAvailability(
            camera: .denied, offline: .online)
        guard case .unavailable(let reason) = gate else {
            return XCTFail("expected unavailable")
        }
        XCTAssertTrue(reason.contains("denied"))
    }

    func test_not_determined_maps_to_permission_requirement() {
        XCTAssertEqual(
            FeatureGate.captureAvailability(camera: .notDetermined,
                                            offline: .online),
            .requiresPermission(.camera))
    }

    func test_sync_offline_labels_the_queue_honestly() {
        let gate = FeatureGate.syncAvailability(
            authenticated: true, offline: .offline)
        guard case .unavailable(let reason) = gate else {
            return XCTFail("expected unavailable offline")
        }
        XCTAssertTrue(reason.contains("queue"))
    }

    func test_sync_requires_enrollment_first() {
        let gate = FeatureGate.syncAvailability(
            authenticated: false, offline: .online)
        guard case .unavailable(let reason) = gate else {
            return XCTFail("expected unavailable unenrolled")
        }
        XCTAssertTrue(reason.contains("enrolled"))
    }

    func test_restricted_is_distinct_from_denied() {
        XCTAssertEqual(
            FeatureGate.captureAvailability(camera: .restricted,
                                            offline: .online),
            .unavailable(reason: "Camera access is restricted on this device."))
    }
}

final class UUIDProviderTests: XCTestCase {

    func test_sequential_provider_is_deterministic_and_unique() {
        let p = SequentialUUIDProvider(prefix: "op")
        XCTAssertEqual(p.next(), "op-000001")
        XCTAssertEqual(p.next(), "op-000002")
        XCTAssertEqual(p.next(), "op-000003")
    }

    func test_system_provider_yields_unique_ids() {
        let p = SystemUUIDProvider()
        let ids = Set((0..<100).map { _ in p.next() })
        XCTAssertEqual(ids.count, 100)
    }

    func test_sequential_provider_is_injectable_through_protocol() {
        let provider: UUIDProvider = SequentialUUIDProvider(prefix: "t")
        XCTAssertEqual(provider.next(), "t-000001")
    }
}
