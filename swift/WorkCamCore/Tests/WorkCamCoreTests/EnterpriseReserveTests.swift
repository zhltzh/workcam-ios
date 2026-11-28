// TM-04 H02/H03/H04 tests: the enterprise reserve. REAL Windows
// execution. Marked EXPERIMENTAL by convention (target isolation).

import XCTest
import WorkCamModels
import WorkCamSync
import WorkCamEnterprise

final class RoleMatrixTests: XCTestCase {

    func test_owner_can_do_everything_in_any_scope() {
        // scope pairing is a property of the ACTION (org-level
        // actions exist only in org scope) - so "owner does
        // everything" means: every action PAIRED with its valid
        // scope, plus every project-scope action in any scope the
        // matrix names. Org actions in team/project scope are
        // meaningless for every role, owner included.
        for action in EnterpriseAction.allCases {
            XCTAssertTrue(
                PermissionMatrix.allowed(.owner, action: action,
                                         scope: .organization),
                "owner refused \(action)/organization")
        }
        for scope in EnterpriseScope.allCases {
            XCTAssertTrue(PermissionMatrix.allowed(
                .owner, action: .capturePhoto, scope: scope))
            XCTAssertTrue(PermissionMatrix.allowed(
                .owner, action: .readHistory, scope: scope))
        }
    }

    func test_deny_by_default_unlisted_pairs_refuse() {
        // viewer: anything but readHistory/runReport refuses
        for action in EnterpriseAction.allCases
        where action != .readHistory && action != .runReport {
            XCTAssertFalse(
                PermissionMatrix.allowed(.viewer,
                                          action: action,
                                          scope: .project))
        }
        // member: no close, no delete
        XCTAssertFalse(PermissionMatrix.allowed(
            .member, action: .closeIssue, scope: .project))
        XCTAssertFalse(PermissionMatrix.allowed(
            .member, action: .deletePhoto, scope: .project))
        // inspector: no delete
        XCTAssertFalse(PermissionMatrix.allowed(
            .inspector, action: .deletePhoto, scope: .project))
        // admin: no viewer-only gap - admins act, they don't read?
        // admins read too (matrix lists readHistory for admin).
    }

    func test_org_actions_require_org_scope() {
        for role in EnterpriseRole.allCases {
            XCTAssertFalse(PermissionMatrix.allowed(
                role, action: .manageBilling, scope: .project))
            XCTAssertFalse(PermissionMatrix.allowed(
                role, action: .assignRoles, scope: .team))
            XCTAssertTrue(PermissionMatrix.allowed(
                .owner, action: .manageBilling, scope: .organization))
        }
    }

    func test_grants_list_matches_allowed() {
        let memberGrants = PermissionMatrix.grants(role: .member,
                                                  scope: .project)
        XCTAssertEqual(Set(memberGrants), [
            .capturePhoto, .createIssue, .commentIssue, .readHistory,
        ])
        let viewerGrants = PermissionMatrix.grants(role: .viewer,
                                                   scope: .project)
        XCTAssertEqual(Set(viewerGrants), [.readHistory, .runReport])
    }

    func test_inspector_closes_but_member_cannot() {
        XCTAssertTrue(PermissionMatrix.allowed(
            .inspector, action: .closeIssue, scope: .project))
        XCTAssertFalse(PermissionMatrix.allowed(
            .member, action: .closeIssue, scope: .project))
    }
}

final class TeamBookkeepingTests: XCTestCase {

    private func m(_ id: String, _ role: EnterpriseRole,
                   team: String = "t1") -> TeamMembership {
        TeamMembership(organizationID: "org", teamID: team,
                       memberID: id, role: role,
                       assignedAt: Date(timeIntervalSince1970: 0))
    }

    func test_removing_the_last_owner_is_refused_by_name() throws {
        let members = [m("o1", .owner)]
        XCTAssertThrowsError(
            try TeamBookkeeping.removeMember(
                members[0], from: members)) { error in
            XCTAssertEqual(
                error as? TeamBookkeepingError,
                .lastOwnerIrremovable("o1"))
        }
    }

    func test_removing_an_owner_with_a_copilot_succeeds() throws {
        let members = [m("o1", .owner), m("o2", .owner)]
        let remaining = try TeamBookkeeping.removeMember(
            members[0], from: members)
        XCTAssertEqual(remaining.map(\.memberID), ["o2"])
    }

    func test_removing_a_non_owner_is_ordinary() throws {
        let members = [m("o1", .owner), m("v1", .viewer)]
        let remaining = try TeamBookkeeping.removeMember(
            members[1], from: members)
        XCTAssertEqual(remaining.map(\.memberID), ["o1"])
    }

    func test_viewer_cannot_assign_roles() throws {
        let members = [m("o1", .owner), m("v1", .viewer)]
        XCTAssertThrowsError(
            try TeamBookkeeping.assignRole(
                to: "v1", in: "t1", newRole: .member,
                actingAs: .viewer, members: members)) { error in
            XCTAssertEqual(
                error as? TeamBookkeepingError,
                .roleEscalationRefused(from: .viewer, to: .member))
        }
    }

    func test_owner_role_is_never_granted_via_assign() {
        let members = [m("o1", .owner), m("a1", .admin)]
        XCTAssertThrowsError(
            try TeamBookkeeping.assignRole(
                to: "a1", in: "t1", newRole: .owner,
                actingAs: .owner, members: members))
    }

    func test_admin_promotes_member_to_inspector() throws {
        let members = [m("o1", .owner), m("m1", .member)]
        let updated = try TeamBookkeeping.assignRole(
            to: "m1", in: "t1", newRole: .inspector,
            actingAs: .admin, members: members)
        XCTAssertEqual(updated.first {
            $0.memberID == "m1" }?.role, .inspector)
    }

    func test_unknown_member_refused_by_name() {
        let members = [m("o1", .owner)]
        XCTAssertThrowsError(
            try TeamBookkeeping.assignRole(
                to: "ghost", in: "t1", newRole: .member,
                actingAs: .owner, members: members)) { error in
            XCTAssertEqual(
                error as? TeamBookkeepingError,
                .unknownMember("ghost"))
        }
    }
}

final class InvitationTests: XCTestCase {

    private func minted(ttl: TimeInterval = 86400,
                        role: EnterpriseRole = .member)
            throws -> TeamInvitation {
        try InvitationMint.mint(
            teamID: "t1", role: role, projectID: "p1",
            invitedBy: .admin, ttlSeconds: ttl,
            at: Date(timeIntervalSince1970: 1_000),
            uuidProvider: SequentialUUIDProvider(prefix: "t"))
    }

    func test_mint_requires_owner_or_admin_inviter() {
        for role in EnterpriseRole.allCases
        where role != .owner && role != .admin {
            XCTAssertThrowsError(
                try InvitationMint.mint(
                    teamID: "t", role: .member, projectID: nil,
                    invitedBy: role, ttlSeconds: 60,
                    at: Date(), uuidProvider: SequentialUUIDProvider())
            ) { error in
                XCTAssertEqual(
                    error as? InvitationMint.MintError,
                    .inviterCannotInvite(role))
            }
        }
        XCTAssertNoThrow(try minted())
    }

    func test_owner_seat_is_never_mintable_via_invite() {
        XCTAssertThrowsError(try minted(role: .owner))
    }

    func test_accept_within_ttl_is_single_use() throws {
        var inv = try minted(ttl: 100)
        XCTAssertNoThrow(try inv.accept(
            as: "newbie", now: Date(timeIntervalSince1970: 1050)))
        // second accept refused (terminal)
        XCTAssertThrowsError(try inv.accept(
            as: "newbie", now: Date(timeIntervalSince1970: 1060)))
            { error in
            XCTAssertEqual(
                error as? TeamInvitation.AcceptError,
                .notPending("accepted"))
        }
    }

    func test_accept_after_expiry_is_named_error() throws {
        var inv = try minted(ttl: 10)
        XCTAssertThrowsError(try inv.accept(
            as: "n", now: Date(timeIntervalSince1970: 1_050))) {
            error in
            XCTAssertEqual(
                error as? TeamInvitation.AcceptError,
                .expiredAt(Date(timeIntervalSince1970: 1_010),
                           now: Date(timeIntervalSince1970: 1_050)))
        }
        XCTAssertEqual(inv.state, .expired)
    }

    func test_revoke_is_terminal_for_the_token() throws {
        var inv = try minted()
        inv.revoke()
        XCTAssertEqual(inv.state, .revoked)
        XCTAssertThrowsError(try inv.accept(
            as: "n", now: Date(timeIntervalSince1970: 1_100)))
        // revoke after revoke: still revoked (idempotent no-op)
        inv.revoke()
        XCTAssertEqual(inv.state, .revoked)
    }

    func test_sweep_expires_pending_only() throws {
        var inv = try minted(ttl: 5)
        inv.sweepExpire(now: Date(timeIntervalSince1970: 1_003))
        XCTAssertEqual(inv.state, .pending)     // not yet
        inv.sweepExpire(now: Date(timeIntervalSince1970: 1_020))
        XCTAssertEqual(inv.state, .expired)
        var accepted = try minted(ttl: 5)
        _ = try? accepted.accept(
            as: "x", now: Date(timeIntervalSince1970: 1_001))
        accepted.sweepExpire(now: Date(timeIntervalSince1970: 1_020))
        XCTAssertEqual(accepted.state, .accepted)   // terminal stays
    }
}
