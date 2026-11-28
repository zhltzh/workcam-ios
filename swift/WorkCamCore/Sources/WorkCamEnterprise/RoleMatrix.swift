// TM-04 H02/H03 (EXPERIMENTAL enterprise reserve, P2): the role
// permission matrix for enterprise/team/project scopes. Real
// Windows compile + tests - but NOTHING here ships in RC4 (J08)
// or touches the stable single-user product.
//
// Role model (task sheet H03):
//   Owner      - org-level; manages billing/roles (no email here)
//   Admin      - org + all projects; manages members, cannot
//                remove the last Owner (guard in TeamBookkeeping)
//   Inspector  - per-project: full capture + issue lifecycle
//                INCLUDING closing issues
//   Member     - per-project: capture + comment; may NOT close
//                issues or delete photos
//   Viewer     - read-only everywhere, including history
//
// RED LINES encoded:
//   - deny-by-default: an action not listed for a role is REFUSED
//   - the last Owner is irremovable (org lockout guard)
//   - Viewers read history but never write, never even propose
//   - photo deletion is Owner/Admin-only (originals discipline)

import Foundation
import WorkCamModels

public enum EnterpriseRole: String, Codable, Sendable, Equatable,
                            CaseIterable {
    case owner
    case admin
    case inspector
    case member
    case viewer
}

public enum EnterpriseScope: String, Codable, Sendable, Equatable,
                              CaseIterable {
    case organization
    case team
    case project
}

public enum EnterpriseAction: String, Sendable, Equatable,
                               CaseIterable {
    // org level
    case manageBilling = "manage_billing"
    case assignRoles = "assign_roles"
    case removeMember = "remove_member"
    // project level
    case capturePhoto = "capture_photo"
    case deletePhoto = "delete_photo"
    case createIssue = "create_issue"
    case closeIssue = "close_issue"
    case commentIssue = "comment_issue"
    case runReport = "run_report"
    case readHistory = "read_history"
}

public enum PermissionMatrix {

    /// The full matrix. Deny-by-default: `allowed` is the single
    /// source and anything absent is a refusal.
    public static func allowed(_ role: EnterpriseRole,
                               action: EnterpriseAction,
                               scope: EnterpriseScope) -> Bool {
        // scope preconditions: org-level actions need org scope
        switch action {
        case .manageBilling, .assignRoles, .removeMember:
            guard scope == .organization else { return false }
        default:
            break
        }
        switch (role, action) {
        case (.owner, _):
            return true        // owner does everything, any scope
        case (.admin, .capturePhoto), (.admin, .deletePhoto),
             (.admin, .createIssue), (.admin, .closeIssue),
             (.admin, .commentIssue), (.admin, .runReport),
             (.admin, .readHistory):
            return true
        case (.inspector, .capturePhoto), (.inspector, .createIssue),
             (.inspector, .closeIssue), (.inspector, .commentIssue),
             (.inspector, .runReport), (.inspector, .readHistory):
            return true        // note: NO deletePhoto
        case (.member, .capturePhoto), (.member, .createIssue),
             (.member, .commentIssue), (.member, .readHistory):
            return true        // no close, no delete
        case (.viewer, .readHistory), (.viewer, .runReport):
            return true       // read-only (reports are reads)
        default:
            return false       // deny-by-default
        }
    }

    /// All actions a role may take in a scope (for UI affordances -
    /// the UI hides what the matrix refuses).
    public static func grants(role: EnterpriseRole,
                              scope: EnterpriseScope)
            -> [EnterpriseAction] {
        EnterpriseAction.allCases.filter {
            allowed(role, action: $0, scope: scope)
        }
    }
}

// MARK: - H02: enterprise/team/project membership records

public struct TeamMembership: Codable, Sendable, Equatable {
    public let organizationID: String
    public let teamID: String
    public let memberID: String
    public let role: EnterpriseRole
    public let assignedAt: Date
    /// Nil = the whole team; otherwise the membership is scoped to
    /// ONE project (inspectors/members/viewers are project-scoped).
    public let projectID: String?

    public init(organizationID: String, teamID: String,
                memberID: String, role: EnterpriseRole,
                assignedAt: Date, projectID: String? = nil) {
        self.organizationID = organizationID
        self.teamID = teamID
        self.memberID = memberID
        self.role = role
        self.assignedAt = assignedAt
        self.projectID = projectID
    }
}

public enum TeamBookkeepingError: Error, Equatable {
    case lastOwnerIrremovable(String)
    case unknownMember(String)
    case roleEscalationRefused(from: EnterpriseRole,
                               to: EnterpriseRole)
}

public enum TeamBookkeeping {

    /// H02/H03 red line: removing the LAST owner locks the org out
    /// of administration forever - refused by name.
    public static func removeMember(
        _ target: TeamMembership,
        from members: [TeamMembership]) throws -> [TeamMembership] {
        var remaining = members.filter {
            !($0.memberID == target.memberID
              && $0.teamID == target.teamID)
        }
        if target.role == .owner {
            let ownersLeft = remaining.filter {
                $0.role == .owner && $0.teamID == target.teamID
            }
            if ownersLeft.isEmpty {
                throw TeamBookkeepingError.lastOwnerIrremovable(
                    target.memberID)
            }
        }
        return remaining
    }

    /// Only owners/admins assign roles, and NOBODY grants an owner
    /// role through the member path (owner transitions are an
    /// org-level transfer protocol, deliberately not modeled here).
    public static func assignRole(
        to memberID: String, in teamID: String,
        newRole: EnterpriseRole,
        actingAs actor: EnterpriseRole,
        members: [TeamMembership]) throws -> [TeamMembership] {
        guard actor == .owner || actor == .admin else {
            throw TeamBookkeepingError.roleEscalationRefused(
                from: actor, to: newRole)
        }
        guard newRole != .owner else {
            // owner grants happen only via the org-transfer protocol
            throw TeamBookkeepingError.roleEscalationRefused(
                from: .admin, to: .owner)
        }
        guard members.contains(where: {
            $0.memberID == memberID && $0.teamID == teamID })
        else {
            throw TeamBookkeepingError.unknownMember(memberID)
        }
        return members.map { m in
            m.memberID == memberID && m.teamID == teamID
                ? TeamMembership(
                    organizationID: m.organizationID,
                    teamID: m.teamID, memberID: m.memberID,
                    role: newRole, assignedAt: m.assignedAt,
                    projectID: m.projectID)
                : m
        }
    }
}
