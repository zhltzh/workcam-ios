@preconcurrency import AVFoundation
import Foundation

// All mutable capture state is confined to queue. The session is exposed only for preview.
final class CameraController: NSObject, @unchecked Sendable, AVCapturePhotoCaptureDelegate {
    let session = AVCaptureSession()
    private let queue = DispatchQueue(label: "com.workcam.camera")
    private let output = AVCapturePhotoOutput()
    private var input: AVCaptureDeviceInput?
    private var rearLenses: [AVCaptureDevice] = []
    private var configured = false
    private var active = false
    private var capturing = false
    private var requestingPermission = false
    private var observers: [NSObjectProtocol] = []
    var onStatus: (@Sendable (String, Bool) -> Void)?
    var onSaved: (@Sendable (URL?, String?) -> Void)?
    var onCapabilities: (@Sendable (Bool, Bool, String) -> Void)?

    private let savePhoto: @Sendable (Data) throws -> URL

    init(savePhoto: @escaping @Sendable (Data) throws -> URL = CaptureStore.save) {
        self.savePhoto = savePhoto
        super.init()
        for name in [AVCaptureSession.wasInterruptedNotification, AVCaptureSession.interruptionEndedNotification, AVCaptureSession.runtimeErrorNotification] {
            observers.append(NotificationCenter.default.addObserver(forName: name, object: session, queue: nil) { [weak self] note in
                let name = note.name
                let reset = (note.userInfo?[AVCaptureSessionErrorKey] as? AVError)?.code == .mediaServicesWereReset
                self?.queue.async { [weak self] in
                    guard let self else { return }
                    if name == AVCaptureSession.interruptionEndedNotification || reset {
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
            guard !requestingPermission else { return }
            requestingPermission = true
            AVCaptureDevice.requestAccess(for: .video) { [weak self] _ in
                self?.queue.async { [weak self] in
                    self?.requestingPermission = false
                    self?.startIfAuthorized()
                }
            }
        case .denied, .restricted:
            onStatus?("Camera access denied or restricted — check Settings", false)
        case .authorized:
            do {
                if !configured { try configure() }
                if !session.isRunning { session.startRunning() }
                publishCapabilities()
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
        rearLenses = [device] + [AVCaptureDevice.DeviceType.builtInUltraWideCamera, .builtInTelephotoCamera]
            .compactMap { AVCaptureDevice.default($0, for: .video, position: .back) }
        session.beginConfiguration()
        defer { session.commitConfiguration() }
        session.sessionPreset = .photo
        guard session.canAddInput(input) else { throw cameraError() }
        session.addInput(input)
        guard session.canAddOutput(output) else { session.removeInput(input); throw cameraError() }
        session.addOutput(output)
        self.input = input
        configured = true
    }

    private func cameraError() -> NSError {
        NSError(domain: "WorkCam", code: 2, userInfo: [NSLocalizedDescriptionKey: "Camera configuration failed"])
    }

    private func publishCapabilities() {
        guard let device = input?.device else { return }
        let lens: String
        switch device.deviceType {
        case .builtInUltraWideCamera: lens = "Ultra Wide"
        case .builtInTelephotoCamera: lens = "Telephoto"
        default: lens = "Wide"
        }
        onCapabilities?(rearLenses.count > 1, device.hasFlash, lens)
    }

    func switchLens() {
        queue.async {
            guard self.active, self.session.isRunning, !self.capturing,
                  self.rearLenses.count > 1, let current = self.input,
                  let index = self.rearLenses.firstIndex(where: { $0.uniqueID == current.device.uniqueID }) else { return }
            do {
                let next = try AVCaptureDeviceInput(device: self.rearLenses[(index + 1) % self.rearLenses.count])
                self.session.beginConfiguration()
                self.session.removeInput(current)
                let changed = self.session.canAddInput(next)
                if changed {
                    self.session.addInput(next)
                    self.input = next
                } else if self.session.canAddInput(current) {
                    self.session.addInput(current)
                } else {
                    self.configured = false
                    self.session.removeOutput(self.output)
                    self.input = nil
                }
                self.session.commitConfiguration()
                self.publishCapabilities()
                self.onStatus?(changed ? "Camera ready" : "Lens change unavailable",
                               self.configured && self.session.isRunning && !self.session.isInterrupted)
            } catch {
                self.onStatus?("Lens change failed", self.session.isRunning && !self.session.isInterrupted)
            }
        }
    }

    func focusAndExpose(at point: CGPoint) {
        queue.async {
            guard let device = self.input?.device, self.canAdjustCamera else { return }
            do {
                try device.lockForConfiguration()
                defer { device.unlockForConfiguration() }
                if device.isFocusPointOfInterestSupported, device.isFocusModeSupported(.autoFocus) {
                    device.focusPointOfInterest = point
                    device.focusMode = .autoFocus
                }
                if device.isExposurePointOfInterestSupported, device.isExposureModeSupported(.continuousAutoExposure) {
                    device.exposurePointOfInterest = point
                    device.exposureMode = .continuousAutoExposure
                }
            } catch {
                self.onStatus?("Focus and exposure adjustment unavailable", self.canAdjustCamera)
            }
        }
    }

    func adjustZoom(by scale: CGFloat) {
        guard scale.isFinite, scale > 0 else { return }
        queue.async {
            guard let device = self.input?.device, self.canAdjustCamera else { return }
            do {
                try device.lockForConfiguration()
                defer { device.unlockForConfiguration() }
                let requested = device.videoZoomFactor * scale
                device.videoZoomFactor = min(device.maxAvailableVideoZoomFactor,
                                             max(device.minAvailableVideoZoomFactor, requested))
            } catch {
                self.onStatus?("Zoom adjustment unavailable", self.canAdjustCamera)
            }
        }
    }

    private var canAdjustCamera: Bool {
        active && session.isRunning && !session.isInterrupted && !capturing
    }

    func capture(flash: Bool) {
        queue.async {
            guard self.active, self.session.isRunning, !self.session.isInterrupted, !self.capturing else { return }
            self.capturing = true
            self.onStatus?("Capturing…", false)
            let settings = AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
            if flash, self.input?.device.isFlashAvailable == true,
               self.output.supportedFlashModes.contains(.on) {
                settings.flashMode = .on
            }
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
                let url = try self.savePhoto(data)
                self.onSaved?(url, nil)
            } catch { self.onSaved?(nil, "Save failed: \(error.localizedDescription)") }
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
