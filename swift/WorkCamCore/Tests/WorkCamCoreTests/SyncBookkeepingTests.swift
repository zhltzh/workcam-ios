// TM-04 B05/B09/B10 tests: UUID mapping guards, lifecycle terminal
// semantics, atomic cursor persistence. REAL Windows execution.

import XCTest
import WorkCamSync

final class SyncBookkeepingTests: XCTestCase {

    // MARK: B05

    func test_mapping_roundtrip_both_directions() throws {
        let store = UUIDMappingStore()
        let at = Date(timeIntervalSince1970: 500)
        try store.associate(entityType: "project", localID: "L-1",
                             serverID: "S-9f", at: at)
        XCTAssertEqual(
            store.serverID(entityType: "project", localID: "L-1")?
                .serverID, "S-9f")
        XCTAssertEqual(
            store.localID(entityType: "project", serverID: "S-9f")?
                .localID, "L-1")
    }

    func test_same_pair_replay_is_idempotent() throws {
        let store = UUIDMappingStore()
        let at = Date(timeIntervalSince1970: 500)
        let first = try store.associate(
            entityType: "photo", localID: "L-1", serverID: "S-1",
            at: at)
        let replay = try store.associate(
            entityType: "photo", localID: "L-1", serverID: "S-1",
            at: at.addingTimeInterval(60))
        XCTAssertEqual(first, replay)        // same version, no dupe
        XCTAssertEqual(store.count, 1)
    }

    func test_conflicting_server_id_for_same_local_is_refused() throws {
        let store = UUIDMappingStore()
        try store.associate(entityType: "project", localID: "L-1",
                             serverID: "S-a", at: Date())
        XCTAssertThrowsError(
            try store.associate(entityType: "project", localID: "L-1",
                                serverID: "S-b", at: Date())) { e in
            XCTAssertEqual(
                e as? UUIDMappingError,
                .conflictingServerID(entityType: "project",
                                     localID: "L-1",
                                     have: "S-a", got: "S-b"))
        }
    }

    func test_server_id_already_bound_refuses_second_local() throws {
        let store = UUIDMappingStore()
        try store.associate(entityType: "photo", localID: "L-1",
                             serverID: "S-shared", at: Date())
        XCTAssertThrowsError(
            try store.associate(entityType: "photo", localID: "L-2",
                                serverID: "S-shared", at: Date()))
        XCTAssertEqual(store.count, 1)
    }

    func test_entity_types_are_independent_namespaces() throws {
        let store = UUIDMappingStore()
        try store.associate(entityType: "project", localID: "x",
                             serverID: "S-1", at: Date())
        // same server id in ANOTHER type namespace is fine
        XCTAssertNoThrow(try store.associate(
            entityType: "issue", localID: "x", serverID: "S-1",
            at: Date()))
        XCTAssertEqual(store.count, 2)
    }

    // MARK: B09

    func test_active_reaches_all_nonterminal_states() {
        XCTAssertTrue(EntityLifecycle.allowedTransition(
            from: .active, to: .tombstoned))
        XCTAssertTrue(EntityLifecycle.allowedTransition(
            from: .active, to: .archived))
        XCTAssertTrue(EntityLifecycle.allowedTransition(
            from: .active, to: .closedTerminal))
    }

    func test_tombstone_is_final_except_replay() {
        XCTAssertFalse(EntityLifecycle.allowedTransition(
            from: .tombstoned, to: .active))
        XCTAssertFalse(EntityLifecycle.allowedTransition(
            from: .tombstoned, to: .archived))
        XCTAssertFalse(EntityLifecycle.allowedTransition(
            from: .tombstoned, to: .closedTerminal))
        XCTAssertTrue(EntityLifecycle.allowedTransition(
            from: .tombstoned, to: .tombstoned))   // marker replay
    }

    func test_archived_requires_explicit_unarchive_only() {
        XCTAssertTrue(EntityLifecycle.allowedTransition(
            from: .archived, to: .active))
        XCTAssertFalse(EntityLifecycle.allowedTransition(
            from: .archived, to: .tombstoned))
        XCTAssertFalse(EntityLifecycle.allowedTransition(
            from: .archived, to: .closedTerminal))
    }

    func test_closed_terminal_refuses_every_mutation() {
        for target in EntityLifecycle.allCases {
            if target == .closedTerminal { continue }
            XCTAssertFalse(EntityLifecycle.allowedTransition(
                from: .closedTerminal, to: target),
                "closedTerminal -> \(target) must be refused")
        }
        XCTAssertFalse(EntityLifecycle.closedTerminal
            .allowsContentEdits)
        XCTAssertFalse(EntityLifecycle.closedTerminal
            .acceptsNewPhotos)
    }

    func test_archived_refuses_new_photos_and_edits() {
        XCTAssertFalse(EntityLifecycle.archived.acceptsNewPhotos)
        XCTAssertFalse(EntityLifecycle.archived.allowsContentEdits)
    }

    // MARK: B10

    private func tempCursorFile() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "wcam-cursor-\(UUID().uuidString).json")
    }

    func test_cursor_advances_and_never_moves_backwards() throws {
        let store = CursorStateStore(fileURL: tempCursorFile())
        try store.advance(ownerID: "o1", entityType: "photo",
                          to: 5, at: Date())
        try store.advance(ownerID: "o1", entityType: "photo",
                          to: 5, at: Date())   // same = no-op
        try store.advance(ownerID: "o1", entityType: "photo",
                          to: 3, at: Date())   // backwards = refused
        XCTAssertEqual(
            store.position(ownerID: "o1", entityType: "photo"), 5)
    }

    func test_cursor_scopes_are_independent_per_owner_and_type() throws {
        let store = CursorStateStore(fileURL: tempCursorFile())
        try store.advance(ownerID: "o1", entityType: "photo",
                          to: 10, at: Date())
        try store.advance(ownerID: "o1", entityType: "issue",
                          to: 3, at: Date())
        try store.advance(ownerID: "o2", entityType: "photo",
                          to: 7, at: Date())
        XCTAssertEqual(store.position(ownerID: "o1",
                                      entityType: "photo"), 10)
        XCTAssertEqual(store.position(ownerID: "o1",
                                      entityType: "issue"), 3)
        XCTAssertEqual(store.position(ownerID: "o2",
                                      entityType: "photo"), 7)
        XCTAssertEqual(store.position(ownerID: "o2",
                                      entityType: "issue"), 0)
    }

    func test_cursor_survives_a_restart_via_atomic_file() throws {
        let file = tempCursorFile()
        let store = CursorStateStore(fileURL: file)
        try store.advance(ownerID: "o1", entityType: "photo",
                          to: 42, at: Date())
        // crash + restart: a NEW store instance over the same file
        // resumes exactly where the previous one committed.
        let resurrected = CursorStateStore(fileURL: file)
        XCTAssertEqual(resurrected.position(ownerID: "o1",
                                             entityType: "photo"),
                       42)
        try? FileManager.default.removeItem(at: file)
    }

    func test_restarted_store_never_skips_or_rewinds() throws {
        let file = tempCursorFile()
        let store = CursorStateStore(fileURL: file)
        try store.advance(ownerID: "o1", entityType: "event",
                          to: 8, at: Date())
        let resurrected = CursorStateStore(fileURL: file)
        // backwards is a guarded NO-OP (idempotent anti-rewind), not
        // an error: replayed events can never move the cursor back
        XCTAssertNoThrow(try resurrected.advance(
            ownerID: "o1", entityType: "event", to: 6, at: Date()))
        XCTAssertEqual(resurrected.position(ownerID: "o1",
                                             entityType: "event"), 8)
        try? FileManager.default.removeItem(at: file)
    }
}
