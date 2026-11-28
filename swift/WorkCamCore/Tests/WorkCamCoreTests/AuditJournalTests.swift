// TM-04 H06 tests: organization audit journal. REAL Windows
// execution. EXPERIMENTAL reserve - not in RC4.

import XCTest
import WorkCamEnterprise

final class AuditJournalTests: XCTestCase {

    private func fill(_ journal: OrganizationAuditJournal,
                      org: String = "org-1") {
        var t: TimeInterval = 100
        for (actor, action, target) in [
            ("m1", "member_invited", "inv-1"),
            ("m2", "photo_captured", "ph-1"),
            ("m1", "role_assigned", "m2"),
            ("m3", "issue_closed", "i-9"),
        ] {
            t += 50
            journal.append(organizationID: org,
                           actorMemberID: actor, action: action,
                           targetType: "x", targetID: target,
                           payloadHint: "hint-\(action)",
                           at: Date(timeIntervalSince1970: t))
        }
    }

    func test_sequences_are_per_org_monotone() {
        let journal = OrganizationAuditJournal()
        fill(journal)
        fill(journal, org: "org-2")           // other org starts at 1
        let h1 = journal.history(organizationID: "org-1")
        let h2 = journal.history(organizationID: "org-2")
        XCTAssertEqual(h1.map(\.sequence), [1, 2, 3, 4])
        XCTAssertEqual(h2.map(\.sequence), [1, 2, 3, 4])
        XCTAssertEqual(h1.map(\.action),
                       journal.history(organizationID: "org-1")
                           .map(\.action))     // stable
    }

    func test_journal_is_append_only_by_construction() {
        // The public API has no update/delete; appending more never
        // rewrites earlier sequences (the immutability pin).
        let journal = OrganizationAuditJournal()
        fill(journal)
        journal.append(organizationID: "org-1",
                       actorMemberID: "m1", action: "extra",
                       targetType: "x", targetID: "t",
                       payloadHint: "", at: Date())
        let h = journal.history(organizationID: "org-1")
        XCTAssertEqual(h.count, 5)
        XCTAssertEqual(h.last?.action, "extra")
        XCTAssertEqual(h.first?.sequence, 1)   // history untouched
    }

    func test_query_by_actor_action_and_target() {
        let journal = OrganizationAuditJournal()
        fill(journal)
        XCTAssertEqual(
            journal.history(organizationID: "org-1", by: "m1")
                .map(\.action),
            ["member_invited", "role_assigned"])
        XCTAssertEqual(
            journal.history(organizationID: "org-1",
                            action: "issue_closed")
                .map(\.actorMemberID), ["m3"])
        XCTAssertEqual(
            journal.history(organizationID: "org-1",
                            targetID: "ph-1").count, 1)
    }

    func test_windowed_query_uses_injected_time_bounds() {
        let journal = OrganizationAuditJournal()
        fill(journal)                     // entries at t=150,200,250,300
        let window = journal.history(
            organizationID: "org-1",
            from: Date(timeIntervalSince1970: 200),
            to: Date(timeIntervalSince1970: 250))
        // 150 (member_invited) excluded; 200, 250 included;
        // 300 (issue_closed) excluded
        XCTAssertEqual(window.map(\.action),
                       ["photo_captured", "role_assigned"])
        // tight single-point window
        let point = journal.history(
            organizationID: "org-1",
            from: Date(timeIntervalSince1970: 300),
            to: Date(timeIntervalSince1970: 300))
        XCTAssertEqual(point.map(\.action), ["issue_closed"])
    }

    func test_orgs_are_isolated() {
        let journal = OrganizationAuditJournal()
        fill(journal)
        fill(journal, org: "org-2")
        XCTAssertEqual(
            journal.history(organizationID: "org-1").count, 4)
        XCTAssertEqual(
            journal.history(organizationID: "org-2").count, 4)
        // an org's query NEVER returns another org's rows
        for e in journal.history(organizationID: "org-1") {
            XCTAssertEqual(e.organizationID, "org-1")
        }
    }

    func test_audit_sequencing_survives_interleaved_orgs() {
        let journal = OrganizationAuditJournal()
        var t: TimeInterval = 0
        for i in 0..<6 {
            t += 1
            journal.append(
                organizationID: i % 2 == 0 ? "org-a" : "org-b",
                actorMemberID: "m", action: "a\(i)",
                targetType: "t", targetID: "x",
                payloadHint: "", at: Date(timeIntervalSince1970: t))
        }
        let a = journal.history(organizationID: "org-a")
        let b = journal.history(organizationID: "org-b")
        XCTAssertEqual(a.map(\.sequence), [1, 2, 3])
        XCTAssertEqual(b.map(\.sequence), [1, 2, 3])
        // actions stay in append order within each org
        XCTAssertEqual(a.map(\.action), ["a0", "a2", "a4"])
        XCTAssertEqual(b.map(\.action), ["a1", "a3", "a5"])
    }
}
