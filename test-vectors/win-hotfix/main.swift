import Foundation

struct Fixtures: Decodable {
    let archive_request: SyncBatch
    let rejected_replay: SyncReceipt
    let archive_page: SyncEventPage
    let upload_session: OriginalSession
}

let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let cases = try JSONDecoder().decode(Fixtures.self, from: data)
precondition(cases.archive_request.operations.first?.op == "project_archive")
precondition(!cases.rejected_replay.confirmed && cases.rejected_replay.error?.code == "revision_conflict")
precondition(cases.archive_page.items.last?.seq == cases.archive_page.cursor)
precondition(cases.archive_page.items.last?.tombstone == false)
precondition(Set(0..<cases.upload_session.total_chunks).subtracting(cases.upload_session.received_chunks) == [1])
let encoded = try JSONEncoder().encode(cases.archive_request)
_ = try JSONDecoder().decode(SyncBatch.self, from: encoded)
print("PASS Swift Codable: archive, rejected replay, cursor and sparse upload")
