import Foundation

@main
struct MockSyncOutboxTests {
    static func main() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let file = directory.appendingPathComponent("mock-outbox.json")
        let first = SyncOutbox.Operation(id: UUID(), body: Data("mock first".utf8))
        let second = SyncOutbox.Operation(id: UUID(), body: Data("mock second".utf8))

        do {
            let outbox = try SyncOutbox(file: file)
            try await outbox.enqueue(first)
            try await outbox.enqueue(first)
            try await outbox.enqueue(second)
            do {
                try await outbox.enqueue(.init(id: first.id, body: second.body))
                fatalError("Expected reused ID to be rejected")
            } catch SyncOutbox.Failure.reusedOperationID {}
        }

        do {
            let outbox = try SyncOutbox(file: file)
            let pending = await outbox.pending()
            precondition(pending == [first, second])
            try await outbox.acknowledge([first.id, UUID()])
        }

        let outbox = try SyncOutbox(file: file)
        let pending = await outbox.pending()
        precondition(pending == [second])
        print("PASS · Mock outbox · restart, duplicate enqueue, ID conflict and partial acknowledgment")
    }
}
