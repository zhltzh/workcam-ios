// TM68 rule tests: every TM34 conflict kind maps to a detail with
// allowed actions; nothing is ever silently auto-resolved.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import XCTest
@testable import WorkCamSync

final class ConflictDetailRuleTests: XCTestCase {

    func test_revision_conflict_extracts_current_revision() {
        let d = ConflictDetail.from(
            serverErrorCode: "revision_conflict",
            serverMessage: "{'code': 'revision_conflict', "
                + "'message': 'project revision changed: expected 1, "
                + "current 3', 'details': {'current_revision': 3, "
                + "'recovery': 're-fetch...'}}",
            entityType: "project", entityID: "p-1",
            localRevision: 1, localPayloadJSON: "{\"name\":\"v4\"}")
        XCTAssertNotNil(d)
        XCTAssertEqual(d!.kind, .revisionConflict)
        XCTAssertEqual(d!.serverCurrentRevision, 3)
        XCTAssertTrue(d!.allowedActions
            .contains(.rebaseWithCurrentRevision))
        XCTAssertTrue(d!.allowedActions
            .contains(.keepServerDiscardLocal))
        XCTAssertFalse(d!.allowedActions.isEmpty)
    }

    func test_annotation_deleted_offers_recreate_only() {
        let d = ConflictDetail.from(
            serverErrorCode: "annotation_deleted",
            serverMessage: "deleted annotations are immutable",
            entityType: "annotation", entityID: "a-9",
            localRevision: 2, localPayloadJSON: "{}")
        XCTAssertEqual(d?.allowedActions.first, .recreateAnnotation)
        XCTAssertTrue(d!.userSummary.contains("immutable"))
    }

    func test_batch_id_conflict_requires_restart() {
        let d = ConflictDetail.from(
            serverErrorCode: "batch_id_conflict",
            serverMessage: "photo already processed under batch_id "
                + "with DIFFERENT parameters",
            entityType: "photo", entityID: "ph-1",
            localRevision: nil, localPayloadJSON: "{}")
        XCTAssertEqual(d?.allowedActions, [.cancelBatchAndRestart])
    }

    func test_archived_project_needs_unarchive() {
        let d = ConflictDetail.from(
            serverErrorCode: "project_archived",
            serverMessage: "cannot add photos to an archived project",
            entityType: "project", entityID: "p-arch",
            localRevision: nil, localPayloadJSON: "{}")
        XCTAssertEqual(d?.allowedActions, [.unarchiveFirst])
    }

    func test_non_conflict_code_returns_nil() {
        // plain failures are NOT conflicts - conflating them would
        // show users a resolution UI for a server bug
        XCTAssertNil(ConflictDetail.from(
            serverErrorCode: "invalid_payload",
            serverMessage: "bad",
            entityType: "project", entityID: nil,
            localRevision: nil, localPayloadJSON: "{}"))
    }

    func test_no_kind_resolves_silently() {
        // the engine exposes actions; it never picks one
        for code in ConflictKind.allCases {
            let d = ConflictDetail.from(
                serverErrorCode: code.rawValue,
                serverMessage: "{'current_revision': 2}",
                entityType: "x", entityID: nil,
                localRevision: nil, localPayloadJSON: "{}")
            XCTAssertNotNil(d)
            XCTAssertFalse(d!.allowedActions.isEmpty)
            XCTAssertFalse(d!.allowedActions.count > 3,
                "a conflict must not offer a confusing action zoo")
        }
    }
}
