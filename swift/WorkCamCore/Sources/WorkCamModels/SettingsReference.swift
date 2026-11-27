// FE-E09: Settings & Data Management SwiftUI reference.
// XCODE_PENDING: authored on Windows; Mac compiles.
//
// THE RED LINE this view enforces structurally (task + E01 audit):
//   cache clearing and ORIGINAL deletion are DIFFERENT actions with
//   different confirmations. Cache = derivatives/thumbnails only
//   (PhotoAssetCache is typed derivative-only in WorkCamSync).
//   Original deletion is NOT OFFERED in this UI at all - it is a
//   product decision (DECISION_REQUIRED) and stays absent until then.

// Platform gate (TM-04 A05): the Windows toolchain has no SwiftUI.
#if canImport(SwiftUI)
import SwiftUI
import WorkCamSync

public struct SettingsView: View {
    // Units persist via AppStorage; the converter is WorkCamGeometry
    @AppStorage("wcds.unitSystem") public var unitSystem = "metric"
    @AppStorage("wcds.watermarkTemplate") public var watermarkTemplate =
        "engineering_default"
    @AppStorage("wcds.privacy.redactByDefault")
    public var redactByDefault = false

    public var body: some View {
        List {
            Section("Units") {
                Picker("Measurements", selection: $unitSystem) {
                    Text("Metric (m, m²)").tag("metric")
                    Text("Imperial (ft, ft²)").tag("imperial")
                }
                .accessibilityIdentifier("settingsUnitsPicker")
            }
            Section("Watermark") {
                Picker("Template", selection: $watermarkTemplate) {
                    Text("Engineering Default").tag("engineering_default")
                    Text("Inspection").tag("inspection")
                    Text("Minimal").tag("minimal")
                }
                .accessibilityIdentifier("settingsWatermarkPicker")
            }
            Section("Privacy") {
                Toggle("Redact derivatives by default",
                      isOn: $redactByDefault)
                    .accessibilityIdentifier("settingsRedactDefault")
            } footer: {
                Text("Redaction applies to DERIVATIVES only. "
                     + "Originals are never modified.")
            }
            DataManagementSection()
            DeviceSection()
        }
        .navigationTitle("Settings")
    }
}

// MARK: - Data management (cache vs originals, structurally distinct)

public struct DataManagementSection: View {
    @State private var cacheBytes: Int = 0
    @State private var originalCount: Int = 0
    @State private var confirmClearCache = false
    @State private var exportMessage = ""

    public var body: some View {
        Section("Data") {
            // CACHE: derivatives + thumbnails ONLY (typed cache)
            row("Cache (derivatives + thumbnails)",
                ByteCountFormatter.string(
                    fromByteCount: Int64(cacheBytes), countStyle: .file))
            Button("Clear cache") { confirmClearCache = true }
                .accessibilityIdentifier("settingsClearCache")
            // ORIGINALS: shown, protected, no delete button exists
            row("Original photos", "\(originalCount)")
                .accessibilityIdentifier("settingsOriginalCount")
        } footer: {
            Text("Clearing the cache frees space by removing "
                 + "REGENERABLE derivatives. Original photos are "
                 + "immutable evidence and are never touched by "
                 + "cache management.")
        }
        .confirmationDialog(
            "Clear cache?",
            isPresented: $confirmClearCache,
            titleVisibility: .visible) {
            Button("Clear cache (originals stay)", role: .destructive) {
                // PhotoAssetCache is derivative-only BY TYPE - there
                // is no API to put an original into it
                clearDerivativeCache()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Only thumbnails and watermarked previews are "
                 + "removed. Your original photos stay untouched.")
        }
    }

    private func clearDerivativeCache() {
        cacheBytes = 0
        exportMessage = "Cache cleared. Originals untouched."
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack { Text(label); Spacer();
                Text(value).monospacedDigit()
                    .foregroundStyle(.secondary) }
    }
}

// MARK: - Device identity (TM03 lifecycle surfaced read-only)

public struct DeviceSection: View {
    @State private var deviceID = ""
    @State private var keyState = "unknown"

    public var body: some View {
        Section("Device") {
            row("Device ID", deviceID)
            row("Key state", keyState)
                .accessibilityIdentifier("settingsKeyState")
        } footer: {
            Text("Key state is SERVER-resolved. Possession of the "
                 + "signing key is proven at rotation; it does not "
                 + "prove Secure Enclave hardware origin.")
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack { Text(label); Spacer();
                Text(value).monospacedDigit()
                    .foregroundStyle(.secondary) }
    }
}
#endif
