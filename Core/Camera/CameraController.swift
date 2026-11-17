@preconcurrency import AVFoundation
import Foundation

// All mutable capture state is confined to queue. The session is exposed only for preview.
final class CameraController: NSObject, @unchecked Sendable, AVCapturePhotoCaptureDelegate {
    let session = AVCaptureSession()
    private let queue = DispatchQueue(label: "com.workcam.camera")
    private let output = AVCapturePhotoOutput()
    private var configured = false
    private var active = false
    private var capturing = false
    private var observers: [NSObjectProtocol] = []
    var onStatus: (@Sendable (String, Bool) -> Void)?
    var onSaved: (@Sendable (String) -> Void)?

    override init() {
        super.init()
        for name in [AVCaptureSession.wasInterruptedNotification, AVCaptureSession.interruptionEndedNotification, AVCaptureSession.runtimeErrorNotification] {
            observers.append(NotificationCenter.default.addObserver(forName: name, object: session, queue: nil) { [weak self] note in
                let name = note.name
                self?.queue.async { [weak self] in
                    guard let self else { return }
                    if name == AVCaptureSession.interruptionEndedNotification {
                        self.startIfAuthorized()
                    } else {
                        self.onStatus?(name == AVCaptureSession.runtimeErrorNotification ? "Camera error — close and reopen camera" : "Camera interrupted", false)
                    }
                }
            })
        }
    }

    deinit { observers.forEach(NotificationCenter.default.removeObserver) }

    func setActive(_ active: Bool) {
        queue.async {
            self.active = active
            if active { self.startIfAuthorized() }
            else {
                self.session.stopRunning()
                self.onStatus?("Camera paused", false)
            }
        }
    }

    private func startIfAuthorized() {
        guard active else { return }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .notDetermined:
            onStatus?("Camera permission required", false)
            AVCaptureDevice.requestAccess(for: .video) { [weak self] _ in
                self?.queue.async { [weak self] in self?.startIfAuthorized() }
            }
        case .denied, .restricted:
            onStatus?("Camera access denied or restricted — check Settings", false)
        case .authorized:
            do {
                if !configured { try configure() }
                if !session.isRunning { session.startRunning() }
                onStatus?(session.isRunning ? "Camera ready" : "Camera unavailable", session.isRunning && !capturing && !session.isInterrupted)
            } catch { onStatus?(error.localizedDescription, false) }
        @unknown default: onStatus?("Camera authorization unavailable", false)
        }
    }

    private func configure() throws {
        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            throw NSError(domain: "WorkCam", code: 1, userInfo: [NSLocalizedDescriptionKey: "No rear camera available"])
        }
        let input = try AVCaptureDeviceInput(device: device)
        session.beginConfiguration()
        defer { session.commitConfiguration() }
        session.sessionPreset = .photo
        guard session.canAddInput(input) else { throw cameraError() }
        session.addInput(input)
        guard session.canAddOutput(output) else { session.removeInput(input); throw cameraError() }
        session.addOutput(output)
        configured = true
    }

    private func cameraError() -> NSError {
        NSError(domain: "WorkCam", code: 2, userInfo: [NSLocalizedDescriptionKey: "Camera configuration failed"])
    }

    func capture() {
        queue.async {
            guard self.active, self.session.isRunning, !self.session.isInterrupted, !self.capturing else { return }
            self.capturing = true
            self.onStatus?("Capturing…", false)
            let settings = AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
            // Task 002 uses a fixed portrait camera UI and matching photo orientation.
            if let connection = self.output.connection(with: .video), connection.isVideoRotationAngleSupported(90) {
                connection.videoRotationAngle = 90
            }
            self.output.capturePhoto(with: settings, delegate: self)
        }
    }

    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        let data = photo.fileDataRepresentation()
        let message = error?.localizedDescription
        queue.async {
            do {
                if let message { throw NSError(domain: "WorkCam", code: 3, userInfo: [NSLocalizedDescriptionKey: message]) }
                guard let data else { throw self.cameraError() }
                let url = try CaptureStore.save(data)
                self.onSaved?(url.lastPathComponent)
            } catch { self.onSaved?("Save failed: \(error.localizedDescription)") }
        }
    }

    func photoOutput(_ output: AVCapturePhotoOutput, didFinishCaptureFor resolvedSettings: AVCaptureResolvedPhotoSettings, error: Error?) {
        let message = error?.localizedDescription
        queue.async {
            self.capturing = false
            if let message { self.onStatus?("Capture failed: \(message)", self.active && self.session.isRunning) }
            else if self.active { self.startIfAuthorized() }
        }
    }
}
