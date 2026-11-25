import SwiftUI
import ImageIO
import QuickLook
import PDFKit
import AVFoundation

@MainActor
final class CameraModel: ObservableObject {
    let controller: CameraController
    @Published var status = "Camera not started"
    @Published var ready = false
    @Published var saved = "No photo saved this session"

    @Published var canSwitchLens = false
    @Published var hasFlash = false
    @Published var lens = "Wide"
    @Published var latest: UIImage?

    init(project: WorkProject?, didSave: @escaping @MainActor () -> Void) {
        controller = CameraController { data in
            if let project { return try ProjectStore.capture(data, in: project) }
            return try CaptureStore.save(data)
        }
        controller.onStatus = { [weak self] status, ready in
            Task { @MainActor [weak self] in self?.status = status; self?.ready = ready }
        }
        controller.onCapabilities = { [weak self] multiple, flash, lens in
            Task { @MainActor [weak self] in
                self?.canSwitchLens = multiple
                self?.hasFlash = flash
                self?.lens = lens
            }
        }
        controller.onSaved = { [weak self] url, error in
            Task { @MainActor [weak self] in
                guard let url else {
                    self?.saved = error ?? "Photo was not saved"
                    return
                }
                self?.saved = project == nil ? "Photo saved locally" : "Photo saved to project"
                self?.latest = Self.thumbnail(url.appendingPathComponent("photo.jpg"))
                didSave()
            }
        }
    }

    private static func thumbnail(_ url: URL) -> UIImage? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: 1000
              ] as CFDictionary) else { return nil }
        return UIImage(cgImage: image)
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

    @State private var showGrid = false
    @State private var flash = false
    @State private var reviewing = false

    var body: some View {
        VStack(spacing: 12) {
            CameraPreview(session: model.controller.session)
                .overlay {
                    if showGrid {
                        GeometryReader { proxy in
                            Path { path in
                                for index in 1...2 {
                                    let x = proxy.size.width * CGFloat(index) / 3
                                    let y = proxy.size.height * CGFloat(index) / 3
                                    path.move(to: CGPoint(x: x, y: 0))
                                    path.addLine(to: CGPoint(x: x, y: proxy.size.height))
                                    path.move(to: CGPoint(x: 0, y: y))
                                    path.addLine(to: CGPoint(x: proxy.size.width, y: y))
                                }
                            }.stroke(.white.opacity(0.7), lineWidth: 1)
                        }.allowsHitTesting(false)
                    }
                }
                .overlay(alignment: .topLeading) {
                    TimelineView(.periodic(from: .now, by: 60)) { context in
                        Text(context.date.formatted(date: .abbreviated, time: .shortened))
                            .font(.caption.monospacedDigit())
                            .padding(8)
                            .background(.black.opacity(0.8), in: RoundedRectangle(cornerRadius: 6))
                            .foregroundStyle(.white)
                            .padding(8)
                            .accessibilityLabel("Device time \(context.date.formatted())")
                    }
                }
                .background(.black)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .accessibilityLabel("Live rear camera preview")

            Text(model.status).accessibilityIdentifier("cameraStatus")
            Text(model.saved).font(.caption)
            Text("Location: Not Implemented · Local storage")
                .font(.caption).foregroundStyle(.secondary)

            HStack {
                Button(showGrid ? "Grid On" : "Grid Off", systemImage: "grid") { showGrid.toggle() }
                Button(flash && model.hasFlash ? "Flash On" : "Flash Off",
                       systemImage: flash && model.hasFlash ? "bolt.fill" : "bolt.slash") { flash.toggle() }
                    .disabled(!model.ready || !model.hasFlash)
                Button(model.lens, systemImage: "camera.aperture") { model.controller.switchLens() }
                    .disabled(!model.ready || !model.canSwitchLens)
            }.buttonStyle(.bordered).font(.caption)

            HStack(spacing: 28) {
                Button { reviewing = true } label: {
                    if let image = model.latest {
                        Image(uiImage: image).resizable().scaledToFill()
                            .frame(width: 44, height: 44).clipShape(RoundedRectangle(cornerRadius: 7))
                    } else {
                        Image(systemName: "photo").frame(width: 44, height: 44)
                    }
                }.disabled(model.latest == nil).accessibilityLabel("Review latest photo")

                Button { model.controller.capture(flash: flash) } label: {
                    Image(systemName: "camera.fill").font(.title)
                        .frame(width: 72, height: 62)
                }.buttonStyle(.borderedProminent)
                    .disabled(!model.ready).accessibilityLabel("Capture")

                #if DEBUG
                NavigationLink { LidarLabView() } label: {
                    Image(systemName: "ruler").frame(width: 44, height: 44)
                }.accessibilityLabel("Open experimental LiDAR Lab")
                #endif
            }
            if model.status.contains("denied") {
                Button("Open Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                }
            }
        }
        .padding().navigationTitle(project?.name ?? "Camera")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $reviewing, onDismiss: {
            model.controller.setActive(visible && scenePhase == .active)
        }) {
            NavigationStack {
                if let image = model.latest {
                    Image(uiImage: image).resizable().scaledToFit().padding()
                        .navigationTitle("Latest Photo")
                        .toolbar {
                            Button("Back to Camera") { reviewing = false }
                        }
                }
            }
            .onAppear { model.controller.setActive(false) }
        }
        .onAppear { visible = true; model.controller.setActive(scenePhase == .active) }
        .onDisappear { visible = false; model.controller.setActive(false) }
        .onChange(of: scenePhase) { _, phase in model.controller.setActive(visible && !reviewing && phase == .active) }
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
    @State private var search = ""
    @State private var showArchived = false

    private var filteredProjects: [WorkProject] {
        projects.filter {
            ($0.archived == true) == showArchived &&
            (search.isEmpty || $0.name.localizedCaseInsensitiveContains(search))
        }
    }
    @State private var creating = false
    @State private var name = ""
    @State private var error = ""

    var body: some View {
        List {
            if filteredProjects.isEmpty && error.isEmpty {
                ContentUnavailableView("No Projects", systemImage: "folder",
                                       description: Text("Create a project to organize site photos."))
            }
            ForEach(filteredProjects) { project in
                NavigationLink(project.name) { ProjectPhotosView(project: project, didRename: reload) }
                    .contextMenu {
                        Button(project.archived == true ? "Restore Project" : "Archive Project") {
                            do {
                                try ProjectStore.setArchived(project.archived != true, for: project)
                                reload()
                            } catch { self.error = error.localizedDescription }
                        }
                    }
            }
            if !error.isEmpty { Text(error).foregroundStyle(.red) }
        }
        .navigationTitle("Projects")
        .searchable(text: $search, prompt: "Search projects")
        .toolbar {
            Button(showArchived ? "Show Active" : "Show Archived", systemImage: "archivebox") {
                showArchived.toggle()
            }
            Button("New Project", systemImage: "plus") { name = ""; creating = true }
        }
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

private enum ReportPaper: String, CaseIterable, Sendable {
    case a4 = "A4", letter = "US Letter"

    var size: CGSize {
        switch self {
        case .a4: CGSize(width: 595, height: 842)
        case .letter: CGSize(width: 612, height: 792)
        }
    }
}

private enum ReportKind: String, CaseIterable, Sendable {
    case site = "Site Photo Report", inspection = "Inspection Report", evidence = "Evidence Report"
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
    @State private var preview: URL?
    @State private var paper = ReportPaper.a4
    @State private var reportKind = ReportKind.site

    var body: some View {
        List {
            Section {
                if project.archived != true {
                    NavigationLink {
                        CameraView(project: project, didSave: reload)
                    } label: {
                        Label("Take Photo", systemImage: "camera")
                    }
                }
                Picker("Report Type", selection: $reportKind) {
                    ForEach(ReportKind.allCases, id: \.self) { Text($0.rawValue) }
                }.disabled(exporting).onChange(of: reportKind) { report = nil }
                Picker("Report Paper", selection: $paper) {
                    ForEach(ReportPaper.allCases, id: \.self) { Text($0.rawValue) }
                }.disabled(exporting).onChange(of: paper) { report = nil }
                Button("Export PDF", systemImage: "doc") { export() }
                    .disabled(photos.isEmpty || exporting)
                if exporting { ProgressView() }
                if let report {
                    Button("Preview PDF", systemImage: "doc.text.magnifyingglass") { preview = report }
                    ShareLink("Share PDF", item: report)
                }
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
        .quickLookPreview($preview)
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
        let paper = paper
        let kind = reportKind
        exporting = true
        error = ""
        Task {
            defer { exporting = false }
            do { report = try await Task.detached(priority: .userInitiated) {
                try ProjectVisuals.report(project: project, photos: photos, paper: paper, kind: kind)
            }.value }
            catch { self.error = error.localizedDescription }
        }
    }
}

private enum WatermarkTemplate: String, CaseIterable {
    case minimal = "Minimal", construction = "Construction", inspection = "Inspection"
    case corporate = "Corporate", evidence = "Evidence"

    var fontScale: CGFloat {
        switch self {
        case .minimal: 0.018
        case .construction, .evidence: 0.022
        case .inspection: 0.020
        case .corporate: 0.024
        }
    }

    var opacity: CGFloat {
        switch self {
        case .minimal: 0.45
        case .construction: 0.60
        case .inspection: 0.55
        case .corporate: 0.65
        case .evidence: 0.70
        }
    }

    var defaultsLeft: Bool { self == .construction || self == .corporate }
}

private enum WatermarkPosition: String, CaseIterable {
    case template = "Template Default", topLeft = "Top Left", topRight = "Top Right"
    case bottomLeft = "Bottom Left", bottomRight = "Bottom Right"
}

private struct ProjectPhotoView: View {
    let photo: WorkPhoto
    let project: WorkProject
    @State private var image: UIImage?
    @State private var annotation: PhotoAnnotation?
    @State private var note = ""
    @State private var tool = AnnotationKind.marker
    @State private var draft: [AnnotationPoint] = []
    @State private var undo: [PhotoAnnotation?] = []
    @State private var redo: [PhotoAnnotation?] = []
    @State private var error = ""
    @State private var derivative: URL?
    @AppStorage("watermark.template") private var template = WatermarkTemplate.construction
    @AppStorage("watermark.position") private var position = WatermarkPosition.template
    @AppStorage("watermark.project") private var showProject = true
    @AppStorage("watermark.projectID") private var showProjectID = false
    @AppStorage("watermark.time") private var showTime = true
    @AppStorage("watermark.notes") private var showNotes = true
    @State private var evidenceStatus = "Checking local evidence…"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .overlay {
                            Canvas { context, size in
                                let bounds = CGRect(origin: .zero, size: size)
                                context.withCGContext { cg in
                                    UIGraphicsPushContext(cg)
                                    defer { UIGraphicsPopContext() }
                                    ProjectVisuals.drawMarker(makeAnnotation(draft) ?? annotation, in: bounds, diameter: 24)
                                }
                            }.allowsHitTesting(false)
                        }
                        .contentShape(Rectangle())
                        .gesture(DragGesture(minimumDistance: 0).onChanged { event in
                            guard let point = normalized(event.location) else { return }
                            if draft.isEmpty { draft = [point] }
                            if tool == .freehand {
                                if draft.count < 2048 { draft.append(point) }
                            } else {
                                draft = [draft[0], point]
                            }
                        }.onEnded { _ in
                            if let next = makeAnnotation(draft) {
                                undo.append(annotation)
                                redo.removeAll()
                                annotation = next
                                derivative = nil
                            }
                            draft.removeAll()
                        })
                        .onGeometrySizeChange { displayedSize = $0 }
                }
                Picker("Annotation Tool", selection: $tool) {
                    ForEach(AnnotationKind.allCases, id: \.self) { Text($0.rawValue) }
                }
                HStack {
                    Button("Undo") {
                        if let previous = undo.popLast() {
                            redo.append(annotation)
                            annotation = previous
                            derivative = nil
                        }
                    }.disabled(undo.isEmpty)
                    Button("Redo") {
                        if let next = redo.popLast() {
                            undo.append(annotation)
                            annotation = next
                            derivative = nil
                        }
                    }.disabled(redo.isEmpty)
                }.buttonStyle(.bordered)
                Text("Draw on the photo to place a human-added annotation.").font(.caption)
                TextField("Annotation text or note", text: $note)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: note) { derivative = nil }
                Button("Save Annotation") {
                    do { try saveAnnotation(); error = "" }
                    catch { self.error = error.localizedDescription }
                }.disabled(annotation == nil)
                Text(evidenceStatus).font(.caption).textSelection(.enabled)
                Text("Remote hardware origin: NOT_ATTESTED").font(.caption).foregroundStyle(.secondary)
                ShareLink("Share Original", item: photo.url)
                DisclosureGroup("Watermark Settings") {
                    Picker("Template", selection: $template) {
                        ForEach(WatermarkTemplate.allCases, id: \.self) { Text($0.rawValue) }
                    }
                    Picker("Position", selection: $position) {
                        ForEach(WatermarkPosition.allCases, id: \.self) { Text($0.rawValue) }
                    }
                    Toggle("Project Name", isOn: $showProject)
                    Toggle("Project ID", isOn: $showProjectID)
                    Toggle("Date & Time", isOn: $showTime)
                    Toggle("Notes", isOn: $showNotes)
                }
                Button("Generate Watermark Preview") {
                    do {
                        var fields = ["DERIVATIVE · UNVERIFIED"]
                        if showProject { fields.append(project.name) }
                        if showProjectID { fields.append("Project ID: \(project.id.uuidString)") }
                        if showTime { fields.append("\(photo.capturedAt.formatted()) [device time, unverified]") }
                        if showNotes, !note.isEmpty { fields.append("Notes: \(note)") }
                        derivative = try ProjectVisuals.watermark(photo: photo, annotation: annotation,
                                                                  template: template, position: position,
                                                                  label: fields.joined(separator: "\n"))
                        error = ""
                    } catch { self.error = error.localizedDescription }
                }.disabled(image == nil)
                if let derivative {
                    if let preview = UIImage(contentsOfFile: derivative.path) {
                        Image(uiImage: preview).resizable().scaledToFit()
                            .accessibilityLabel("Generated watermark preview")
                    }
                    ShareLink("Share Watermarked Copy", item: derivative)
                }
                if !error.isEmpty { Text(error).foregroundStyle(.red) }
            }.padding()
        }
        .navigationTitle("Project Photo")
        .task {
            image = ProjectVisuals.image(photo, maxPixels: 1600)
            do {
                annotation = try ProjectStore.annotation(for: photo)
                note = annotation?.note ?? ""
                tool = annotation?.kind ?? .marker
                if image == nil { throw ProjectVisuals.ExportError.unreadablePhoto }
            } catch { self.error = error.localizedDescription }
            if photo.localEntry.pathExtension == "evidence" {
                let directory = photo.url.deletingLastPathComponent()
                evidenceStatus = await Task.detached { EvidenceStore.inspect(directory) }.value
            } else {
                evidenceStatus = "Legacy original · No signed evidence record"
            }
        }
    }

    @State private var displayedSize: CGSize?

    private func normalized(_ point: CGPoint) -> AnnotationPoint? {
        guard let size = displayedSize, size.width > 0, size.height > 0 else { return nil }
        return AnnotationPoint(x: min(1, max(0, point.x / size.width)),
                               y: min(1, max(0, point.y / size.height)))
    }

    private func makeAnnotation(_ points: [AnnotationPoint]) -> PhotoAnnotation? {
        guard let first = points.first else { return nil }
        let anchor = tool == .marker || tool == .text ? (points.last ?? first) : first
        return PhotoAnnotation(x: anchor.x, y: anchor.y, note: note, kind: tool, points: points)
    }

    private func saveAnnotation() throws {
        guard var updated = annotation else { return }
        updated.note = note
        try ProjectStore.save(updated, for: photo)
        annotation = updated
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

    static func watermark(photo: WorkPhoto, annotation: PhotoAnnotation?, template: WatermarkTemplate,
                          position: WatermarkPosition, label: String) throws -> URL {
        guard let image = image(photo, maxPixels: 4096) else { throw ExportError.unreadablePhoto }
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let bounds = CGRect(origin: .zero, size: image.size)
        let rendered = UIGraphicsImageRenderer(size: image.size, format: format).jpegData(withCompressionQuality: 0.9) { context in
            image.draw(in: bounds)
            drawMarker(annotation, in: bounds, diameter: max(18, min(bounds.width, bounds.height) / 24))
            let base = min(bounds.width, bounds.height)
            let inset = min(max(8, floor(base * 0.015)), base / 8)
            let font = UIFont.systemFont(ofSize: max(10, floor(base * template.fontScale)), weight: .semibold)
            let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: UIColor.white]
            let maxWidth = max(1, floor(bounds.width * 0.62) - 2 * inset)
            let measured = (label as NSString).boundingRect(with: CGSize(width: maxWidth, height: .greatestFiniteMagnitude),
                                                            options: [.usesLineFragmentOrigin], attributes: attributes, context: nil)
            let height = min(ceil(measured.height), max(1, bounds.height * 0.4))
            let width = min(maxWidth, ceil(measured.width)) + 2 * inset
            let left = position == .topLeft || position == .bottomLeft ||
                (position == .template && template.defaultsLeft)
            let top = position == .topLeft || position == .topRight
            let panel = CGRect(x: left ? inset : bounds.width - inset - width,
                               y: top ? inset : bounds.height - height - 3 * inset,
                               width: width, height: height + 2 * inset)
            context.cgContext.setFillColor(UIColor.black.withAlphaComponent(max(0.60, template.opacity)).cgColor)
            context.cgContext.fill(panel)
            (label as NSString).draw(with: panel.insetBy(dx: inset, dy: inset), options: [.usesLineFragmentOrigin, .truncatesLastVisibleLine],
                                    attributes: attributes, context: nil)
        }
        let url = try exportURL(name: "Watermarked Photo.jpg")
        try rendered.write(to: url, options: [.atomic, .completeFileProtection])
        return url
    }

    static func report(project: WorkProject, photos: [WorkPhoto], paper: ReportPaper, kind: ReportKind) throws -> URL {
        guard photos.count <= 500 else { throw ExportError.photoLimit }
        let page = CGRect(origin: .zero, size: paper.size)
        let contentWidth = page.width - 72
        let url = try exportURL(name: "\(kind.rawValue).pdf")
        do {
            let annotations = try photos.map { try ProjectStore.annotation(for: $0) }
            let originalHashes = kind == .evidence ? try photos.map { evidenceHash(try Data(contentsOf: $0.url)) } : []
            var unreadablePhoto = false
            try UIGraphicsPDFRenderer(bounds: page).writePDF(to: url) { context in
                for (offset, photo) in photos.enumerated() {
                    autoreleasepool {
                        context.beginPage()
                        drawText(kind.rawValue, in: CGRect(x: 36, y: 32, width: contentWidth, height: 32), size: 23)
                        drawText(project.name, in: CGRect(x: 36, y: 70, width: contentWidth, height: 40), size: 16)
                        drawText(deviceTime(photo), in: CGRect(x: 36, y: 116, width: contentWidth, height: 24), size: 11)
                        if let image = image(photo, maxPixels: 1800) {
                            let frame = CGRect(x: 36, y: 146, width: contentWidth, height: page.height - 322)
                            let rect = AVMakeRect(aspectRatio: image.size, insideRect: frame)
                            image.draw(in: rect)
                            drawMarker(annotations[offset], in: rect, diameter: 18)
                        } else {
                            unreadablePhoto = true
                        }
                        if let annotation = annotations[offset] {
                            drawText("Human-added note: \(annotation.note)", in: CGRect(x: 36, y: page.height - 164, width: contentWidth, height: 92), size: 12)
                        }
                        if kind == .evidence {
                            drawText("Original SHA-256: " + originalHashes[offset],
                                     in: CGRect(x: 36, y: page.height - 76, width: contentWidth, height: 22), size: 9)
                        }
                        drawText("UNVERIFIED · Reference only · \(offset + 1) / \(photos.count)",
                                 in: CGRect(x: 36, y: page.height - 54, width: contentWidth, height: 24), size: 10)
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

    static func drawMarker(_ annotation: PhotoAnnotation?, in rect: CGRect, diameter: CGFloat) {
        guard let annotation else { return }
        let kind = annotation.kind ?? .marker
        func mapped(_ point: AnnotationPoint) -> CGPoint {
            CGPoint(x: rect.minX + rect.width * point.x, y: rect.minY + rect.height * point.y)
        }
        let start = mapped(AnnotationPoint(x: annotation.x, y: annotation.y))
        if kind == .text {
            let width = min(rect.width * 0.7, max(1, rect.maxX - start.x))
            drawText(annotation.note, in: CGRect(x: start.x, y: start.y, width: width,
                                                 height: max(1, rect.maxY - start.y)), size: diameter * 0.8, color: .systemRed)
            return
        }
        if kind != .marker, let endpoint = annotation.points?.last {
            let end = mapped(endpoint)
            let path = UIBezierPath()
            let frame = CGRect(x: min(start.x, end.x), y: min(start.y, end.y),
                               width: abs(end.x - start.x), height: abs(end.y - start.y))
            switch kind {
            case .rectangle: path.append(UIBezierPath(rect: frame))
            case .ellipse: path.append(UIBezierPath(ovalIn: frame))
            case .freehand:
                path.move(to: start)
                for point in annotation.points ?? [] { path.addLine(to: mapped(point)) }
            case .arrow:
                path.move(to: start)
                path.addLine(to: end)
                let angle = atan2(end.y - start.y, end.x - start.x)
                for turn in [-CGFloat.pi / 6, CGFloat.pi / 6] {
                    path.move(to: end)
                    path.addLine(to: CGPoint(x: end.x - diameter * cos(angle + turn),
                                              y: end.y - diameter * sin(angle + turn)))
                }
            default: break
            }
            UIColor.systemRed.setStroke()
            path.lineWidth = max(2, diameter * 0.15)
            path.lineCapStyle = .round
            path.stroke()
            return
        }
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

#if DEBUG && targetEnvironment(simulator)
enum Night04Fixture {
    static let root: URL? = ProcessInfo.processInfo.arguments.contains("--night04-fixture")
        ? FileManager.default.temporaryDirectory.appendingPathComponent("Night04-" + UUID().uuidString)
        : nil

    @MainActor
    static func run() -> String {
        guard let root else { return "Synthetic workflow disabled" }
        let result: String
        do {
            let project = try ProjectStore.create("Synthetic Test Site")
            let source = UIGraphicsImageRenderer(size: CGSize(width: 640, height: 480))
                .jpegData(withCompressionQuality: 0.9) { context in
                    UIColor.systemBlue.setFill()
                    context.fill(CGRect(x: 0, y: 0, width: 640, height: 480))
                }
            _ = try ProjectStore.capture(source, in: project)
            guard let photo = try ProjectStore.photos(in: project).first else { throw EvidenceError.invalid }
            let mark = PhotoAnnotation(x: 0.2, y: 0.2, note: "Synthetic fixture", kind: .arrow,
                                       points: [.init(x: 0.2, y: 0.2), .init(x: 0.7, y: 0.6)])
            try ProjectStore.save(mark, for: photo)
            guard try ProjectStore.annotation(for: photo)?.kind == .arrow else { throw EvidenceError.invalid }
            let watermark = try ProjectVisuals.watermark(photo: photo, annotation: mark, template: .evidence,
                                                         position: .bottomRight, label: "SYNTHETIC · UNVERIFIED")
            guard UIImage(contentsOfFile: watermark.path) != nil else { throw EvidenceError.invalid }
            try FileManager.default.copyItem(at: watermark, to: root.appendingPathComponent("Synthetic Watermark.jpg"))
            for kind in ReportKind.allCases {
                for paper in ReportPaper.allCases {
                    let pdf = try ProjectVisuals.report(project: project, photos: [photo], paper: paper, kind: kind)
                    guard let document = PDFDocument(url: pdf), document.pageCount == 1,
                          document.page(at: 0)?.bounds(for: .mediaBox).size == paper.size else {
                        throw EvidenceError.invalid
                    }
                    try FileManager.default.copyItem(at: pdf,
                        to: root.appendingPathComponent("Synthetic \(kind.rawValue) \(paper.rawValue).pdf"))
                }
            }
            let evidence = try EvidenceStore.exportPackages()
            let packages = try FileManager.default.contentsOfDirectory(at: evidence, includingPropertiesForKeys: nil)
            guard packages.count == 1, let package = packages.first,
                  try Data(contentsOf: package.appendingPathComponent("photo.jpg")) == source,
                  try Data(contentsOf: photo.url) == source,
                  try ProjectStore.projects().contains(where: { $0.id == project.id }) else {
                throw EvidenceError.invalid
            }
            result = "PASS · Synthetic project → save → annotation → watermark → 6 PDFs → evidence export"
        } catch {
            result = "FAIL · Synthetic workflow: \(error)"
        }
        try? Data(result.utf8).write(to: root.appendingPathComponent("result.txt"), options: .atomic)
        return result
    }
}
#endif
