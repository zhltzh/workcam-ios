import Foundation

let folder = URL(fileURLWithPath: CommandLine.arguments[1])
let file = folder.appendingPathComponent("inbox.json")
let inbox = try SyncInbox(file: file)
let first = SyncInbox.Event(sequence: 4, kind: "project", id: "p", action: "created", tombstone: false,
                            snapshot: Data(#"{"name":"Server project"}"#.utf8))
try await inbox.apply(.init(events: [first], cursor: 4, more: true))
let reopened = try SyncInbox(file: file)
let cursor = await reopened.cursor()
let saved = await reopened.snapshots()
precondition(cursor == 4 && saved.first?.summary == "Server project")
do {
    try await reopened.apply(.init(events: [first], cursor: 4, more: true))
    fatalError("Stuck page was accepted")
} catch is URLError {}

let blocked = folder.appendingPathComponent("blocked.json")
let failing = try SyncInbox(file: blocked)
try FileManager.default.createDirectory(at: blocked, withIntermediateDirectories: false)
do {
    try await failing.apply(.init(events: [first], cursor: 4, more: false))
    fatalError("An unwritable destination was accepted")
} catch is CocoaError {}
let unchanged = await failing.cursor()
let empty = await failing.snapshots()
precondition(unchanged == 0 && empty.isEmpty)
print("PASS atomic cursor + server snapshot, reopen, non-progress rejection and write failure")
