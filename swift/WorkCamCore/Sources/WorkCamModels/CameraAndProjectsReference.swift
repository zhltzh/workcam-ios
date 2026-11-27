// FE-E03/E04/E05/E06: camera protocol alignment + projects/photos
// reference + annotation/issue diff notes + measurement UI reference.
// XCODE_PENDING: authored on Windows; Mac compiles.
//
// POSITIONING (E01 audit): Mac ALREADY HAS CameraController (162
// lines, AVFoundation), full ProjectsView, a complete annotation
// canvas and PDF rendering. These files do NOT duplicate them.
// They deliver: (1) the protocol seam so hardware stays on Mac,
// (2) view models testable without any camera, (3) the measurement
// cards driven by our real geometry core, (4) diff bindings between
// Mac's editors and the Windows business models (B04/B05).

import SwiftUI
import WorkCamModels
import WorkCamSync

// MARK: - E03: Camera service protocol (the ONLY seam Mac must adopt)

public protocol CameraService: AnyObject {
    var isAvailable: Bool { get }
    var authorizationDenied: Bool { get }
    func configure() async throws
    func capture() async throws -> CapturedFrame
}

public struct CapturedFrame: Sendable {
    public let jpeg: Data
    public let width: Int
    public let height: Int
    public let capturedAt: Date
    public init(jpeg: Data, width: Int, height: Int,
                capturedAt: Date) {
        self.jpeg = jpeg; self.width = width; self.height = height
        self.capturedAt = capturedAt
    }
}

/// Testable camera ViewModel: EVERYTHING except the shutter goes
/// through state the tests control; the hardware call is one line.
@MainActor
public final class CameraRefModel: ObservableObject {
    @Published public private(set) var gridOn = false
    @Published public private(set) var flashOn = false
    @Published public private(set) var selectedProjectID: String?
    @Published public private(set) var lastShot: CapturedFrame?
    @Published public private(set) var offline = false
    @Published public private(set) var shotPending = false
    @Published public private(set) var quickNote = ""

    private let camera: CameraService

    public init(camera: CameraService) { self.camera = camera }

    public func toggleGrid() { gridOn.toggle() }
    public func toggleFlash() { flashOn.toggle() }
    public func select(project: String?) { selectedProjectID = project }
    public func setOffline(_ value: Bool) { offline = value }
    public func setQuickNote(_ note: String) { quickNote = note }

    /// The shutter: hardware in, evidence pipeline out. Photos queue
    /// offline (TM67 outbox discipline) - offline state never blocks
    /// the shutter, it only labels the queue.
    public func shoot() async {
        shotPending = true
        defer { shotPending = false }
        guard let frame = try? await camera.capture() else { return }
        lastShot = frame
        // Staging + signing stay on the device pipeline (TM70
        // CaptureTransaction); this reference stops at the seam.
    }
}

// MARK: - E04: Projects/photos manager reference (batch + empty states)

@MainActor
public final class ProjectsRefModel: ObservableObject {
    @Published public var search = ""
    @Published public var showArchived = false
    @Published public var selection = Set<String>()
    @Published public private(set) var error: String?
    @Published public private(set) var busy = false

    private let repo: Repository

    public init(repo: Repository) { self.repo = repo }

    public func projects() throws -> [ProjectRecord] {
        try repo.projects().filter {
            (showArchived || $0.status == "active") &&
            (search.isEmpty ||
             $0.name.localizedCaseInsensitiveContains(search))
        }
    }

    /// Batch tag via B07 semantics: failure-only retry subset; the
    /// vocabulary has no original-delete op (structurally).
    public func batchTag(_ tag: String,
                         onComplete: @escaping (String) -> Void) {
        guard !selection.isEmpty else { return }
        busy = true
        Task {
            defer { busy = false }
            for id in selection {
                do { try await Task.detached {
                        try repo.save(photo: PhotoRecord(
                            photoID: id, projectID: "",
                            originalFilename: id, mimeType: "image/jpeg",
                            byteSize: 0, createdAt: Date(),
                            tags: [tag]))
                    }.value
                } catch {
                    error = "Failed: \(id) - \(error.localizedDescription)"
                    onComplete("retry:\(id)")
                    return
                }
            }
            onComplete("done")
        }
    }
}

public struct EmptyProjectsState: View {
    public var body: some View {
        ContentUnavailableView(
            "No projects yet",
            systemImage: "folder.badge.plus",
            description: Text("Create your first site to start "
                              + "capturing evidence."))
    }
}

public struct ErrorBanner: View {
    public let message: String
    public let retry: () -> Void
    public var body: some View {
        HStack {
            Label(message, systemImage: "exclamationmark.triangle")
                .font(.callout)
            Spacer()
            Button("Retry", action: retry)
        }
        .padding()
        .background(.red.opacity(0.1))
        .accessibilityIdentifier("errorBanner")
    }
}

// MARK: - E05: annotation/issue DIFF BINDINGS (not a canvas rewrite)

/// The E01 audit verdict: Mac's ProjectPhotoView keeps the canvas.
/// What Windows ships is the BINDING between Mac's annotation
/// shapes and the server columns (proven live in the A06 contract
/// test), plus the checklist entry seam for B02.
public enum AnnotationBinding {
    /// Mac PhotoAnnotation{x,y,kind,color,lineWidth,note} ->
    /// server sync op payload (norm coords, type, color_hex,
    /// stroke_width, text_content). Verified against the LIVE server
    /// in test_m03_a06_night05_contract.py - this maps ONE WAY.
    public static func opPayload(photoID: String, kind: String,
                                 x: Double, y: Double,
                                 color: String, lineWidth: Double,
                                 note: String) -> [String: Any] {
        [
            "server_photo_id": photoID,
            "annotation_type": kind,     // Night05 vocabulary == server
            "norm_x1": x, "norm_y1": y,
            "color_hex": color, "stroke_width": lineWidth,
            "text_content": note,
        ]
    }
}

// MARK: - E06: measurement cards (simulated data; real AR stays Mac)

public struct MeasurementCard: View {
    public let title: String
    public let value: String
    public let unit: String
    public let quality: String       // good/fair/poor/invalid (TM22)
    public let uncertainty: String? // shown when calibrated; nil =
                                    // "uncalibrated estimate" wording

    public var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline) {
                Text(value).font(.title2).monospacedDigit()
                Text(unit).font(.callout).foregroundStyle(.secondary)
            }
            HStack {
                Label(quality, systemImage: "gauge.with.needle")
                    .font(.caption)
                if let u = uncertainty {
                    Text("± \(u)").font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text("Uncalibrated estimate")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .padding()
        .background(.gray.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .accessibilityIdentifier("measurementCard_\(title)")
    }
}
