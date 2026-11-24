import Foundation

struct CaptureStore {
    static func save(_ data: Data) throws -> URL {
        try EvidenceStore.capture(data)
    }
}

struct WorkProject: Codable, Identifiable, Sendable {
    let id: UUID
    var name: String
}

struct WorkPhoto: Identifiable, Sendable {
    let url: URL
    let capturedAt: Date
    let localEntry: URL
    var id: String { localEntry.deletingPathExtension().lastPathComponent }
    var annotationURL: URL { localEntry.deletingPathExtension().appendingPathExtension("annotation.json") }
}

enum ProjectStore {
    private static func root() throws -> URL {
        let support = try FileManager.default.url(for: .applicationSupportDirectory,
                                                  in: .userDomainMask, appropriateFor: nil, create: true)
        let root = support.appendingPathComponent("WorkProjects", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    private static func directory(_ id: UUID) throws -> URL {
        try root().appendingPathComponent(id.uuidString, isDirectory: true)
    }

    static func projects() throws -> [WorkProject] {
        let folders = try FileManager.default.contentsOfDirectory(at: root(), includingPropertiesForKeys: nil)
        return try folders.compactMap { folder in
            guard UUID(uuidString: folder.lastPathComponent) != nil else { return nil }
            let data = try Data(contentsOf: folder.appendingPathComponent("project.json"))
            return try JSONDecoder().decode(WorkProject.self, from: data)
        }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    static func create(_ name: String) throws -> WorkProject {
        let project = WorkProject(id: UUID(), name: name)
        let root = try root()
        let staging = root.appendingPathComponent(".\(project.id.uuidString)")
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: staging) }
        try JSONEncoder().encode(project).write(to: staging.appendingPathComponent("project.json"), options: .atomic)
        try FileManager.default.moveItem(at: staging, to: directory(project.id))
        return project
    }

    static func rename(_ project: WorkProject, to name: String) throws -> WorkProject {
        var updated = project
        updated.name = name
        let url = try directory(project.id).appendingPathComponent("project.json")
        try JSONEncoder().encode(updated).write(to: url, options: .atomic)
        return updated
    }

    static func capture(_ data: Data, in project: WorkProject) throws -> URL {
        let timestamp = Int64((Date().timeIntervalSince1970 * 1000).rounded())
        let recordID = UUID()
        let reference = try directory(project.id).appendingPathComponent("\(timestamp)-\(recordID.uuidString).evidence")
        try Data().write(to: reference, options: [.atomic, .completeFileProtection])
        return try EvidenceStore.capture(data, recordID: recordID)
    }

    static func photos(in project: WorkProject) throws -> [WorkPhoto] {
        let evidenceRoot = try EvidenceStore.root()
        try EvidenceStore.recoverPending(in: evidenceRoot)
        return try FileManager.default.contentsOfDirectory(at: directory(project.id), includingPropertiesForKeys: nil)
            .compactMap { entry in
                let components = entry.deletingPathExtension().lastPathComponent.split(separator: "-", maxSplits: 1)
                guard components.count == 2, let timestamp = Int64(components[0]) else { return nil }
                let original: URL
                switch entry.pathExtension {
                case "jpg":
                    original = entry
                case "evidence":
                    guard let id = UUID(uuidString: String(components[1])) else { return nil }
                    original = evidenceRoot.appendingPathComponent(id.uuidString.lowercased(), isDirectory: true)
                        .appendingPathComponent("photo.jpg")
                    guard FileManager.default.fileExists(atPath: original.path) else { return nil }
                default:
                    return nil
                }
                return WorkPhoto(url: original, capturedAt: Date(timeIntervalSince1970: Double(timestamp) / 1000),
                                 localEntry: entry)
            }
            .sorted { ($0.capturedAt, $0.id) > ($1.capturedAt, $1.id) }
    }

    static func annotation(for photo: WorkPhoto) throws -> PhotoAnnotation? {
        let url = photo.annotationURL
        do {
            return try JSONDecoder().decode(PhotoAnnotation.self, from: Data(contentsOf: url))
        } catch let error as CocoaError where error.code == .fileReadNoSuchFile {
            return nil
        }
    }

    static func save(_ annotation: PhotoAnnotation, for photo: WorkPhoto) throws {
        let url = photo.annotationURL
        try JSONEncoder().encode(annotation).write(to: url, options: [.atomic, .completeFileProtection])
    }
}

struct PhotoAnnotation: Codable, Sendable {
    let x: Double
    let y: Double
    let note: String
}
