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

    @State private var blocked: [ProjectSyncQueue.Job] = []
    @State private var editIssue: SyncInbox.Event?
    private enum RemoteAction { case rename, archive, associate }

    var body: some View {
        Form {
            TextField("Backend HTTPS URL", text: $endpoint).textInputAutocapitalization(.never).autocorrectionDisabled()
                .disabled(task != nil).onChange(of: endpoint) { serverSnapshots = []; loadedEndpoint = nil; blocked = [] }
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
                                    if projectID != nil {
                                        Button("Associate Selected Local Project") {
                                            remoteProject = event; remoteAction = .associate
                                        }
                                    }
                                    Button("Archive Server Project", role: .destructive) {
                                        remoteProject = event; remoteAction = .archive
                                    }
                                }.disabled(task != nil)
                            }
                            if event.kind == "issue", event.fields["status"] as? String != "closed" {
                                Button("Edit Server Issue") { editIssue = event }.disabled(task != nil)
                            }
                        }
                    }
                }
            }
            if !blocked.isEmpty {
                Section("Outbox Conflicts") {
                    ForEach(blocked, id: \.id) { job in
                        VStack(alignment: .leading) {
                            Text(job.kind.replacingOccurrences(of: "_", with: " ").capitalized).font(.headline)
                            Text(job.detail ?? job.issue ?? "Failed. Kept in the queue.")
                            Text(job.text.sorted(by: { $0.key < $1.key }).map { "\($0.key): \($0.value)" }.joined(separator: "\n"))
                                .font(.caption).textSelection(.enabled)
                            if let id = job.text["server_project_id"] ?? job.text["server_issue_id"],
                               let event = serverSnapshots.first(where: { $0.id == id }),
                               let revision = event.fields["revision"] as? Int {
                                Text("Server: \(event.summary) · revision \(revision)").font(.caption)
                                if job.issue == "revision_conflict", event.fields["status"] as? String != "closed",
                                   event.fields["status"] as? String != "archived" {
                                    Button("Queue My Edit at Server Revision \(revision)") {
                                        resolve(job, revision: revision)
                                    }.disabled(task != nil)
                                }
                            }
                        }
                    }
                    Text("Unresolved changes remain queued. Download fresh server changes before rebasing.").font(.caption)
                }
            }
            if task != nil { Button("Cancel", role: .cancel) { task?.cancel() } }
            Text(message).textSelection(.enabled)
        }
        .navigationTitle("Backend Sync")
        .sheet(item: $editIssue) { event in
            ServerIssueEditor(event: event) { enqueueRemote($0) }
        }
        .alert(remoteAction == .archive ? "Queue Server Archive?" :
            remoteAction == .associate ? "Associate the selected local project?" : "Queue Server Rename", isPresented: Binding(
            get: { remoteAction != nil }, set: { if !$0 { remoteAction = nil } }
        )) {
            if remoteAction == .rename { TextField("New project name", text: $remoteName) }
            Button(remoteAction == .associate ? "Associate" : "Queue") { queueRemoteChange() }
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
        if remoteAction == .associate {
            guard let local = projectID, let server = UUID(uuidString: event.id) else { return }
            task = Task {
                defer { task = nil }
                do {
                    try await makeQueue(endpoint).associate(local: local, server: server, revision: revision)
                    message = "Project association saved. Local records were preserved."
                } catch { message = error.localizedDescription }
            }
            return
        }
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

    private func enqueueRemote(_ job: ProjectSyncQueue.Job) {
        guard let endpoint = loadedEndpoint else { return }
        task = Task {
            defer { task = nil }
            do {
                let queue = try makeQueue(endpoint)
                try await queue.enqueue([job])
                message = await queue.pendingDetails()
            } catch { message = error.localizedDescription }
        }
    }

    private func resolve(_ job: ProjectSyncQueue.Job, revision: Int) {
        guard let endpoint = loadedEndpoint else { return }
        task = Task {
            defer { task = nil }
            do {
                let queue = try makeQueue(endpoint)
                try await queue.resolve(job.id, newRevision: revision)
                blocked = await queue.blockedJobs()
                message = "Rebased edit queued with a new operation ID."
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
                blocked = await (try makeQueue(url.absoluteString)).blockedJobs()
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
                    let count = try await queue.flush(using: api)
                    blocked = await queue.blockedJobs()
                    message = "\(count) newly confirmed · \(await queue.pendingDetails())"
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


private struct ServerIssueEditor: View {
    @Environment(\.dismiss) private var dismiss
    let event: SyncInbox.Event
    let enqueue: (ProjectSyncQueue.Job) -> Void
    @State private var title = ""
    @State private var description = ""
    @State private var severity = "medium"
    @State private var role = "before"
    @State private var photo = ""

    private var previous: String? {
        (event.fields["photos"] as? [[String: String]])?.first(where: { $0["role"] == role })?["photo_id"]
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Issue") {
                    TextField("Title", text: $title)
                    TextField("Description", text: $description, axis: .vertical)
                    Picker("Severity", selection: $severity) {
                        ForEach(["low", "medium", "high", "critical"], id: \.self) { Text($0.capitalized) }
                    }
                    Button("Queue Issue Edit") {
                        save("issue_update", ["title": title, "description": description, "severity": severity])
                    }.disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if let status = event.fields["status"] as? String,
                       let next = ["open": "in_progress", "in_progress": "resolved", "resolved": "closed"][status] {
                        Button("Queue Status: " + next) { save("issue_transition", ["target_status": next]) }
                    }
                }
                Section("Photo Role") {
                    Picker("Role", selection: $role) {
                        ForEach(["before", "progress", "after"], id: \.self) { Text($0.capitalized) }
                    }
                    if let previous { Text("Current: " + previous).font(.caption) }
                    TextField("Server photo UUID", text: $photo).textInputAutocapitalization(.never)
                    Button("Queue Link or Replace") {
                        var fields = ["role": role, "server_photo_id": photo.lowercased()]
                        if let previous { fields["expected_previous_photo_id"] = previous }
                        save("issue_photo_link", fields)
                    }.disabled(UUID(uuidString: photo) == nil)
                    if let previous {
                        Button("Queue Unlink Current Photo", role: .destructive) {
                            save("issue_photo_unlink", ["role": role, "server_photo_id": previous])
                        }
                    }
                }
            }
            .navigationTitle("Server Issue")
            .toolbar { Button("Close") { dismiss() } }
            .onAppear {
                title = event.fields["title"] as? String ?? ""
                description = event.fields["description"] as? String ?? ""
                severity = event.fields["severity"] as? String ?? "medium"
            }
        }
    }

    private func save(_ kind: String, _ fields: [String: String]) {
        guard let revision = event.fields["revision"] as? Int else { return }
        var payload = fields
        payload["server_issue_id"] = event.id
        let versioned = kind == "issue_update" || kind == "issue_transition"
        enqueue(.init(id: UUID().uuidString, kind: kind, text: payload,
                      integers: versioned ? ["expected_revision": revision] : [:]))
        dismiss()
    }
}
