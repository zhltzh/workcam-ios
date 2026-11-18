import SwiftUI
import AVFoundation

@MainActor
final class CameraModel: ObservableObject {
    let controller = CameraController()
    @Published var status = "Camera not started"
    @Published var ready = false
    @Published var saved = "No photo saved this session"

    init() {
        controller.onStatus = { [weak self] status, ready in
            Task { @MainActor [weak self] in self?.status = status; self?.ready = ready }
        }
        controller.onSaved = { [weak self] name in
            Task { @MainActor [weak self] in self?.saved = name.hasPrefix("Save failed:") ? name : "Saved locally: \(name)" }
        }
    }
}

struct CameraView: View {
    @StateObject private var model = CameraModel()
    @Environment(\.scenePhase) private var scenePhase
    @State private var visible = false

    var body: some View {
        VStack(spacing: 16) {
            CameraPreview(session: model.controller.session)
                .overlay(alignment: .topLeading) { Text("Unsigned capture").padding(8).background(.black.opacity(0.7)).foregroundStyle(.white) }
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .accessibilityLabel("Live rear camera preview")
            Text(model.status).accessibilityIdentifier("cameraStatus")
            Button("Capture") { model.controller.capture() }
                .buttonStyle(.borderedProminent).disabled(!model.ready)
            Text(model.saved).font(.caption).textSelection(.enabled)
            Text("Saved only inside this app. Signing: Not Implemented.").font(.caption)
            Button("Open Settings") {
                if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
            }
        }
        .padding().navigationTitle("Camera")
        .onAppear { visible = true; model.controller.setActive(scenePhase == .active) }
        .onDisappear { visible = false; model.controller.setActive(false) }
        .onChange(of: scenePhase) { _, phase in model.controller.setActive(visible && phase == .active) }
    }
}

private struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.layerView.session = session
        view.layerView.videoGravity = .resizeAspectFill
        return view
    }
    func updateUIView(_ view: PreviewView, context: Context) {}
}

private final class PreviewView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
    var layerView: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    override func layoutSubviews() {
        super.layoutSubviews()
        if let connection = layerView.connection, connection.isVideoRotationAngleSupported(90) {
            connection.videoRotationAngle = 90
        }
    }
}
