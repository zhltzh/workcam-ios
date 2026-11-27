// TM-04 C03: repository protocols + dependency injection + the
// revision-checked in-memory reference store. Real Windows compile
// + unit tests.
//
// CONTRACT (mirrors the server + B08/B09 red lines):
//   - saves are REVISION-CHECKED: writing a stale revision is a
//     typed refusal, never a silent overwrite (the repository is
//     the local half of the server's CAS discipline)
//   - deletes are TOMBSTONES kept in a repository-side table: the
//     wire models (ProjectRecord/PhotoRecord, pinned by the OpenAPI
//     gate) stay untouched, deleted rows remain visible to history
//     queries, and `get` still resolves the last state
//   - everything is protocol-injected: tests use the memory doubles,
//     the app plugs SQLite-backed adapters on the Mac side.

import Foundation

// MARK: - protocol surface

public protocol ProjectRepository: Sendable {
    func get(_ id: String) -> ProjectRecord?
    /// includeDeleted=false hides tombstoned rows from listings.
    func list(includeDeleted: Bool) -> [ProjectRecord]
    /// Throws RepositoryError.staleRevision on CAS mismatch.
    func save(_ record: ProjectRecord) throws
    /// Tombstone delete - the row survives for history queries.
    func tombstone(_ id: String, at: Date) throws
}

public protocol PhotoRepository: Sendable {
    func get(_ id: String) -> PhotoRecord?
    func list(projectID: String, includeDeleted: Bool) -> [PhotoRecord]
    func save(_ record: PhotoRecord) throws
    func tombstone(_ id: String, at: Date) throws
}

public enum RepositoryError: Error, Equatable {
    case staleRevision(id: String, have: Int, need: Int)
    case notFound(id: String)
}

// MARK: - in-memory reference implementations

public final class MemoryProjectRepository: ProjectRepository,
                                            @unchecked Sendable {
    private var byID: [String: ProjectRecord] = [:]
    private var deletedAt: [String: Date] = [:]
    private let lock = NSLock()

    public init() {}

    public func get(_ id: String) -> ProjectRecord? {
        lock.lock(); defer { lock.unlock() }
        return byID[id]        // last state stays resolvable (B09)
    }

    public func list(includeDeleted: Bool) -> [ProjectRecord] {
        lock.lock(); defer { lock.unlock() }
        return byID.values
            .filter { includeDeleted || deletedAt[$0.projectID] == nil }
            .sorted { $0.projectID < $1.projectID }
    }

    public func save(_ record: ProjectRecord) throws {
        lock.lock(); defer { lock.unlock() }
        if let existing = byID[record.projectID],
           deletedAt[record.projectID] == nil {
            guard record.revision == existing.revision else {
                throw RepositoryError.staleRevision(
                    id: record.projectID,
                    have: record.revision,
                    need: existing.revision)
            }
        }
        byID[record.projectID] = record
        deletedAt.removeValue(forKey: record.projectID)
    }

    public func tombstone(_ id: String, at: Date) throws {
        lock.lock(); defer { lock.unlock() }
        guard byID[id] != nil else {
            throw RepositoryError.notFound(id: id)
        }
        deletedAt[id] = at
    }
}

public final class MemoryPhotoRepository: PhotoRepository,
                                           @unchecked Sendable {
    private var byID: [String: PhotoRecord] = [:]
    private var deletedAt: [String: Date] = [:]
    private let lock = NSLock()

    public init() {}

    public func get(_ id: String) -> PhotoRecord? {
        lock.lock(); defer { lock.unlock() }
        return byID[id]
    }

    public func list(projectID: String,
                     includeDeleted: Bool) -> [PhotoRecord] {
        lock.lock(); defer { lock.unlock() }
        return byID.values
            .filter { $0.projectID == projectID }
            .filter { includeDeleted
                      || deletedAt[$0.photoID] == nil }
            .sorted { $0.photoID < $1.photoID }
    }

    public func save(_ record: PhotoRecord) throws {
        lock.lock(); defer { lock.unlock() }
        byID[record.photoID] = record
        deletedAt.removeValue(forKey: record.photoID)
    }

    public func tombstone(_ id: String, at: Date) throws {
        lock.lock(); defer { lock.unlock() }
        guard byID[id] != nil else {
            throw RepositoryError.notFound(id: id)
        }
        deletedAt[id] = at
    }
}

// MARK: - DI container (type-keyed, explicit, test-friendly)

public final class ServiceRegistry: @unchecked Sendable {
    private var services: [ObjectIdentifier: Any] = [:]
    private let lock = NSLock()

    public init() {}

    public func register<Service>(_ service: Service,
                                  for type: Service.Type = Service.self) {
        lock.lock(); defer { lock.unlock() }
        services[ObjectIdentifier(type)] = service
    }

    public func resolve<Service>(_ type: Service.Type = Service.self) -> Service? {
        lock.lock(); defer { lock.unlock() }
        return services[ObjectIdentifier(type)] as? Service
    }
}

// MARK: - C14: injectable UUID + test doubles

public protocol UUIDProvider: Sendable {
    func next() -> String
}

public struct SystemUUIDProvider: UUIDProvider {
    public init() {}
    public func next() -> String { UUID().uuidString }
}

/// Deterministic sequential ids for tests (injectable, wall-clock
/// free).
public final class SequentialUUIDProvider: UUIDProvider, @unchecked Sendable {
    private var counter = 0
    private let prefix: String
    private let lock = NSLock()

    public init(prefix: String = "op") {
        self.prefix = prefix
    }

    public func next() -> String {
        lock.lock(); defer { lock.unlock() }
        counter += 1
        return "\(prefix)-\(String(format: "%06d", counter))"
    }
}
