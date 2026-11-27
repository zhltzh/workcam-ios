// TM-04 C04/C05/C09 tests: atomic writes, transactions, and the
// dependency scheduler. REAL execution on Windows Swift 6.4.3 + Mac.

import XCTest
import WorkCamSync

final class AtomicFileTests: XCTestCase {

    private var dir: URL!

    override func setUp() {
        super.setUp()
        dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("wcam-atomic-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(
            at: dir, withIntermediateDirectories: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: dir)
        super.tearDown()
    }

    private func url(_ name: String) -> URL {
        dir.appendingPathComponent(name)
    }

    func test_portable_sha256_matches_standard_vectors() {
        // FIPS 180-4 vectors (single-block + multi-block + padding)
        XCTAssertEqual(AtomicFile.sha256Hex(Data("abc".utf8)),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        XCTAssertEqual(AtomicFile.sha256Hex(Data("".utf8)),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        XCTAssertEqual(AtomicFile.sha256Hex(Data(
            "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq".utf8)),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1")
    }

    func test_atomic_write_replaces_target_and_reads_back() throws {
        let target = url("state.json")
        try AtomicFile.write(Data("v1".utf8), to: target)
        try AtomicFile.write(Data("v2".utf8), to: target)
        let back = try Data(contentsOf: target)
        XCTAssertEqual(String(decoding: back, as: UTF8.self), "v2")
    }

    func test_write_verified_roundtrip() throws {
        let target = url("blob.bin")
        let payload = Data((0..<2048).map { UInt8($0 % 251) })
        try AtomicFile.writeVerified(payload, to: target)
        let back = try Data(contentsOf: target)
        XCTAssertEqual(back, payload)
    }

    func test_stale_temp_cleanup_never_touches_committed_files() throws {
        // committed artifact that MUST survive
        let real = url("photo-12.jpg")
        try Data("jpeg".utf8).write(to: real)
        // crash residue in OUR namespace
        let t1 = url("state.json.wcamtmp-AAAA")
        let t2 = url("blob.bin.wcamtmp-BBBB-CC")
        try Data("x".utf8).write(to: t1)
        try Data("y".utf8).write(to: t2)
        // a file that merely CONTAINS the marker but is not a temp
        let sneaky = url("report-wcamtmp-2026.pdf")
        try Data("z".utf8).write(to: sneaky)

        let removed = AtomicFile.removeStaleTemps(
            in: dir, now: Date(timeIntervalSince1970: 1_000_000))

        XCTAssertEqual(removed, 2)
        XCTAssertTrue(FileManager.default.fileExists(atPath: real.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: sneaky.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: t1.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: t2.path))
    }

    func test_temp_age_gate_respected() throws {
        let fresh = url("a.wcamtmp-NEW")
        try Data("n".utf8).write(to: fresh)
        // fresh temp (mtime = real now) must survive an old-age gate
        let removed = AtomicFile.removeStaleTemps(
            in: dir, olderThan: 3600, now: Date())
        XCTAssertEqual(removed, 0)
        XCTAssertTrue(FileManager.default.fileExists(atPath: fresh.path))
    }
}

final class TransactionTests: XCTestCase {

    func test_commit_applies_all_staged_writes() throws {
        let store = InMemoryTransactionalStore()
        try store.withTransaction {
            store.set("a", "1")
            store.set("b", "2")
        }
        XCTAssertEqual(store.committedSnapshot(), ["a": "1", "b": "2"])
    }

    func test_rollback_on_error_leaves_state_untouched() {
        let store = InMemoryTransactionalStore()
        struct Boom: Error {}
        XCTAssertThrowsError(try store.withTransaction {
            store.set("a", "1")
            store.set("b", "2")
            throw Boom()
        })
        XCTAssertEqual(store.committedSnapshot(), [:])   // zero residue
        XCTAssertFalse(store.inTransaction)
    }

    func test_staged_delete_visible_inside_but_not_before_rollback() throws {
        let store = InMemoryTransactionalStore()
        try store.withTransaction { store.set("k", "v0") }
        store.begin()
        store.remove("k")
        XCTAssertEqual(store.get("k"), nil)     // staged view
        store.rollback()
        XCTAssertEqual(store.get("k"), "v0")    // committed view intact
    }

    func test_isolated_transactions_do_not_leak_staged_state() throws {
        let store = InMemoryTransactionalStore()
        store.begin()
        store.set("x", "staged")
        XCTAssertEqual(store.get("x"), "staged")
        store.rollback()
        store.begin()
        XCTAssertNil(store.get("x"))     // staged write did NOT leak
        store.rollback()
    }
}

final class SyncSchedulerTests: XCTestCase {

    private func op(_ id: String, deps: [String] = []) -> OutboxOperation {
        var o = OutboxOperation(operationID: id, op: "project_update",
                                payloadJSON: "{}")
        // dependsOn is immutable post-init in the public API; build
        // via the initializer's parameter instead
        return OutboxOperation(operationID: id, op: "project_update",
                                payloadJSON: "{}", dependsOn: deps)
    }

    func test_linear_chain_layers_topologically() throws {
        let batches = try SyncScheduler.dependencyBatches([
            op("c", deps: ["b"]), op("a"), op("b", deps: ["a"]),
        ])
        XCTAssertEqual(batches.map { $0.map(\.operationID) },
                       [["a"], ["b"], ["c"]])
    }

    func test_diamond_dependencies_share_levels_correctly() throws {
        let batches = try SyncScheduler.dependencyBatches([
            op("root"),
            op("l", deps: ["root"]), op("r", deps: ["root"]),
            op("join", deps: ["l", "r"]),
        ])
        XCTAssertEqual(batches.map { $0.map(\.operationID) },
                       [["root"], ["l", "r"], ["join"]])
    }

    func test_acknowledged_dependencies_do_not_constrain() throws {
        let batches = try SyncScheduler.dependencyBatches(
            [op("b", deps: ["already-done"])],
            alreadyAcked: ["already-done"])
        XCTAssertEqual(batches.map { $0.map(\.operationID) }, [["b"]])
    }

    func test_missing_dependency_is_a_named_error() {
        XCTAssertThrowsError(
            try SyncScheduler.dependencyBatches(
                [op("b", deps: ["ghost"])])) { error in
            XCTAssertEqual(
                error as? SyncSchedulerError,
                .dependencyMissing(op: "b", missing: "ghost"))
        }
    }

    func test_cycle_is_detected_and_names_every_member() {
        XCTAssertThrowsError(
            try SyncScheduler.dependencyBatches([
                op("a", deps: ["c"]), op("b", deps: ["a"]),
                op("c", deps: ["b"]),
            ])) { error in
            guard case SyncSchedulerError.cycleDetected(let ops)
                    = error else {
                return XCTFail("expected cycleDetected, got \(error)")
            }
            XCTAssertEqual(Set(ops), ["a", "b", "c"])
        }
    }

    func test_batches_are_deterministic_for_equal_input() throws {
        let ops = [op("z"), op("m", deps: ["z"]), op("a", deps: ["z"])]
        let b1 = try SyncScheduler.dependencyBatches(ops)
        let b2 = try SyncScheduler.dependencyBatches(ops)
        XCTAssertEqual(b1.map { $0.map(\.operationID) },
                       b2.map { $0.map(\.operationID) })
        XCTAssertEqual(b1[1].map(\.operationID), ["a", "m"])  // sorted
    }
}
