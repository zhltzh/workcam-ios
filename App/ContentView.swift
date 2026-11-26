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

    var body: some View {
        Form {
            TextField("Backend HTTPS URL", text: $endpoint).textInputAutocapitalization(.never).autocorrectionDisabled()
            SecureField("Enrollment code", text: $code)
            Picker("Project", selection: $projectID) {
                Text("Select Project").tag(UUID?.none)
                ForEach(projects) { Text($0.name).tag(Optional($0.id)) }
            }
            Button("Queue for Sync") { start(publish: false) }.disabled(task != nil || projectID == nil)
            Button("Publish Pending Changes") { start(publish: true) }.disabled(task != nil || projectID == nil)
            if task != nil { Button("Cancel", role: .cancel) { task?.cancel() } }
            Text(message).textSelection(.enabled)
        }
        .navigationTitle("Backend Sync")
        .onAppear {
            do { projects = try ProjectStore.projects() }
            catch { message = error.localizedDescription }
        }
        .onDisappear { task?.cancel(); code = "" }
    }

    private func start(publish: Bool) {
        guard let url = URL(string: endpoint.trimmingCharacters(in: .whitespacesAndNewlines)),
              let project = projects.first(where: { $0.id == projectID }) else { return }
        let enrollment = code
        code = ""
        task = Task {
            defer { task = nil }
            do {
                let api = try WorkCamAPI(base: url)
                let root = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                       appropriateFor: nil, create: true)
                let queue = try ProjectSyncQueue(file: root.appendingPathComponent("Sync/" +
                    evidenceHash(Data(url.absoluteString.utf8)) + ".json"))
                let photos = try ProjectStore.photos(in: project)
                let issues = try ProjectStore.issues(for: project)
                try await queue.enqueue(ProjectSyncQueue.Job.publication(project: project, photos: photos, issues: issues))
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
