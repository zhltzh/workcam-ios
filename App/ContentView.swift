import SwiftUI

struct ContentView: View {
    @State private var exporting = false
    @State private var exportMessage = ""
    @State private var showExportMessage = false
    @State private var exportURL: URL?
    @State private var evidenceReport = "Local verification pending"
    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 24) {
                Text("Trusted Capture Prototype").font(.title2)
                VStack(alignment: .leading) {
                    Text("Camera Status").font(.headline)
                    Text("Open camera to check availability").foregroundStyle(.secondary)
                }
                status("Signing Status")
                status("Network Status")
                #if DEBUG
                NavigationLink("LiDAR Lab") { LidarLabView() }
                #endif
                NavigationLink("Open Camera") { CameraView() }
                NavigationLink("Evidence Debug") {
                    ScrollView {
                        VStack {
                            Button("Reverify Saved Evidence") {
                                Task { evidenceReport = await Task.detached { EvidenceStore.report() }.value }
                            }
                            Button("Test Tamper Detection on Copies") {
                                Task { evidenceReport = await Task.detached { EvidenceStore.tamperReport() }.value }
                            }
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
