import SwiftUI

struct ContentView: View {
    @State private var exporting = false
    @State private var exportMessage = ""
    @State private var showExportMessage = false
    @State private var exportURL: URL?
    @State private var evidenceReport = "Local verification pending"
    #if DEBUG && targetEnvironment(simulator)
    @State private var fixtureResult: String?
    @State private var networkFixtureResult: String?
    #endif
    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 24) {
                Text("Trusted Capture Prototype").font(.title2)
                #if DEBUG && targetEnvironment(simulator)
                if let fixtureResult { Text(fixtureResult).font(.caption) }
                if let networkFixtureResult { Text(networkFixtureResult).font(.caption) }
                #endif
                VStack(alignment: .leading) {
                    Text("Camera Status").font(.headline)
                    Text("Open camera to check availability").foregroundStyle(.secondary)
                }
                status("Signing Status")
                NavigationLink("Backend Sync") { BackendSyncView() }
                #if DEBUG
                NavigationLink("LiDAR Lab") { LidarLabView() }
                #endif
                NavigationLink("Projects") { ProjectsView() }
                NavigationLink("Open Camera") { CameraView() }
                NavigationLink("Evidence") {
                    ScrollView {
                        VStack {
                            Button("Reverify Saved Evidence") {
                                Task { evidenceReport = await Task.detached { EvidenceStore.report() }.value }
                            }
                            #if DEBUG
                            Button("Test Tamper Detection on Copies") {
                                Task { evidenceReport = await Task.detached { EvidenceStore.tamperReport() }.value }
                            }
                            #endif
                            Button(exporting ? "Preparing Export…" : "Prepare Evidence Export") {
                                exporting = true
                                exportURL = nil
                                Task {
                                    do {
                                        let url = try await Task.detached { try EvidenceStore.exportPackages() }.value
                                        exportURL = url
                                        exportMessage = "Export ready. Use Share Public Evidence Folder below."
                                    } catch {
                                        exportMessage = "Export failed: \(error.localizedDescription) (\(error)). Keep the iPhone unlocked and retry."
                                    }
                                    exporting = false
                                    showExportMessage = true
                                }
                            }.disabled(exporting)
                            if exporting { ProgressView("Preparing public evidence…") }
                            Text(exportMessage).font(.caption).textSelection(.enabled)
                            if let exportURL { ShareLink("Share Public Evidence Folder", item: exportURL) }
                            Text(evidenceReport).font(.caption).textSelection(.enabled)
                        }.padding()
                    }
                        .task { evidenceReport = await Task.detached { EvidenceStore.report() }.value }
                }
                Spacer()
            }
            .padding()
            .navigationTitle("WorkCam")
            #if DEBUG && targetEnvironment(simulator)
            .task {
                if networkFixtureResult == nil, ProcessInfo.processInfo.arguments.contains("--int02-live") {
                    do {
                        try await SwiftNetworkFixture.run()
                        networkFixtureResult = "PASS · Swift live HTTP"
                    } catch {
                        networkFixtureResult = "FAIL · Swift live HTTP · \(error.localizedDescription)"
                    }
                }
                if fixtureResult == nil, Night04Fixture.root != nil {
                    fixtureResult = Night04Fixture.run()
                }
            }
            #endif
            .alert("Evidence Export", isPresented: $showExportMessage) {
                Button("OK", role: .cancel) { }
            } message: { Text(exportMessage) }
        }
    }

    private func status(_ title: String) -> some View {
        VStack(alignment: .leading) {
            Text(title).font(.headline)
            Text("Not Implemented").foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct BackendSyncView: View {
    @State private var endpoint = ""
    @State private var code = ""
    @State private var projects: [WorkProject] = []
    @State private var projectID: UUID?
    @State private var task: Task<Void, Never>?
    @State private var message = "Ready to queue or publish a project."
    @State private var stream = SyncInbox.Stream.all
    @State private var serverSnapshots: [SyncInbox.Event] = []
    @State private var loadedEndpoint: String?
    @State private var remoteProject: SyncInbox.Event?
    @State private var remoteName = ""
    @State private var remoteAction: RemoteAction?

    private enum RemoteAction { case rename, archive }

    var body: some View {
        Form {
            TextField("Backend HTTPS URL", text: $endpoint).textInputAutocapitalization(.never).autocorrectionDisabled()
                .disabled(task != nil).onChange(of: endpoint) { serverSnapshots = []; loadedEndpoint = nil }
            SecureField("Enrollment code", text: $code)
            Picker("Project", selection: $projectID) {
                Text("Select Project").tag(UUID?.none)
                ForEach(projects) { Text($0.name).tag(Optional($0.id)) }
            }
            Button("Queue for Sync") { start(publish: false) }.disabled(task != nil || projectID == nil)
            Button("Publish Pending Changes") { start(publish: true) }.disabled(task != nil)
            Picker("Download Filter", selection: $stream) {
                ForEach(SyncInbox.Stream.allCases) { Text($0.rawValue.capitalized).tag($0) }
            }.disabled(task != nil).onChange(of: stream) { serverSnapshots = [] }
            Button("Download Server Changes") { download() }.disabled(task != nil)
            if !serverSnapshots.isEmpty {
                Section("Server Snapshots") {
                    ForEach(serverSnapshots, id: \.key) { event in
                        HStack {
                            Text(event.summary)
                                .foregroundStyle(event.tombstone ? .secondary : .primary)
                            if event.projectRevision != nil {
                                Spacer()
                                Menu("Project Actions") {
                                    Button("Rename Server Project") {
                                        remoteProject = event; remoteName = ""; remoteAction = .rename
                                    }
                                    Button("Archive Server Project", role: .destructive) {
                                        remoteProject = event; remoteAction = .archive
                                    }
                                }.disabled(task != nil)
                            }
                        }
                    }
                }
            }
            if task != nil { Button("Cancel", role: .cancel) { task?.cancel() } }
            Text(message).textSelection(.enabled)
        }
        .navigationTitle("Backend Sync")
        .alert(remoteAction == .archive ? "Queue Server Archive?" : "Queue Server Rename", isPresented: Binding(
            get: { remoteAction != nil }, set: { if !$0 { remoteAction = nil } }
        )) {
            if remoteAction == .rename { TextField("New project name", text: $remoteName) }
            Button("Queue") { queueRemoteChange() }
            Button("Cancel", role: .cancel) {}
        }
        .onAppear {
            do { projects = try ProjectStore.projects() }
            catch { message = error.localizedDescription }
        }
        .onDisappear { task?.cancel(); code = "" }
    }

    private func queueRemoteChange() {
        guard let remoteAction, let event = remoteProject, let revision = event.projectRevision,
              let endpoint = loadedEndpoint else { return }
        let title = remoteName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard remoteAction == .archive || (1...200).contains(title.count) else {
            message = "Enter a project name of 1–200 characters."
            return
        }
        var text = ["server_project_id": event.id]
        if remoteAction == .rename { text["name"] = title }
        let job = ProjectSyncQueue.Job(id: UUID().uuidString,
            kind: remoteAction == .rename ? "project_update" : "project_archive",
            text: text, integers: ["expected_revision": revision])
        task = Task {
            defer { task = nil }
            do {
                let queue = try makeQueue(endpoint)
                try await queue.enqueue([job])
                message = await queue.pendingDetails()
            } catch { message = error.localizedDescription }
        }
    }

    #if DEBUG && targetEnvironment(simulator)
    private static let fixtureSyncRoot = FileManager.default.temporaryDirectory
        .appendingPathComponent("AlphaSync-" + UUID().uuidString)
    #endif

    private func syncRoot() throws -> URL {
        #if DEBUG && targetEnvironment(simulator)
        if ProcessInfo.processInfo.arguments.contains("--int02-live") { return Self.fixtureSyncRoot }
        #endif
        return try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                           appropriateFor: nil, create: true)
    }

    private func makeQueue(_ endpoint: String) throws -> ProjectSyncQueue {
        let root = try syncRoot()
        return try ProjectSyncQueue(file: root.appendingPathComponent("Sync/" +
            evidenceHash(Data(endpoint.utf8)) + ".json"))
    }

    private func download() {
        guard let url = URL(string: endpoint.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            message = "Enter a valid backend address."
            return
        }
        let enrollment = code
        let stream = stream
        code = ""
        serverSnapshots = []
        task = Task {
            defer { task = nil }
            do {
                let api = try WorkCamAPI(base: url)
                let root = try syncRoot()
                let account = evidenceHash(Data(url.absoluteString.utf8))
                let file = root.appendingPathComponent("Sync/inbox-\(account)-\(stream.rawValue).json")
                let inbox = try SyncInbox(file: file)
                serverSnapshots = await inbox.snapshots()
                try await api.connect(code: enrollment)
                try await inbox.synchronize(using: api, filter: stream)
                serverSnapshots = await inbox.snapshots()
                loadedEndpoint = url.absoluteString
                message = "Server snapshots saved · cursor \(await inbox.cursor())"
            } catch is CancellationError {
                message = "Cancelled. Committed pages remain saved."
            } catch {
                message = error.localizedDescription
            }
        }
    }

    private func start(publish: Bool) {
        guard let url = URL(string: endpoint.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            message = "Enter a valid backend address."
            return
        }
        let project = projects.first(where: { $0.id == projectID })
        let enrollment = code
        code = ""
        task = Task {
            defer { task = nil }
            do {
                let api = try WorkCamAPI(base: url)
                let queue = try makeQueue(url.absoluteString)
                if let project {
                    let photos = try ProjectStore.photos(in: project)
                    let issues = try ProjectStore.issues(for: project)
                    try await queue.enqueue(ProjectSyncQueue.Job.publication(project: project, photos: photos, issues: issues))
                }
                if publish {
                    try await api.connect(code: enrollment)
                    try await queue.flush(using: api)
                    message = "Server acknowledged the queued changes."
                } else {
                    let pending = await queue.pending()
                    message = pending == 0 ? "No unpublished changes queued." : "Queued locally · \(pending) pending"
                }
            } catch is CancellationError {
                message = "Cancelled. Unconfirmed operations remain queued."
            } catch {
                message = error.localizedDescription
            }
        }
    }
}
