import Foundation
func point(_ x: Int, _ y: Int, _ depth: Float = 2, _ fx: Float = 100) -> SIMD3<Float>? {
    DepthGeometry.cameraPoint(x: x, y: y, width: 2, height: 2,
                              imageWidth: 200, imageHeight: 200,
                              fx: fx, fy: 100, cx: 100, cy: 100, depth: depth)
}
precondition(point(0, 0) == SIMD3<Float>(-1, 1, -2))
precondition(point(1, 1) == SIMD3<Float>(1, -1, -2))
precondition(point(-1, 0) == nil && point(2, 0) == nil)
precondition(point(0, 0, .nan) == nil && point(0, 0, -1) == nil)
precondition(point(0, 0, 11) == nil && point(0, 0, 2, 0) == nil)
print("PASS: synthetic depth geometry axes, scaling, pixel centers and invalid input; no device evidence")

let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
let archive = directory.appendingPathComponent("records.json")
defer { try? FileManager.default.removeItem(at: directory) }
let empty = try LabArchive.load(archive)
precondition(empty.isEmpty)
let row = ["batch", "test,with comma", "quote\"", "line\nbreak", "中文", ""].map(LabArchive.quoted).joined(separator: ",")
try LabArchive.save([row], to: archive)
let restored = try LabArchive.load(archive)
precondition(restored == [row])
try Data("corrupt".utf8).write(to: archive)
do { try LabArchive.save([], to: archive); fatalError("corrupt archive overwritten") } catch { }
let unchanged = try String(contentsOf: archive, encoding: .utf8)
precondition(unchanged == "corrupt")
try row.write(toFile: "/tmp/workcam-csv-fixture.csv", atomically: true, encoding: .utf8)
print("PASS: archive reload, corruption preservation, CSV escaping fixture (synthetic)")
