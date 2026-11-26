import Foundation

let id = UUID()
let serialized = Data("{\"id\":\"\(id.uuidString)\",\"name\":\"Legacy\"}".utf8)
let project = try JSONDecoder().decode(WorkProject.self, from: serialized)
precondition(project.archived == nil && project.id == id)
let photoURL = URL(fileURLWithPath: CommandLine.arguments[1]).appendingPathComponent("original.jpg")
let original = Data([1, 2, 3])
try original.write(to: photoURL)
let photo = WorkPhoto(url: photoURL, capturedAt: .now, localEntry: photoURL)
try Data("{\"x\":0.25,\"y\":0.75,\"note\":\"legacy\"}".utf8).write(to: photo.annotationURL)
let legacy = try ProjectStore.annotations(for: photo)
precondition(legacy.count == 1 && legacy.first?.kind == nil)
let annotations = AnnotationKind.allCases.map {
    PhotoAnnotation(x: 0.125, y: 0.8, note: $0.rawValue, kind: $0, color: .yellow, lineWidth: 9)
}
try ProjectStore.save(annotations, for: photo)
let loaded = try ProjectStore.annotations(for: photo)
precondition(loaded.map(\.kind) == annotations.map(\.kind) && loaded.allSatisfy { $0.color == .yellow && $0.lineWidth == 9 })
try ProjectStore.save([], for: photo)
let cleared = try ProjectStore.annotations(for: photo)
let preserved = try Data(contentsOf: photoURL)
precondition(cleared.isEmpty && preserved == original)
print("PASS legacy project, six annotation shapes and styles, empty override and immutable original")
