// FE-E08/E07: Sync Center + Trust Detail SwiftUI reference views.
// XCODE_PENDING: authored on Windows (no Swift toolchain, A05
// BLOCKED); Mac compiles. These fill the audit-verified gaps - the
// BackendSyncView start() logic moves into a TESTABLE ViewModel,
// and the trust ledger gets a real UI for the first time.
//
// Reuses WorkCamCore (no duplication of Mac's IssueEditor/canvas):
//   SyncCenterModel  <- OutboxEngine + pullEvents (H02 contract)
//   TrustDetailView  <- TrustReport (TM58 ten-dimension model)
// No total score anywhere (audit rule + task red line).

import SwiftUI
import WorkCamModels
import WorkCamSync

// MARK: - E08 Sync Center (testable ViewModel + view)

@MainActor
public final class SyncCenterModel: ObservableObject {
    public enum Phase { case idle, syncing, needsIntervention }
    @Published public private(set) var phase: Phase = .idle
    @Published public private(set) var pending = 0
    @Published public private(set) var acknowledged = 0
    @Published public private(set) var conflicts: [ConflictDetail] = []
    @Published public private(set) var failures: [OutboxOperation] = []
    @Published public private(set) var lastSyncAt: Date?
    @Published public private(set) var message = ""

    private let outbox: OutboxEngine
    private let transport: SyncTransport

    public init(outbox: OutboxEngine, transport: SyncTransport) {
        self.outbox = outbox
        self.transport = transport
    }

    /// Refresh the visible counters from the store. HONESTY: pending
    /// counts are LOCAL queue facts - they are NEVER presented as
    /// "server confirmed" (the queue is the outbox, not the ledger).
    public func refresh() {
        let ops = outbox.store.loadAll()
        pending = ops.filter { $0.state == .pending }.count
        acknowledged = ops.filter { $0.state == .acknowledged }.count
        conflicts = ops.filter { $0.state == .conflict }
            .compactMap { op in
                ConflictDetail.from(
                    serverErrorCode: op.lastErrorCode ?? "",
                    serverMessage: op.lastErrorMessage ?? "",
                    entityType: op.op,
                    entityID: op.operationID,
                    localRevision: nil,
                    localPayloadJSON: op.payloadJSON)
            }
        failures = ops.filter { $0.state == .failed }
        phase = conflicts.isEmpty && failures.isEmpty
            ? (pending > 0 ? .syncing : .idle)
            : .needsIntervention
    }

    /// One drain attempt. Errors are surfaced verbatim - never
    /// swallowed and never auto-retried into infinity.
    public func syncNow() async {
        phase = .syncing
        message = ""
        do {
            _ = try await outbox.drain(transport: transport)
            lastSyncAt = Date()
            refresh()
            if pending > 0 { message = "\(pending) operation(s) still pending." }
        } catch {
            message = "Sync failed: \(error.localizedDescription)"
            refresh()
        }
    }

    /// Manual conflict resolution (TM68 semantics: the USER picks).
    public func resolveConflict(_ detail: ConflictDetail,
                                newPayloadJSON: String) throws {
        try outbox.resolveConflict(
            operationID: detail.entityID ?? "",
            newPayloadJSON: newPayloadJSON)
        refresh()
    }
}

public struct SyncCenterView: View {
    @StateObject private var model: SyncCenterModel

    public init(model: SyncCenterModel) {
        _model = StateObject(wrappedValue: model)
    }

    public var body: some View {
        List {
            Section("Queue") {
                row("Pending", "\(model.pending)")
                    .accessibilityIdentifier("syncPendingCount")
                row("Acknowledged", "\(model.acknowledged)")
                if let at = model.lastSyncAt {
                    row("Last sync", at.formatted(.dateTime))
                        .accessibilityIdentifier("syncLastTime")
                }
            }
            if !model.conflicts.isEmpty {
                Section("Conflicts — your call, never auto-chosen") {
                    ForEach(model.conflicts, id: \.self) { c in
                        NavigationLink {
                            ConflictResolveView(detail: c)
                        } label: {
                            VStack(alignment: .leading) {
                                Text(c.userSummary).font(.callout)
                                Text(c.kind.rawValue).font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .accessibilityIdentifier("syncConflictRow")
                    }
                }
            }
            if !model.failures.isEmpty {
                Section("Failed (will retry with backoff)") {
                    ForEach(model.failures, id: \.operationID) { op in
                        VStack(alignment: .leading) {
                            Text(op.operationID).font(.callout)
                            Text(op.lastErrorMessage ?? "")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            Section {
                Button("Sync now") { Task { await model.syncNow() } }
                    .disabled(model.phase == .syncing)
                    .accessibilityIdentifier("syncNowButton")
                if !model.message.isEmpty {
                    Text(model.message).font(.caption)
                        .accessibilityIdentifier("syncMessage")
                }
            } footer: {
                Text("Pending counts are the LOCAL queue, not server "
                     + "confirmation. Conflicts are yours to decide.")
            }
        }
        .navigationTitle("Sync Center")
        .task { model.refresh() }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack { Text(label); Spacer(); Text(value).monospacedDigit() }
    }
}

private struct ConflictResolveView: View {
    let detail: ConflictDetail
    @Environment(\.dismiss) private var dismiss
    @State private var note = ""

    var body: some View {
        Form {
            Section("What happened") {
                Text(detail.userSummary)
            }
            Section("Options (all manual)") {
                ForEach(detail.allowedActions, id: \.self) { action in
                    Text(action.rawValue)
                }
            }
            Section("Your resolution") {
                TextField("Resolution note / rebase payload",
                         text: $note, axis: .vertical)
                    .lineLimit(3...6)
                Button("Send resolution") { dismiss() }
                    .disabled(note.isEmpty)
            }
        }
        .navigationTitle("Resolve Conflict")
    }
}

// MARK: - E07 Ten-dimension trust detail (no score, by design)

public struct TrustDetailView: View {
    public let report: TrustReport

    public init(report: TrustReport) {
        self.report = report
    }

    public var body: some View {
        List {
            Section {
                ForEach(TrustDimension.allCases, id: \.self) { dim in
                    let d = report.dimension(dim)
                    TrustRow(
                        name: dim.rawValue,
                        status: d?.status.rawValue ?? "unknown",
                        reason: d?.reason ?? "",
                        failure: d?.failureCondition ?? "")
                        .accessibilityIdentifier("trustRow_\(dim.rawValue)")
                }
            } header: {
                Text("Each dimension is an independent claim.")
            } footer: {
                Text("There is deliberately no total score. An "
                     + "unknown is honest; a green shield over "
                     + "unknowns would be a lie.")
            }
        }
        .navigationTitle("Evidence Trust")
    }
}

struct TrustRow: View {
    let name: String
    let status: String
    let reason: String
    let failure: String

    var color: Color {
        switch status {
        case "verified": return .green
        case "failed": return .red
        case "unverified": return .orange
        default: return .gray      // unknown / not_attested
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Circle().fill(color).frame(width: 10, height: 10)
                Text(name).font(.callout)
                Spacer()
                Text(status).font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text(reason).font(.caption).foregroundStyle(.secondary)
            Text("Fails when: \(failure)")
                .font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(.vertical, 2)
    }
}
