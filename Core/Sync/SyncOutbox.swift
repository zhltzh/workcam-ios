import Foundation

/// One writer per file. Store operation bodies only, without authentication material.
actor SyncOutbox {
    struct Operation: Codable, Equatable, Sendable {
        let id: UUID
        let body: Data
    }

    enum Failure: Error, Equatable {
        case reusedOperationID
    }

    private let file: URL
    private var operations: [Operation]

    init(file: URL) throws {
        self.file = file
        do {
            operations = try JSONDecoder().decode([Operation].self, from: Data(contentsOf: file))
        } catch let error as CocoaError where error.code == .fileReadNoSuchFile {
            operations = []
        }
    }

    func enqueue(_ operation: Operation) throws {
        if let existing = operations.first(where: { $0.id == operation.id }) {
            guard existing == operation else { throw Failure.reusedOperationID }
            return
        }
        try persist(operations + [operation])
    }

    func pending() -> [Operation] {
        operations
    }

    /// Apply only IDs that the transport has confirmed as accepted or already applied.
    func acknowledge(_ ids: Set<UUID>) throws {
        let remaining = operations.filter { !ids.contains($0.id) }
        guard remaining.count != operations.count else { return }
        try persist(remaining)
    }

    private func persist(_ updated: [Operation]) throws {
        try JSONEncoder().encode(updated).write(to: file, options: .atomic)
        operations = updated
    }
}
