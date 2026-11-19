#if DEBUG
import SwiftUI
import ARKit
import SceneKit

@MainActor
final class LidarLabModel: ObservableObject {
    @Published var status = "Tap Start to request camera access"
    @Published var rows: [String] = []
    @Published var reference = "1.0"
    @Published var mode = "raycast"
    @Published var condition = "normal"
    @Published var targetDistance = "1.0"
    @Published var storageError = ""
    @Published var batch = UUID().uuidString
    @Published var testID = "T04-001"
    private var observedConfidence: Int?
    private var depthReceived = false
    private var tracking = "unavailable"
    private var running = false
    private var originConfidence: Int?
    private var elapsedStart = ProcessInfo.processInfo.systemUptime
    private var hardware: String {
        var info = utsname(); uname(&info)
        return withUnsafePointer(to: &info.machine) {
            $0.withMemoryRebound(to: CChar.self, capacity: 256) { String(cString: $0) }
        }
    }
    var batches: [String] {
        Array(Set(rows.compactMap { $0.split(separator: "\"", omittingEmptySubsequences: false).dropFirst().first.map(String.init) } + [batch])).sorted()
    }
    func newBatch() { stop(); batch = UUID().uuidString }
    private func quoted(_ value: String) -> String {
        LabArchive.quoted(value)
    }
    private var loadFailed = false
    private let archive = URL.applicationSupportDirectory.appending(path: "LidarLabRows.json")
    init() {
        do {
            if FileManager.default.fileExists(atPath: archive.path) {
                rows = try LabArchive.load(archive)
                if let savedBatch = rows.last?.split(separator: "\"", omittingEmptySubsequences: false).dropFirst().first, !savedBatch.isEmpty { batch = String(savedBatch) }
            }
        } catch { loadFailed = true; storageError = "Cannot load records: \(error.localizedDescription)" }
    }
    private func persist() {
        guard !loadFailed else { return }
        do {
            try FileManager.default.createDirectory(at: archive.deletingLastPathComponent(), withIntermediateDirectories: true)
            try LabArchive.save(rows, to: archive)
        } catch { storageError = "Records not saved: \(error.localizedDescription)" }
    }
    let view = ARSCNView(frame: .zero)
    private var first: SIMD3<Float>?
    private var started = Date()
    private var timer: Timer?
    func start() {
        guard ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) else {
            status = "Scene depth unsupported"; return
        }
        let configuration = ARWorldTrackingConfiguration()
        configuration.frameSemantics = [.sceneDepth]
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth) {
            configuration.frameSemantics.insert(.smoothedSceneDepth)
        }
        running = true
        view.session.run(configuration, options: [.resetTracking, .removeExistingAnchors])
        first = nil
        view.scene.rootNode.childNodes.forEach { $0.removeFromParentNode() }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }
    func refresh() {
        guard let frame = view.session.currentFrame else { status = "Waiting for frame"; return }
        status = "Tracking: \(frame.camera.trackingState); depth: \(frame.sceneDepth == nil ? "unavailable" : "available")"
        if case .normal = frame.camera.trackingState { } else { first = nil }
    }
    func stop() { running = false; timer?.invalidate(); timer = nil; view.session.pause(); first = nil }
    func sample() {
        let elapsed = max(0, ProcessInfo.processInfo.systemUptime - elapsedStart)
        observedConfidence = nil; depthReceived = false
        tracking = view.session.currentFrame.map { String(describing: $0.camera.trackingState) } ?? "unavailable"
        guard running else { record(nil, elapsed, "session_not_running"); return }
        guard let current = view.session.currentFrame,
              ProcessInfo.processInfo.systemUptime - current.timestamp < 1 else {
            record(nil, elapsed, "stale_frame"); first = nil; return
        }
        guard let frame = view.session.currentFrame,
              case .normal = frame.camera.trackingState else {
            record(nil, elapsed, "tracking_unavailable"); first = nil; return
        }
        let point: SIMD3<Float>
        if mode == "raycast" {
            guard let query = view.raycastQuery(from: CGPoint(x: view.bounds.midX, y: view.bounds.midY),
                                               allowing: .estimatedPlane, alignment: .any),
                  let hit = view.session.raycast(query).first else {
                record(nil, elapsed, "raycast_miss"); first = nil; return
            }
            let t = hit.worldTransform.columns.3
            point = SIMD3<Float>(t.x, t.y, t.z)
        } else {
            guard let depth = mode == "scene_depth" ? frame.sceneDepth : frame.smoothedSceneDepth else {
                record(nil, elapsed, "depth_missing"); first = nil; return
            }
            depthReceived = true
            guard let confidence = depth.confidenceMap else {
                record(nil, elapsed, "confidence_missing"); first = nil; return
            }
            guard let orientation = view.window?.windowScene?.interfaceOrientation else {
                record(nil, elapsed, "orientation_missing"); first = nil; return
            }
            let inverse = frame.displayTransform(for: orientation, viewportSize: view.bounds.size).inverted()
            let image = CGPoint(x: 0.5, y: 0.5).applying(inverse)
            guard image.x >= 0, image.x < 1, image.y >= 0, image.y < 1 else {
                record(nil, elapsed, "pixel_outside_image"); first = nil; return
            }
            let map = depth.depthMap
            let x = Int(image.x * Double(CVPixelBufferGetWidth(map)))
            let y = Int(image.y * Double(CVPixelBufferGetHeight(map)))
            CVPixelBufferLockBaseAddress(map, .readOnly)
            CVPixelBufferLockBaseAddress(confidence, .readOnly)
            defer {
                CVPixelBufferUnlockBaseAddress(map, .readOnly)
                CVPixelBufferUnlockBaseAddress(confidence, .readOnly)
            }
            guard CVPixelBufferGetWidth(confidence) == CVPixelBufferGetWidth(map),
                  CVPixelBufferGetHeight(confidence) == CVPixelBufferGetHeight(map),
                  let bytes = CVPixelBufferGetBaseAddress(map),
                  let quality = CVPixelBufferGetBaseAddress(confidence) else {
                record(nil, elapsed, "invalid_depth_buffer"); first = nil; return
            }
            let z = bytes.advanced(by: y * CVPixelBufferGetBytesPerRow(map)).assumingMemoryBound(to: Float.self)[x]
            let level = quality.advanced(by: y * CVPixelBufferGetBytesPerRow(confidence)).assumingMemoryBound(to: UInt8.self)[x]
            observedConfidence = Int(level)
            guard level >= 2, z.isFinite, z > 0, z <= 10 else {
                record(nil, elapsed, level < 2 ? "low_confidence" : "invalid_depth"); first = nil; return
            }
            let k = frame.camera.intrinsics
            let resolution = frame.camera.imageResolution
            guard let optical = DepthGeometry.cameraPoint(x: x, y: y,
                width: CVPixelBufferGetWidth(map), height: CVPixelBufferGetHeight(map),
                imageWidth: resolution.width, imageHeight: resolution.height,
                fx: k.columns.0.x, fy: k.columns.1.y, cx: k.columns.2.x, cy: k.columns.2.y, depth: z) else {
                record(nil, elapsed, "invalid_intrinsics"); first = nil; return
            }
            let camera = SIMD4<Float>(optical.x, optical.y, optical.z, 1)
            let world = frame.camera.transform * camera
            point = SIMD3<Float>(world.x, world.y, world.z)
        }
        let marker = SCNNode(geometry: SCNSphere(radius: 0.008))
        marker.geometry?.firstMaterial?.diffuse.contents = UIColor.systemYellow
        marker.simdPosition = point
        view.scene.rootNode.addChildNode(marker)
        if let origin = first {
            let distance = Double(simd_distance(origin, point))
            record(distance.isFinite && distance > 0 && distance <= 10 ? distance : nil,
                   elapsed, distance.isFinite && distance > 0 && distance <= 10 ? "" : "distance_outlier")
            first = nil
        } else { first = point; originConfidence = observedConfidence; elapsedStart = ProcessInfo.processInfo.systemUptime; status = "First point set; aim at second point" }
    }
    private func record(_ distance: Double?, _ elapsed: Double, _ failure: String) {
        let standard = Double(reference).flatMap { $0.isFinite && $0 > 0 ? $0 : nil }
        let error = distance.flatMap { d in standard.map { abs(d - $0) } }
        let relative = error.flatMap { e in standard.map { e / $0 } }
        func value(_ n: Double?) -> String { n.map { String($0) } ?? "" }
        rows.append([batch, testID, hardware, UIDevice.current.systemVersion, String(depthReceived),
                     observedConfidence.map(String.init) ?? "", originConfidence.map(String.init) ?? "", tracking, mode, condition, ISO8601DateFormatter().string(from: Date()), value(standard), value(distance),
                     value(error), value(relative), String(elapsed), failure, value(Double(targetDistance))].map(quoted).joined(separator: ","))
        persist()
        status = distance.map { "Measurement estimate: \($0) m; accuracy unverified" } ?? failure
    }
    var csv: String {
        "batch_id,test_id,device_model,ios_version,depth_received,confidence_end,confidence_start,tracking,mode,condition,device_time,distance_true_m,distance_measured_m,absolute_error_m,relative_error,elapsed_s,failure,distance_m\n" + rows.filter { $0.hasPrefix(quoted(batch) + ",") }.joined(separator: "\n")
    }
}

private struct LidarPreview: UIViewRepresentable {
    let model: LidarLabModel
    func makeUIView(context: Context) -> ARSCNView { model.view }
    func updateUIView(_ uiView: ARSCNView, context: Context) {}
}

struct LidarLabView: View {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var model = LidarLabModel()
    var body: some View {
        VStack {
            Text("Experimental measurement. Accuracy not verified.").font(.caption)
            ZStack {
                LidarPreview(model: model)
                Image(systemName: "plus").foregroundStyle(.yellow)
            }
            Picker("Mode", selection: $model.mode) {
                Text("Raycast").tag("raycast")
                Text("Scene Depth").tag("scene_depth")
                Text("Smoothed Depth").tag("smoothed_depth")
            }.onChange(of: model.mode) { model.stop() }
            TextField("Test ID", text: $model.testID)
            Picker("Export batch", selection: $model.batch) { ForEach(model.batches, id: \.self) { Text($0).tag($0) } }.onChange(of: model.batch) { model.stop() }
            Button("New batch") { model.newBatch() }
            Text(model.storageError).foregroundStyle(.red)
            Text(model.status).font(.caption)
            TextField("Reference distance (m)", text: $model.reference).keyboardType(.decimalPad)
            TextField("Device-to-target setup distance (m)", text: $model.targetDistance).keyboardType(.decimalPad)
            Picker("Condition", selection: $model.condition) {
                ForEach(["normal", "low_light", "glass", "oblique", "textureless", "reflective"], id: \.self) { Text($0) }
            }
            HStack {
                Button("Start") { model.start() }
                Button("Mark center") { model.sample() }
                ShareLink("Export CSV (\(model.rows.count))", item: model.csv)
            }
        }.padding().navigationTitle("LiDAR Lab").onDisappear { model.stop() }.onChange(of: scenePhase) { if scenePhase != .active { model.stop() } }
    }
}
#endif
