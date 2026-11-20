import Foundation

enum LabArchive {
    static func quoted(_ value: String) -> String {
        "\"" + value.replacingOccurrences(of: "\"", with: "\"\"") + "\""
    }
    struct Record: Codable {
        let fields: [String]
        let legacyCSV: String?
    }
    struct Envelope: Codable {
        let schemaVersion: Int
        let records: [Record]
    }
    enum ArchiveError: Error { case unsupportedVersion, invalidCSV }
    static func parse(_ row: String) throws -> [String] {
        var fields: [String] = [], field = "", quoted = false
        var index = row.startIndex
        while index < row.endIndex {
            let c = row[index]
            let next = row.index(after: index)
            if c == "\"" {
                if quoted && next < row.endIndex && row[next] == "\"" {
                    field.append("\""); index = row.index(after: next); continue
                }
                quoted.toggle()
            } else if c == "," && !quoted { fields.append(field); field = "" }
            else { field.append(c) }
            index = next
        }
        guard !quoted else { throw ArchiveError.invalidCSV }
        fields.append(field)
        return fields
    }
    static func load(_ url: URL) throws -> [String] {
        guard FileManager.default.fileExists(atPath: url.path) else { return [] }
        let data = try Data(contentsOf: url)
        if let old = try? JSONDecoder().decode([String].self, from: data) { return old }
        let envelope = try JSONDecoder().decode(Envelope.self, from: data)
        guard envelope.schemaVersion == 2 else { throw ArchiveError.unsupportedVersion }
        return envelope.records.map { $0.legacyCSV ?? $0.fields.map(quoted).joined(separator: ",") }
    }
    static func save(_ rows: [String], to url: URL) throws {
        _ = try load(url)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let records = try rows.map { row in
            Record(fields: try parse(row), legacyCSV: row.hasPrefix("\"") ? nil : row)
        }
        let data = try JSONEncoder().encode(Envelope(schemaVersion: 2, records: records))
        if FileManager.default.fileExists(atPath: url.path) {
            let backup = url.appendingPathExtension("pre-v2-backup")
            if !FileManager.default.fileExists(atPath: backup.path) {
                try FileManager.default.copyItem(at: url, to: backup)
            }
        }
        try data.write(to: url, options: .atomic)
    }
}
