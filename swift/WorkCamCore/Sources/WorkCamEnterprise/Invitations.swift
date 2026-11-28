// TM-04 H04 (EXPERIMENTAL enterprise reserve): team invitation
// lifecycle. NO email is sent anywhere - the invite TOKEN is the
// deliverable; delivery transport is out of scope by directive.
//
// Lifecycle:  pending -> accepted | expired | revoked
//   - expiry is checked against an injected clock (no wall clock)
//   - revoked/expired/accepted are TERMINAL for that token; a
//     re-invite mints a NEW token (old receipts stay)
//   - a token binds role + project scope at MINT time: the invite
//     can never grant more than the matrix allows for that role
//   - single-use: accepting twice is a refusal, not a refresh

import Foundation
import WorkCamModels
import WorkCamSync       // UUIDProvider (the C14 injection seam)

public struct TeamInvitation: Codable, Sendable, Equatable {
    public enum State: String, Codable, Sendable, Equatable {
        case pending
        case accepted
        case expired
        case revoked
    }

    public let token: String
    public let teamID: String
    public let role: EnterpriseRole
    public let projectID: String?       // nil = team-wide
    public let invitedBy: String
    public let mintedAt: Date
    public let expiresAt: Date
    public private(set) var state: State

    public init(token: String, teamID: String,
                role: EnterpriseRole, projectID: String?,
                invitedBy: String, mintedAt: Date,
                expiresAt: Date) {
        self.token = token
        self.teamID = teamID
        self.role = role
        self.projectID = projectID
        self.invitedBy = invitedBy
        self.mintedAt = mintedAt
        self.expiresAt = expiresAt
        self.state = .pending
    }

    public enum AcceptError: Error, Equatable {
        case notPending(String)
        case expiredAt(Date, now: Date)
        case roleCannotAccept(EnterpriseRole)
    }

    /// Single-use accept with injected clock.
    public mutating func accept(as memberID: String,
                                now: Date) throws {
        guard state == .pending else {
            throw AcceptError.notPending(state.rawValue)
        }
        guard now <= expiresAt else {
            state = .expired
            throw AcceptError.expiredAt(expiresAt, now: now)
        }
        state = .accepted
    }

    public mutating func revoke() {
        if state == .pending { state = .revoked }
    }

    /// Mark-as-expired pass (sweep): idempotent for anything that
    /// is not already terminal.
    public mutating func sweepExpire(now: Date) {
        if state == .pending && now > expiresAt {
            state = .expired
        }
    }
}

public enum InvitationMint {

    public enum MintError: Error, Equatable {
        /// Viewers cannot invite (deny-by-default at the seam).
        case inviterCannotInvite(EnterpriseRole)
    }

    /// Mint a fresh invite token. The invited role can never be
    /// .owner (the org-transfer protocol owns that path) and the
    /// inviter must be owner/admin per the H03 matrix.
    public static func mint(
        teamID: String, role: EnterpriseRole, projectID: String?,
        invitedBy actingRole: EnterpriseRole, ttlSeconds: TimeInterval,
        at mintedAt: Date,
        uuidProvider: UUIDProvider) throws -> TeamInvitation {
        guard actingRole == .owner || actingRole == .admin else {
            throw MintError.inviterCannotInvite(actingRole)
        }
        guard role != .owner else {
            // owner seats are transferred, never invited
            throw MintError.inviterCannotInvite(.owner)
        }
        return TeamInvitation(
            token: "inv-" + uuidProvider.next(),
            teamID: teamID, role: role, projectID: projectID,
            invitedBy: "actor", mintedAt: mintedAt,
            expiresAt: mintedAt.addingTimeInterval(ttlSeconds))
    }
}
