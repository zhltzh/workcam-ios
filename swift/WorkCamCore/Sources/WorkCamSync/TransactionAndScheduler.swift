// TM-04 C04 + C09: persistence transaction abstraction and the
// sync dependency-graph scheduler. Real Windows compile + tests.
//
// C04: TransactionLog is the testable in-memory reference for the
// LOCAL persistence transaction contract: staged writes are visible
// to the transaction but must not survive a rollback; commit applies
// them atomically (all-or-nothing) to the store. Production maps
// this onto SQLite transactions on the app side.
//
// C09: SyncSchedulerDependencyBatches turns the outbox's dependsOn
// edges into Kahn topological batches: batch N contains only ops
// whose dependencies are fully acknowledged by batches < N. Cycles
// are REFUSED with the offending operation names (never silently
// dropped, never an infinite loop).

import Foundation

// MARK: - C04: transactional key-value store (reference semantics)

public protocol TransactionalStore: AnyObject {
    func get(_ key: String) -> String?
    /// Staged write - visible inside the transaction only.
    func set(_ key: String, _ value: String)
    /// Staged delete - visible inside the transaction only.
    func remove(_ key: String)
    /// Apply all staged operations atomically; rollback discards.
    func commit() throws
    func rollback()
    /// True when a transaction is open on this store.
    var inTransaction: Bool { get }
}

/// In-memory reference implementation: the contract the SQLite
/// adapter must reproduce.
public final class InMemoryTransactionalStore: TransactionalStore {
    private var committed: [String: String] = [:]
    private var staged: [String: String?] = [:]   // nil = deletion
    private var open = false
    private let lock = NSLock()

    public init() {}

    public var inTransaction: Bool { open }

    @discardableResult
    public func begin() -> Self {
        lock.lock(); defer { lock.unlock() }
        precondition(!open, "nested transactions are not part of the contract")
        open = true
        staged = [:]
        return self
    }

    public func get(_ key: String) -> String? {
        lock.lock(); defer { lock.unlock() }
        // staged[key] is String? where nil MEANS deletion - a plain
        // `if let` would confuse "staged delete" with "not staged"
        // (the exact bug the first real run caught).
        if open, let idx = staged.index(forKey: key) {
            return staged[idx].value
        }
        return committed[key]
    }

    public func set(_ key: String, _ value: String) {
        lock.lock(); defer { lock.unlock() }
        precondition(open, "set outside a transaction")
        staged[key] = value
    }

    public func remove(_ key: String) {
        lock.lock(); defer { lock.unlock() }
        precondition(open, "remove outside a transaction")
        // `staged[key] = nil` would DELETE the key on a
        // Dictionary<String, String?> (missing vs deleted confusion,
        // the mirror of the get-side bug); updateValue stores the
        // deletion marker explicitly.
        staged.updateValue(nil, forKey: key)
    }

    public func commit() throws {
        lock.lock(); defer { lock.unlock() }
        precondition(open, "commit without a transaction")
        for (k, v) in staged {
            if let v { committed[k] = v } else { committed.removeValue(forKey: k) }
        }
        staged = [:]
        open = false
    }

    public func rollback() {
        lock.lock(); defer { lock.unlock() }
        staged = [:]
        open = false
    }

    /// Committed view (read-only snapshot for assertions).
    public func committedSnapshot() -> [String: String] {
        lock.lock(); defer { lock.unlock() }
        return committed
    }
}

extension TransactionalStore {
    /// Perform `body` inside a transaction; a thrown error rolls
    /// everything back and rethrows - the store keeps its prior
    /// state exactly (C04 acceptance rule).
    public func withTransaction<T>(_ body: () throws -> T) throws -> T {
        if let selfStore = self as? InMemoryTransactionalStore {
            selfStore.begin()
        }
        do {
            let result = try body()
            try commit()
            return result
        } catch {
            rollback()
            throw error
        }
    }
}

// MARK: - C09: dependency-graph batch scheduler

public enum SyncSchedulerError: Error, Equatable {
    case dependencyMissing(op: String, missing: String)
    case cycleDetected(ops: [String])
}

public enum SyncScheduler {

    /// Kahn topological layering over `dependsOn`, restricted to the
    /// ops actually being scheduled. Dependencies that are already
    /// acknowledged (present in `alreadyAcked`) do not constrain
    /// scheduling. Returns batches in execution order; every batch
    /// is dependency-closed relative to the batches before it.
    public static func dependencyBatches(
        _ ops: [OutboxOperation],
        alreadyAcked: Set<String> = [])
            throws -> [[OutboxOperation]] {
        let byID = Dictionary(
            ops.map { ($0.operationID, $0) },
            uniquingKeysWith: { a, _ in a })

        // every dependency must exist among the ops or be acked
        for op in ops {
            for dep in op.dependsOn
            where !byID.keys.contains(dep)
                && !alreadyAcked.contains(dep) {
                throw SyncSchedulerError.dependencyMissing(
                    op: op.operationID, missing: dep)
            }
        }

        // in-schedule edges only (acked deps are free)
        var remaining = Set(byID.keys)
        var inDegree: [String: Int] = [:]
        for op in ops {
            inDegree[op.operationID] = op.dependsOn
                .filter { remaining.contains($0) }.count
        }

        var batches: [[OutboxOperation]] = []
        while !remaining.isEmpty {
            let layer = remaining
                .filter { (inDegree[$0] ?? 0) == 0 }
                .sorted { $0 < $1 }          // deterministic
            if layer.isEmpty {
                throw SyncSchedulerError.cycleDetected(
                    ops: remaining.sorted())
            }
            batches.append(layer.map { byID[$0]! })
            for id in layer {
                remaining.remove(id)
                for op in ops where op.dependsOn.contains(id) {
                    inDegree[op.operationID, default: 0] -= 1
                }
            }
        }
        return batches
    }
}
