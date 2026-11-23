import SwiftUI

struct ContentView: View {
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
                            Button("Prepare Evidence Export") {
                                do { exportURL = try EvidenceStore.exportPackages() }
                                catch { exportURL = nil; evidenceReport = "Export failed: \(error)" }
                            }
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
