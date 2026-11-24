import SwiftUI
import ImageIO
import AVFoundation

@MainActor
final class CameraModel: ObservableObject {
    let controller: CameraController
    @Published var status = "Camera not started"
    @Published var ready = false
    @Published var saved = "No photo saved this session"

    init(project: WorkProject?, didSave: @escaping @MainActor () -> Void) {
        controller = CameraController { data in
            if let project { return try ProjectStore.capture(data, in: project) }
            return try CaptureStore.save(data)
        }
        controller.onStatus = { [weak self] status, ready in
            Task { @MainActor [weak self] in self?.status = status; self?.ready = ready }
        }
        controller.onSaved = { [weak self] name in
            Task { @MainActor [weak self] in
                if name.hasPrefix("Save failed:") {
                    self?.saved = name
                } else {
                    self?.saved = project == nil ? "Saved locally: \(name)" : "Photo saved to project"
                    didSave()
                }
            }
        }
    }
}

struct CameraView: View {
    @StateObject private var model: CameraModel
    private let project: WorkProject?

    init(project: WorkProject? = nil, didSave: @escaping @MainActor () -> Void = {}) {
        self.project = project
        _model = StateObject(wrappedValue: CameraModel(project: project, didSave: didSave))
    }
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
        .padding().navigationTitle(project?.name ?? "Camera")
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

struct ProjectsView: View {
    @State private var projects: [WorkProject] = []
    @State private var creating = false
    @State private var name = ""
    @State private var error = ""

    var body: some View {
        List {
            if projects.isEmpty && error.isEmpty {
                ContentUnavailableView("No Projects", systemImage: "folder",
                                       description: Text("Create a project to organize site photos."))
            }
            ForEach(projects) { project in
                NavigationLink(project.name) { ProjectPhotosView(project: project, didRename: reload) }
            }
            if !error.isEmpty { Text(error).foregroundStyle(.red) }
        }
        .navigationTitle("Projects")
        .toolbar { Button("New Project", systemImage: "plus") { name = ""; creating = true } }
        .alert("New Project", isPresented: $creating) {
            TextField("Project name", text: $name)
            Button("Create") {
                let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !trimmed.isEmpty else { return }
                do { _ = try ProjectStore.create(trimmed); reload() }
                catch { self.error = error.localizedDescription }
            }
            Button("Cancel", role: .cancel) {}
        }
        .onAppear(perform: reload)
    }

    private func reload() {
        do { projects = try ProjectStore.projects(); error = "" }
        catch { self.error = error.localizedDescription }
    }
}

private struct ProjectPhotosView: View {
    @State var project: WorkProject
    let didRename: () -> Void
    @State private var photos: [WorkPhoto] = []
    @State private var error = ""
    @State private var name = ""
    @State private var renaming = false
    @State private var exporting = false
    @State private var report: URL?

    var body: some View {
        List {
            Section {
                NavigationLink {
                    CameraView(project: project, didSave: reload)
                } label: {
                    Label("Take Photo", systemImage: "camera")
                }
                Button("Export PDF", systemImage: "doc") { export() }
                    .disabled(photos.isEmpty || exporting)
                if exporting { ProgressView() }
                if let report { ShareLink("Share PDF", item: report) }
            }
            Section("Photos") {
                if photos.isEmpty { Text("No photos yet").foregroundStyle(.secondary) }
                ForEach(photos) { photo in
                    NavigationLink {
                        ProjectPhotoView(photo: photo, project: project)
                    } label: {
                        HStack {
                            if let image = ProjectVisuals.image(photo, maxPixels: 120) {
                                Image(uiImage: image).resizable().scaledToFill()
                                    .frame(width: 54, height: 54).clipped().cornerRadius(6)
                            }
                            Text(photo.capturedAt.formatted(date: .abbreviated, time: .standard))
                        }
                    }
                }
            }
            if !error.isEmpty { Text(error).foregroundStyle(.red) }
        }
        .navigationTitle(project.name)
        .toolbar { Button("Rename") { name = project.name; renaming = true } }
        .alert("Rename Project", isPresented: $renaming) {
            TextField("Project name", text: $name)
            Button("Save") {
                let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !trimmed.isEmpty else { return }
                do {
                    project = try ProjectStore.rename(project, to: trimmed)
                    didRename()
                    reload()
                } catch { self.error = error.localizedDescription }
            }
            Button("Cancel", role: .cancel) {}
        }
        .onAppear(perform: reload)
    }

    private func reload() {
        report = nil
        do { photos = try ProjectStore.photos(in: project); error = "" }
        catch { self.error = error.localizedDescription }
    }

    private func export() {
        let project = project
        let photos = photos
        exporting = true
        error = ""
        Task {
            defer { exporting = false }
            do { report = try await Task.detached(priority: .userInitiated) {
                try ProjectVisuals.report(project: project, photos: photos)
            }.value }
            catch { self.error = error.localizedDescription }
        }
    }
}

private struct ProjectPhotoView: View {
    let photo: WorkPhoto
    let project: WorkProject
    @State private var image: UIImage?
    @State private var annotation: PhotoAnnotation?
    @State private var note = ""
    @State private var error = ""
    @State private var derivative: URL?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .overlay {
                            GeometryReader { geometry in
                                if let annotation {
                                    Image(systemName: "1.circle.fill")
                                        .font(.title2)
                                        .foregroundStyle(.white, .red)
                                        .position(x: geometry.size.width * annotation.x,
                                                  y: geometry.size.height * annotation.y)
                                        .allowsHitTesting(false)
                                }
                            }
                        }
                        .contentShape(Rectangle())
                        .gesture(SpatialTapGesture().onEnded { tap in
                            let point = tap.location
                            if let size = displayedSize, size.width > 0, size.height > 0 {
                                annotation = PhotoAnnotation(x: min(1, max(0, point.x / size.width)),
                                                             y: min(1, max(0, point.y / size.height)), note: note)
                                derivative = nil
                            }
                        })
                        .onGeometrySizeChange { displayedSize = $0 }
                }
                Text("Tap the photo to place or move a human-added marker.")
                    .font(.caption)
                TextField("Marker note", text: $note)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: note) { derivative = nil }
                Button("Save Annotation") {
                    do { try saveAnnotation(); error = "" }
                    catch { self.error = error.localizedDescription }
                }.disabled(annotation == nil)
                ShareLink("Share Original", item: photo.url)
                Button("Create Watermarked JPEG") {
                    do {
                        if annotation != nil { try saveAnnotation() }
                        derivative = try ProjectVisuals.watermark(project: project, photo: photo)
                        error = ""
                    } catch { self.error = error.localizedDescription }
                }.disabled(image == nil)
                if let derivative { ShareLink("Share Watermarked Copy", item: derivative) }
                if !error.isEmpty { Text(error).foregroundStyle(.red) }
            }.padding()
        }
        .navigationTitle("Project Photo")
        .task {
            image = ProjectVisuals.image(photo, maxPixels: 1600)
            do {
                annotation = try ProjectStore.annotation(for: photo)
                note = annotation?.note ?? ""
                if image == nil { throw ProjectVisuals.ExportError.unreadablePhoto }
            } catch { self.error = error.localizedDescription }
        }
    }

    @State private var displayedSize: CGSize?

    private func saveAnnotation() throws {
        guard let annotation else { return }
        let updated = PhotoAnnotation(x: annotation.x, y: annotation.y, note: note)
        try ProjectStore.save(updated, for: photo)
        self.annotation = updated
    }
}

private extension View {
    func onGeometrySizeChange(_ update: @escaping (CGSize) -> Void) -> some View {
        background {
            GeometryReader { geometry in
                Color.clear
                    .onAppear { update(geometry.size) }
                    .onChange(of: geometry.size) { _, size in update(size) }
            }
        }
    }
}

private enum ProjectVisuals {
    enum ExportError: LocalizedError {
        case unreadablePhoto, encodingFailed, photoLimit
        var errorDescription: String? {
            switch self {
            case .unreadablePhoto: "A project photo could not be read."
            case .encodingFailed: "The watermarked copy could not be encoded."
            case .photoLimit: "Export supports up to 500 photos per project."
            }
        }
    }

    static func image(_ photo: WorkPhoto, maxPixels: Int) -> UIImage? {
        guard let source = CGImageSourceCreateWithURL(photo.url as CFURL, nil),
              let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: maxPixels
              ] as CFDictionary) else { return nil }
        return UIImage(cgImage: thumbnail)
    }

    static func watermark(project: WorkProject, photo: WorkPhoto) throws -> URL {
        guard let image = image(photo, maxPixels: 4096) else { throw ExportError.unreadablePhoto }
        let annotation = try ProjectStore.annotation(for: photo)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let bounds = CGRect(origin: .zero, size: image.size)
        let rendered = UIGraphicsImageRenderer(size: image.size, format: format).jpegData(withCompressionQuality: 0.9) { context in
            image.draw(in: bounds)
            drawMarker(annotation, in: bounds, diameter: max(18, min(bounds.width, bounds.height) / 24))
            let scale = min(bounds.width, bounds.height) / 600
            let inset = 12 * scale
            let font = UIFont.systemFont(ofSize: 16 * scale, weight: .semibold)
            let label = "\(project.name)\n\(deviceTime(photo))\nDERIVATIVE · UNVERIFIED"
            let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: UIColor.white]
            let maxWidth = bounds.width - 4 * inset
            let measured = (label as NSString).boundingRect(with: CGSize(width: maxWidth, height: bounds.height / 3),
                                                            options: [.usesLineFragmentOrigin], attributes: attributes, context: nil)
            let height = min(ceil(measured.height), bounds.height / 3)
            let panel = CGRect(x: inset, y: bounds.height - height - 3 * inset,
                               width: min(maxWidth, ceil(measured.width)) + 2 * inset, height: height + 2 * inset)
            context.cgContext.setFillColor(UIColor.black.withAlphaComponent(0.8).cgColor)
            context.cgContext.fill(panel)
            (label as NSString).draw(with: panel.insetBy(dx: inset, dy: inset), options: [.usesLineFragmentOrigin, .truncatesLastVisibleLine],
                                    attributes: attributes, context: nil)
        }
        let url = try exportURL(name: "Watermarked Photo.jpg")
        try rendered.write(to: url, options: [.atomic, .completeFileProtection])
        return url
    }

    static func report(project: WorkProject, photos: [WorkPhoto]) throws -> URL {
        guard photos.count <= 500 else { throw ExportError.photoLimit }
        let page = CGRect(x: 0, y: 0, width: 595, height: 842)
        let url = try exportURL(name: "Site Photo Report.pdf")
        do {
            let annotations = try photos.map { try ProjectStore.annotation(for: $0) }
            var unreadablePhoto = false
            try UIGraphicsPDFRenderer(bounds: page).writePDF(to: url) { context in
                for (offset, photo) in photos.enumerated() {
                    autoreleasepool {
                        context.beginPage()
                        drawText("Site Photo Report", in: CGRect(x: 36, y: 32, width: 523, height: 32), size: 23)
                        drawText(project.name, in: CGRect(x: 36, y: 70, width: 523, height: 40), size: 16)
                        drawText(deviceTime(photo), in: CGRect(x: 36, y: 116, width: 523, height: 24), size: 11)
                        if let image = image(photo, maxPixels: 1800) {
                            let frame = CGRect(x: 36, y: 146, width: 523, height: 520)
                            let rect = AVMakeRect(aspectRatio: image.size, insideRect: frame)
                            image.draw(in: rect)
                            drawMarker(annotations[offset], in: rect, diameter: 18)
                        } else {
                            unreadablePhoto = true
                        }
                        if let annotation = annotations[offset] {
                            drawText("Human-added note: \(annotation.note)", in: CGRect(x: 36, y: 678, width: 523, height: 92), size: 12)
                        }
                        drawText("UNVERIFIED · Reference only · \(offset + 1) / \(photos.count)",
                                 in: CGRect(x: 36, y: 788, width: 523, height: 24), size: 10)
                    }
                }
            }
            if unreadablePhoto { throw ExportError.unreadablePhoto }
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: url.path)
            return url
        } catch {
            try? FileManager.default.removeItem(at: url.deletingLastPathComponent())
            throw error
        }
    }

    private static func exportURL(name: String) throws -> URL {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false,
                                                attributes: [.protectionKey: FileProtectionType.complete])
        return folder.appendingPathComponent(name)
    }

    private static func deviceTime(_ photo: WorkPhoto) -> String {
        "\(photo.capturedAt.formatted()) [device time, unverified]"
    }

    private static func drawText(_ text: String, in rect: CGRect, size: CGFloat, color: UIColor = .black) {
        (text as NSString).draw(with: rect, options: [.usesLineFragmentOrigin, .truncatesLastVisibleLine], attributes: [
            .font: UIFont.systemFont(ofSize: size), .foregroundColor: color
        ], context: nil)
    }

    private static func drawMarker(_ annotation: PhotoAnnotation?, in rect: CGRect, diameter: CGFloat) {
        guard let annotation else { return }
        let x = min(rect.maxX - diameter, max(rect.minX, rect.minX + rect.width * annotation.x - diameter / 2))
        let y = min(rect.maxY - diameter, max(rect.minY, rect.minY + rect.height * annotation.y - diameter / 2))
        let marker = CGRect(x: x, y: y, width: diameter, height: diameter)
        UIColor.systemRed.setFill()
        UIBezierPath(ovalIn: marker).fill()
        let font = UIFont.boldSystemFont(ofSize: diameter * 0.65)
        let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: UIColor.white]
        let size = ("1" as NSString).size(withAttributes: attributes)
        ("1" as NSString).draw(at: CGPoint(x: marker.midX - size.width / 2, y: marker.midY - size.height / 2),
                               withAttributes: attributes)
    }
}
