import Foundation

enum LabArchive {
    static func quoted(_ value: String) -> String {
        "\"" + value.replacingOccurrences(of: "\"", with: "\"\"") + "\""
    }
    static func load(_ url: URL) throws -> [String] {
        guard FileManager.default.fileExists(atPath: url.path) else { return [] }
        return try JSONDecoder().decode([String].self, from: Data(contentsOf: url))
    }
    static func save(_ rows: [String], to url: URL) throws {
        // Never replace a corrupt existing archive.
        _ = try load(url)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(rows).write(to: url, options: .atomic)
    }
}
