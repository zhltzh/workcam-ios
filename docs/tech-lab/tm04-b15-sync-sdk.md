# WorkCam Sync SDK — TM-04 B15 Delivery

Branch: `dev/windows-tm04` · All components below are **compiled and
unit-tested on the real Windows Swift 6.4.3 toolchain** (140-test
suite, 0 failures) plus the Python parity/concurrency suites
(25 + 7 passed).

## Component map (all in `swift/WorkCamCore/Sources/WorkCamSync/`)

| Component | File | What it owns | Tests |
|---|---|---|---|
| OutboxEngine + state machine | `Outbox.swift` | pending→sending→acknowledged/failed/conflict; dependency ordering; restart recovery; rebase resubmission | OutboxRuleTests 7/7 |
| Backoff + clocks | `Outbox.swift` | bounded exponential backoff, jitter ratio, Fixed/MutableClock | OutboxRuleTests |
| Field-level 3-way merge | `FieldDiff.swift` | identical/independent/conflicting; only real conflicts reach users | FieldDiffTests 8/8 |
| Conflict decisions | `ConflictResolution.swift` | keepLocal (CAS-stamped), takeServer (audit keeps payload), manualMerge, deferred | ConflictResolutionTests 7/7 |
| HTTP recovery table | `SyncRecovery.swift` | 401/403/409/429/5xx/4xx routing + Retry-After clamp + dead-letter | SyncRecoveryTests 9/9 |
| UUID mappings | `SyncBookkeeping.swift` | versioned local↔server ids; duplicate-association refusals | SyncBookkeepingTests |
| Lifecycle semantics | `SyncBookkeeping.swift` | tombstone/archive/closed-terminal rules | SyncBookkeepingTests |
| Cursor state | `SyncBookkeeping.swift` | per-owner+entity positions, atomic persistence, anti-rewind | SyncBookkeepingTests |
| Intent audit | `IntentAndImport.swift` | stale-op rebind with NEW ids + supersession trail | IntentImportTests |
| Import planner | `IntentAndImport.swift` | createNew/linkExisting/needsDecision partition | IntentImportTests |
| Remediation links | `IntentAndImport.swift` | CAS-gated before/progress/after events, withdraw = recorded unlink | IntentImportTests |
| Transport + persistence seams | `Outbox.swift` + `Repository.swift` | SyncTransport / OutboxStore / repositories / ServiceRegistry DI | RepositoryTests |
| Atomic files | `AtomicFile.swift` | temp+swap, read-back verification, stale-temp cleanup | AtomicFileTests |
| Batching scheduler | `TransactionAndScheduler.swift` | Kahn topological batches, cycle refusal | SyncSchedulerTests |
| Transactions | `TransactionAndScheduler.swift` | staged visibility, rollback zero-residue | TransactionTests |

## Swift intake (the 10-line path from engine to UI)

```swift
import WorkCamSync

let clock = MutableClock()
let engine = OutboxEngine(
    store: InMemoryOutboxStore(),          // replace with the
                                           // SQLite adapter
    backoff: ExponentialBackoff(jitterRatio: 0.2),
    clock: clock)

// 1. queue an edit (idempotency key = operationID)
try engine.enqueue(OutboxOperation(
    operationID: "op-\(uuid.next())", op: "project_update",
    payloadJSON: payload))

// 2. drain on your schedule (UI calls this on foreground/timer)
let receipts = try await engine.drain(transport: realTransport)

// 3. route failures - one status, one policy
for r in receipts where r.status == "rejected" {
    switch SyncRecovery.decision(status: httpStatus(r),
                                 attemptCount: attempts) {
    case .rebaseConflict:      // -> ConflictResolution.apply(...)
        break
    case .retryAfter(let s):  // -> clock/bounded wait, then drain
        break
    default: break
    }
}
```

## Failure matrix (who handles what, with named refusals)

| Failure | Layer | Behavior | Red line |
|---|---|---|---|
| Transport throws | OutboxEngine.drain | back to pending, attempts+1, backoff nextAttemptAt | never counts as a server decision |
| HTTP 401 | SyncRecovery | reauthenticate once, replay via the ledger | token refresh is app-side |
| HTTP 403 | SyncRecovery | escalatePermission - NO retry loop | surface to the user |
| HTTP 409 / revision_conflict | ConflictResolution | typed rebase path with base_revision stamp; user picks keepLocal/takeServer/manualMerge/deferred | never auto-picked, never silent |
| idempotency_conflict | ConflictResolution | reapplyUnderNewID (new id) or user decision | the first decision stands |
| HTTP 429 / 5xx | SyncRecovery | bounded backoff; Retry-After honored clamped to [0, 300]s | a broken header cannot park the queue |
| attempts exhausted | SyncRecovery | deadLetter(reason) | ops are flagged, never deleted |
| Upload session interrupted | server TM35 + `test_tm91_weak_network.py` (inherited, DESKTOP_VERIFIED) | resume from received chunks; duplicate chunks acknowledged; server restart survives; out-of-order tolerated | B14 carries this as the continuation item |
| Stale intent after rebind | IntentAudit | NEW operation id + supersession record; dependents follow | old receipts untouched (append-only) |
| Name-collision import | ImportPlanner | needsDecision surfaces to the user | never auto-creates over a clash |
| Cross-project photo link | RemediationLinkSemantics | named refusal before the op enters the queue | server CAS is the second gate |
| Process kill mid-drain | recoverAfterRestart + server ledger | sending ops return to pending; replay answers duplicate | exactly-once convergence |

## Verification ledger (this sprint)

- Swift suite on Windows: **140 tests, 0 failures** (`swift test`,
  exit 0) — includes every component table above
- Python parity of decision semantics: 25 passed
  (`test_tm04_sync_core_parity.py`)
- Real-backend concurrency: 7 passed twice
  (`test_tm04_b13_multiclient.py`) — same-revision race, idempotency,
  cross-session convergence, owner isolation
- Full backend regression: 1663 passed / 0 failed; gate 12/12

## Known limits (honest)

- No real-network integration in this SDK document: the harness runs
  the in-process FastAPI app (loopback only). On-device weak-network
  behavior inherits TM91's fault-transport discipline for app builds.
- `MutableClock`/`SequentialUUIDProvider` are test doubles, not
  production clocks.
- SwiftUI reference surfaces are Apple-only behind
  `#if canImport(SwiftUI)`; Windows compiles everything else.
- B14's upload-session matrix (expiry/cancel/duplicate-chunk
  consolidation on top of TM91/TM35) remains the open continuation.
