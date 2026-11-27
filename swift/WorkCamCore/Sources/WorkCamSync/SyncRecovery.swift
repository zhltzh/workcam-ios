// TM-04 B11: differentiated recovery for HTTP failures. One status
// code -> ONE policy; no blanket "retry everything".
//
//   401 -> reauthenticate (the device token died; one refresh, then
//          the op replays through the idempotency ledger)
//   403 -> escalatePermission (retrying cannot grant rights; surface
//          to the user, op stays pending untouched)
//   409 -> rebaseConflict (route into the B02/B03 conflict flow)
//   429 -> retryAfter: honor the server's Retry-After when present,
//          otherwise bounded exponential backoff (B12)
//   500/502/503 -> retryAfter: server-side transient, backoff
//   other 4xx -> failFast (client bug or permanent rejection; the
//          op keeps its error and waits for a human)
//   attempts >= maxAttempts -> deadLetter (NEVER deleted; flagged
//          for intervention)
//
// Wall-clock deltas are reported by callers, never asserted by this
// engine (the two-flakes rule): the only contract here is the
// DECISION, which is deterministic.

import Foundation

public enum RecoveryDecision: Equatable, Sendable {
    case reauthenticate
    case escalatePermission
    case rebaseConflict
    case retryAfter(hintSeconds: Double?)
    case failFast
    case deadLetter(reason: String)
}

public enum SyncRecovery {
    public static let maxAttempts = 8

    /// Deterministic decision table. `attemptCount` is the number of
    /// failed attempts already recorded on the operation.
    public static func decision(
        status: Int,
        attemptCount: Int,
        retryAfterSeconds: Double? = nil,
        maxAttempts: Int = SyncRecovery.maxAttempts
    ) -> RecoveryDecision {
        if attemptCount >= maxAttempts {
            return .deadLetter(
                reason: "exhausted \(maxAttempts) attempts " +
                        "(last status \(status))")
        }
        switch status {
        case 401:
            return .reauthenticate
        case 403:
            return .escalatePermission
        case 409:
            return .rebaseConflict
        case 429, 500, 502, 503:
            return .retryAfter(hintSeconds: retryAfterSeconds)
        case 400...499:
            return .failFast
        default:
            // 2xx/3xx never reach recovery; anything else (odd
            // gateways, 1xx leftovers) is treated transient.
            return .retryAfter(hintSeconds: retryAfterSeconds)
        }
    }

    /// Clamp a Retry-After hint into the backoff envelope so a
    /// hostile/broken server header cannot park the queue for hours.
    /// Bounded: [0, 300] seconds (maxSeconds of the standard
    /// ExponentialBackoff).
    public static func clampedRetryAfter(
        _ hint: Double?, maxSeconds: Double = 300) -> Double? {
        guard let h = hint, h.isFinite else { return nil }
        return min(max(0, h), maxSeconds)
    }
}
