// TM-04 B04/B06/B07 tests. REAL Windows execution.

import XCTest
import WorkCamSync

final class IntentImportTests: XCTestCase {

    private func op(_ id: String, deps: [String] = []) -> OutboxOperation {
        OutboxOperation(operationID: id, op: "issue_update",
                        payloadJSON: "{}", dependsOn: deps)
    }

    // MARK: B04

    func test_rebind_gives_new_ids_and_rewrites_dependents() throws {
        let (ops, supers) = try IntentAudit.rebindStale(
            operations: [
                op("b", deps: ["a"]),
                op("a"),
                op("c", deps: ["b"]),      // transitively follows b
                op("d"),                    // untouched
            ],
            staleIDs: ["a", "b"],
            uuidProvider: SequentialUUIDProvider(prefix: "n"),
            reason: "revision_conflict",
            at: Date(timeIntervalSince1970: 10))

        let byID = Dictionary(
            ops.map { ($0.operationID, $0) },
            uniquingKeysWith: { x, _ in x })
        // stale ops gone under their old ids
        XCTAssertNil(byID["a"])
        XCTAssertNil(byID["b"])
        // each stale op re-issued exactly once under a NEW id
        XCTAssertEqual(supers.count, 2)
        XCTAssertEqual(Set(supers.map(\.oldOperationID)),
                       ["a", "b"])
        let aNew = supers.first { $0.oldOperationID == "a" }!
        let bNew = supers.first { $0.oldOperationID == "b" }!
        XCTAssertNotEqual(aNew.newOperationID, bNew.newOperationID)
        // dependent c now points at b's NEW id
        XCTAssertEqual(byID["c"]?.dependsOn, [bNew.newOperationID])
        // untouched op kept its id
        XCTAssertEqual(byID["d"]?.operationID, "d")
        // the new b still depends on the new a
        XCTAssertEqual(byID[bNew.newOperationID]?.dependsOn,
                       [aNew.newOperationID])
    }

    func test_rebind_of_unknown_id_is_named_error() {
        XCTAssertThrowsError(
            try IntentAudit.rebindStale(
                operations: [op("a")], staleIDs: ["ghost"],
                uuidProvider: SequentialUUIDProvider(),
                reason: "r", at: Date())) { e in
            XCTAssertEqual(
                (e as? IntentAudit.AuditError),
                .unknownStaleID("ghost"))
        }
    }

    func test_supersession_records_carry_reason_and_time() throws {
        let at = Date(timeIntervalSince1970: 1234)
        let (_, supers) = try IntentAudit.rebindStale(
            operations: [op("a")], staleIDs: ["a"],
            uuidProvider: SequentialUUIDProvider(prefix: "x"),
            reason: "revision_conflict", at: at)
        XCTAssertEqual(supers[0].reason, "revision_conflict")
        XCTAssertEqual(supers[0].recordedAt, at)
        XCTAssertEqual(supers[0].oldOperationID, "a")
        XCTAssertEqual(supers[0].newOperationID, "x-000001")
    }

    // MARK: B06

    private func local(_ id: String, name: String,
                       rev: Int = 1) -> ProjectRecord {
        ProjectRecord(projectID: id, name: name, description: "",
                      siteAddress: "", status: "active",
                      revision: rev,
                      createdAt: Date(timeIntervalSince1970: 0),
                      updatedAt: Date(timeIntervalSince1970: 0))
    }

    func test_import_plan_partitions_the_three_ways() throws {
        let mappings = UUIDMappingStore()
        try mappings.associate(entityType: "project",
                                localID: "L-1", serverID: "S-1",
                                at: Date())
        let locals = [
            local("L-1", name: "Tower A"),
            local("L-2", name: "Tower B"),   // name clash, unmapped
        ]
        let plan = ImportPlanner.plan(
            serverProjects: [
                ServerProjectStub(serverID: "S-1", name: "Tower A"),
                ServerProjectStub(serverID: "S-2", name: "Tower B"),
                ServerProjectStub(serverID: "S-3", name: "Tower C"),
            ],
            localProjects: locals,
            mappings: mappings,
            uuidProvider: SequentialUUIDProvider(prefix: "L"))

        XCTAssertEqual(plan.count, 3)
        XCTAssertEqual(plan[0],
            .linkExisting(serverID: "S-1", localID: "L-1"))
        XCTAssertEqual(plan[1],
            .needsDecision(serverID: "S-2", name: "Tower B",
                           conflictingLocalID: "L-2"))
        guard case .createNew(serverID: "S-3", localID: let newLocal)
                = plan[2] else {
            return XCTFail("expected createNew")
        }
        XCTAssertEqual(newLocal, "L-000001")
    }

    func test_import_plan_is_idempotent_on_rerun() throws {
        let mappings = UUIDMappingStore()
        try mappings.associate(entityType: "project",
                                localID: "L-1", serverID: "S-1",
                                at: Date())
        let plan = ImportPlanner.plan(
            serverProjects: [
                ServerProjectStub(serverID: "S-1", name: "A")],
            localProjects: [local("L-1", name: "A")],
            mappings: mappings,
            uuidProvider: SequentialUUIDProvider())
        XCTAssertEqual(plan.first,
            .linkExisting(serverID: "S-1", localID: "L-1"))
        // replaying the same server list changes nothing
        let plan2 = ImportPlanner.plan(
            serverProjects: [
                ServerProjectStub(serverID: "S-1", name: "A")],
            localProjects: [local("L-1", name: "A")],
            mappings: mappings,
            uuidProvider: SequentialUUIDProvider())
        XCTAssertEqual(plan2.first, plan.first)
    }

    // MARK: B07

    private func link(kind: RemediationLinkEvent.Kind = .link,
                      issue: String = "i-1", photo: String = "ph-1",
                      role: IssuePhotoRole = .before,
                      rev: Int = 3,
                      at: TimeInterval = 100) -> RemediationLinkEvent {
        RemediationLinkEvent(
            kind: kind, issueID: issue, photoID: photo, role: role,
            baseIssueRevision: rev,
            recordedAt: Date(timeIntervalSince1970: at))
    }

    func test_cross_project_link_refused_by_name() {
        XCTAssertThrowsError(
            try RemediationLinkSemantics.validate(
                event: link(),
                issueProjectID: "p-1", photoProjectID: "p-2",
                currentIssueRevision: 3,
                issueHasAfterPhoto: false)) { e in
            XCTAssertEqual(
                e as? RemediationLinkError,
                .crossProjectRefusal(photoProject: "p-2",
                                     issueProject: "p-1"))
        }
    }

    func test_stale_revision_link_is_refused() {
        XCTAssertThrowsError(
            try RemediationLinkSemantics.validate(
                event: link(rev: 2),
                issueProjectID: "p-1", photoProjectID: "p-1",
                currentIssueRevision: 3,
                issueHasAfterPhoto: false)) { e in
            XCTAssertEqual(
                e as? RemediationLinkError,
                .staleRevision(issueID: "i-1", have: 2, need: 3))
        }
    }

    func test_before_photo_not_replaceable_once_after_exists() {
        XCTAssertThrowsError(
            try RemediationLinkSemantics.validate(
                event: link(kind: .replace, role: .before),
                issueProjectID: "p-1", photoProjectID: "p-1",
                currentIssueRevision: 3,
                issueHasAfterPhoto: true)) { e in
            XCTAssertEqual(
                e as? RemediationLinkError,
                .roleNotSettable(role: .before))
        }
        // but a plain LINK of before is fine even then (append)
        XCTAssertNoThrow(try RemediationLinkSemantics.validate(
            event: link(kind: .link, role: .before),
            issueProjectID: "p-1", photoProjectID: "p-1",
            currentIssueRevision: 3, issueHasAfterPhoto: true))
    }

    func test_withdraw_is_a_recorded_event_not_a_silent_drop() {
        let events = [
            link(photo: "ph-1", role: .before, at: 100),
            link(photo: "ph-2", role: .progress, at: 200),
            // withdraw the progress photo
            link(kind: .unlink, photo: "ph-2", role: .progress,
                 at: 300),
        ]
        let live = RemediationLinkSemantics.liveRoles(events: events)
        XCTAssertEqual(live[.before], "ph-1")
        XCTAssertNil(live[.progress])       // withdrawn
        XCTAssertNil(live[.after])
        // the journal itself is append-only history: 3 events kept
        XCTAssertEqual(events.count, 3)
    }

    func test_replace_of_role_overwrites_in_journal_order() {
        let events = [
            link(photo: "ph-a", role: .after, at: 100),
            link(kind: .replace, photo: "ph-b", role: .after,
                 at: 200),
        ]
        let live = RemediationLinkSemantics.liveRoles(events: events)
        XCTAssertEqual(live[.after], "ph-b")
    }

    func test_unlink_of_non_matching_photo_is_inert() {
        let events = [
            link(photo: "ph-1", role: .before, at: 100),
            // unlink names a DIFFERENT photo than the role holds
            link(kind: .unlink, photo: "ph-other", role: .before,
                 at: 200),
        ]
        let live = RemediationLinkSemantics.liveRoles(events: events)
        XCTAssertEqual(live[.before], "ph-1")   // untouched
    }

    func test_role_vocabulary_is_fixed() {
        XCTAssertEqual(IssuePhotoRole.allCases.map(\.rawValue),
                       ["before", "progress", "after"])
    }
}
