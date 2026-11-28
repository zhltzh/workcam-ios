import Foundation

struct CaptureStore {
    static func save(_ data: Data, context: CaptureContext? = nil) throws -> URL {
        try EvidenceStore.capture(data, context: context)
    }
}

struct WorkProject: Codable, Identifiable, Sendable {
    let id: UUID
    var name: String
    var archived: Bool? = nil
}

struct WorkPhoto: Identifiable, Sendable {
    let url: URL
    let capturedAt: Date
    let localEntry: URL
    var id: String { localEntry.deletingPathExtension().lastPathComponent }
    var collectionURL: URL { localEntry.deletingPathExtension().appendingPathExtension("annotations.json") }
    var annotationURL: URL { localEntry.deletingPathExtension().appendingPathExtension("annotation.json") }
}

enum ProjectStore {
    private static func root() throws -> URL {
        let support: URL
        #if DEBUG && targetEnvironment(simulator)
        if let fixture = Night04Fixture.root {
            support = fixture
        } else {
            support = try FileManager.default.url(for: .applicationSupportDirectory,
                                                  in: .userDomainMask, appropriateFor: nil, create: true)
        }
        #else
        support = try FileManager.default.url(for: .applicationSupportDirectory,
                                                  in: .userDomainMask, appropriateFor: nil, create: true)
        #endif
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
        try save(updated)
        return updated
    }

    static func setArchived(_ archived: Bool, for project: WorkProject) throws {
        var updated = project
        updated.archived = archived
        try save(updated)
    }

    private static func save(_ project: WorkProject) throws {
        let url = try directory(project.id).appendingPathComponent("project.json")
        try JSONEncoder().encode(project).write(to: url, options: [.atomic, .completeFileProtection])
    }

    static func capture(_ data: Data, in project: WorkProject, context: CaptureContext? = nil) throws -> URL {
        let timestamp = Int64((Date().timeIntervalSince1970 * 1000).rounded())
        let projectFolder = try directory(project.id)
        let stored = try JSONDecoder().decode(WorkProject.self, from: Data(contentsOf:
            projectFolder.appendingPathComponent("project.json")))
        guard stored.id == project.id, stored.archived != true else { throw CocoaError(.fileWriteNoPermission) }
        return try EvidenceStore.capture(data, projectLink: .init(projectID: project.id, timestamp: timestamp), context: context)
    }

    static func photos(in project: WorkProject) throws -> [WorkPhoto] {
        let evidenceRoot = try EvidenceStore.root()
        try EvidenceStore.recoverPending(in: evidenceRoot)
        let files = FileManager.default
        let projectFolder = try directory(project.id)
        let existing: [WorkPhoto] = try files.contentsOfDirectory(at: projectFolder, includingPropertiesForKeys: nil)
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
        let linked = Set(existing.map { $0.url.standardizedFileURL })
        let committed: [WorkPhoto] = try files.contentsOfDirectory(at: evidenceRoot, includingPropertiesForKeys: nil)
            .compactMap { record in
                guard let id = UUID(uuidString: record.lastPathComponent),
                      let data = try? Data(contentsOf: record.appendingPathComponent("project-link.json")),
                      let link = try? JSONDecoder().decode(ProjectEvidenceLink.self, from: data),
                      link.projectID == project.id else { return nil }
                let original = record.appendingPathComponent("photo.jpg")
                guard files.fileExists(atPath: original.path), !linked.contains(original.standardizedFileURL) else { return nil }
                let entry = projectFolder.appendingPathComponent("\(link.timestamp)-\(id.uuidString).evidence")
                return WorkPhoto(url: original, capturedAt: Date(timeIntervalSince1970: Double(link.timestamp) / 1000),
                                 localEntry: entry)
            }
        return (existing + committed).sorted { ($0.capturedAt, $0.id) > ($1.capturedAt, $1.id) }
    }

    static func issues(for project: WorkProject) throws -> [InspectionIssue] {
        let file = try directory(project.id).appendingPathComponent("issues.json")
        do {
            return try JSONDecoder().decode([InspectionIssue].self, from: Data(contentsOf: file))
        } catch let error as CocoaError where error.code == .fileReadNoSuchFile { return [] }
    }

    // UI mutations are serialized on the main actor.
    @MainActor
    static func saveIssue(_ issue: InspectionIssue, for project: WorkProject) throws {
        var issues = try issues(for: project)
        var updated = issue
        updated.description = updated.description.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !updated.description.isEmpty else { throw CocoaError(.fileWriteInapplicableStringEncoding) }
        let validPhotos = Set(try photos(in: project).map(\.id))
        guard updated.photos.values.allSatisfy(validPhotos.contains),
              updated.photos.keys.allSatisfy({ InspectionIssue.Role(rawValue: $0) != nil }) else {
            throw CocoaError(.fileWriteNoPermission)
        }
        if let index = issues.firstIndex(where: { $0.id == updated.id }) {
            let old = issues[index]
            guard old.number == updated.number, old.history.count == updated.history.count, old.status != .closed,
                  updated.status == old.status || updated.status == old.status.next else {
                throw CocoaError(.fileWriteNoPermission)
            }
            updated.history = old.history
            updated.history.append(updated.snapshot)
            issues[index] = updated
        } else {
            guard updated.status == .open, updated.history.isEmpty,
                  updated.number == (issues.map(\.number).max() ?? 0) + 1 else {
                throw CocoaError(.fileWriteNoPermission)
            }
            updated.history = [updated.snapshot]
            issues.append(updated)
        }
        let file = try directory(project.id).appendingPathComponent("issues.json")
        try JSONEncoder().encode(issues).write(to: file, options: [.atomic, .completeFileProtection])
    }

    static func annotations(for photo: WorkPhoto) throws -> [PhotoAnnotation] {
        let decoder = JSONDecoder()
        do {
            return try decoder.decode([PhotoAnnotation].self, from: Data(contentsOf: photo.collectionURL))
        } catch let error as CocoaError where error.code == .fileReadNoSuchFile {
            do {
                return [try decoder.decode(PhotoAnnotation.self, from: Data(contentsOf: photo.annotationURL))]
            } catch let error as CocoaError where error.code == .fileReadNoSuchFile {
                return []
            }
        }
    }

    static func save(_ annotations: [PhotoAnnotation], for photo: WorkPhoto) throws {
        try JSONEncoder().encode(annotations).write(to: photo.collectionURL, options: [.atomic, .completeFileProtection])
    }
}

enum AnnotationKind: String, Codable, CaseIterable, Sendable {
    case marker = "Numbered Marker", arrow = "Arrow", rectangle = "Rectangle"
    case ellipse = "Ellipse", text = "Text", freehand = "Freehand"
}

struct AnnotationPoint: Codable, Sendable {
    let x: Double
    let y: Double
}

enum AnnotationColor: String, Codable, CaseIterable, Sendable {
    case red = "Red", yellow = "Yellow", blue = "Blue", white = "White"
}

struct PhotoAnnotation: Codable, Sendable {
    let x: Double
    let y: Double
    var note: String
    var kind: AnnotationKind? = nil
    var points: [AnnotationPoint]? = nil
    var color: AnnotationColor? = nil
    var lineWidth: Double? = nil
}

struct InspectionIssue: Codable, Identifiable, Sendable {
    enum Severity: String, Codable, CaseIterable, Sendable {
        case low, medium, high, critical
    }
    enum Status: String, Codable, CaseIterable, Sendable {
        case open, inProgress = "in_progress", resolved, closed
        var next: Self? {
            switch self {
            case .open: .inProgress
            case .inProgress: .resolved
            case .resolved: .closed
            case .closed: nil
            }
        }
    }
    enum Role: String, Codable, CaseIterable, Sendable { case before, progress, after }
    struct Change: Codable, Sendable {
        let timestamp: Date
        let status: Status
        let description: String
        let severity: Severity
        let photos: [String: String]
    }

    let id: UUID
    let number: Int
    var description: String
    var severity: Severity
    var status: Status
    var photos: [String: String]
    var history: [Change]
    var snapshot: Change {
        Change(timestamp: .now, status: status, description: description, severity: severity, photos: photos)
    }
}

/// Uncompressed ZIP32 archive for bounded, deterministic offline deliveries.
struct DeliveryZIP {
    private(set) var entries: [(name: String, sha256: String)] = []
    private var bytes = Data()
    private var directory = Data()

    private let byteLimit: Int

    init(byteLimit: Int = 128 * 1024 * 1024) {
        self.byteLimit = min(max(22, byteLimit), 128 * 1024 * 1024)
    }

    private func checkBudget(name: String, size: Int) throws {
        let length = name.utf8.count
        // Local header, central header, both path copies, and end record.
        let overhead = 30 + 46 + 2 * length + 22
        guard length > 0, length <= Int(UInt16.max), entries.count < Int(UInt16.max),
              size >= 0, size <= byteLimit - bytes.count - directory.count - overhead else {
            throw CocoaError(.fileWriteOutOfSpace)
        }
    }

    mutating func add(_ name: String, file: URL) throws {
        let properties = try file.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
        guard properties.isRegularFile == true, let size = properties.fileSize else {
            throw CocoaError(.fileReadUnknown)
        }
        try checkBudget(name: name, size: size)
        try add(name, data: Data(contentsOf: file, options: .mappedIfSafe))
    }

    mutating func add(_ name: String, data: Data) throws {
        try checkBudget(name: name, size: data.count)
        let path = Data(name.utf8)
        let offset = UInt32(bytes.count)
        let length = UInt32(data.count)
        let crc = Self.checksum(data)
        bytes.zipWrite(UInt32(0x04034b50))
        for value: UInt16 in [20, 0x0800, 0, 0, 33] { bytes.zipWrite(value) }
        for value in [crc, length, length] { bytes.zipWrite(value) }
        bytes.zipWrite(UInt16(path.count))
        bytes.zipWrite(UInt16(0))
        bytes.append(path)
        bytes.append(data)

        directory.zipWrite(UInt32(0x02014b50))
        for value: UInt16 in [20, 20, 0x0800, 0, 0, 33] { directory.zipWrite(value) }
        for value in [crc, length, length] { directory.zipWrite(value) }
        for value in [UInt16(path.count), 0, 0, 0, 0] { directory.zipWrite(value) }
        directory.zipWrite(UInt32(0))
        directory.zipWrite(offset)
        directory.append(path)
        entries.append((name, evidenceHash(data)))
    }

    mutating func finish() -> Data {
        let offset = UInt32(bytes.count)
        bytes.append(directory)
        bytes.zipWrite(UInt32(0x06054b50))
        for value: UInt16 in [0, 0, UInt16(entries.count), UInt16(entries.count)] { bytes.zipWrite(value) }
        bytes.zipWrite(UInt32(directory.count))
        bytes.zipWrite(offset)
        bytes.zipWrite(UInt16(0))
        return bytes
    }

    private static let table: [UInt32] = (0..<256).map { index in
        (0..<8).reduce(UInt32(index)) { value, _ in
            (value >> 1) ^ (value & 1 == 0 ? 0 : 0xedb88320)
        }
    }

    private static func checksum(_ data: Data) -> UInt32 {
        ~data.reduce(UInt32.max) { crc, byte in
            (crc >> 8) ^ table[Int((crc ^ UInt32(byte)) & 0xff)]
        }
    }
}

private extension Data {
    mutating func zipWrite<T: FixedWidthInteger>(_ value: T) {
        var le = value.littleEndian
        Swift.withUnsafeBytes(of: &le) { append(contentsOf: $0) }
    }
}
