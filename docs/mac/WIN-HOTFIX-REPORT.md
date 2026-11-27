# WorkCam Windows emergency candidate

2026-11-27. Code SHA `2a53712a179ad8880f75cc72d1762fb5b14ec0a8`, parent `2b535a0`.
Candidate branch `codex/workcam-windows-hotfix-01`. Mac baseline PR #24,
`53440fe` and report `33cc1ca`. No main merge or forced push.

## WIN-HOTFIX-01

The enrollment transaction used BEGIN IMMEDIATE and replay entered a second
audit write transaction. The second connection waited until SQLite's busy
timeout. Enrollment and replay now insert redacted audit rows using the
same connection. An enrollment audit failure rolls back the enrollment and
remains retryable. Same-key re-enrollment cannot bypass suspension.

Measured on macOS arm64, Python 3.12.16, synthetic key and disposable local
DB, nearest-rank percentiles in milliseconds:

| Run | Replays | First enrollment | P50 | P95 | P99 |
|---|---:|---:|---:|---:|---:|
| Fixed parent | 3 | 1.518 | 31134.481 | 31198.812 | 31198.812 |
| Candidate | 100 | 1.478 | 0.576 | 0.854 | 1.055 |

Three pre-fix observations show the lock plateau. They do not characterize
the tails of a production workload. A separate loopback HTTP replay after
the fix was 1.68 ms. The regression verified 16 concurrent same-key calls,
separate keys, audit cardinality and redaction, suspend/resume, injected
audit failure rollback and retry. The full suite includes key rotation,
historical migrations and security cases.

## WIN-HOTFIX-02 / 03

Added versioned project archive, protected role replacement, cursor pages
over matching rows, opt-in current snapshots and annotation tombstones.
Rejected batches roll back partial writes and preserve the original error
across replays. Direct project rename and archive now bump revision and
emit an event. The legacy direct REST role API still has its historical
replacement semantics. Full Mac client integration and two physical
devices replaying simultaneously remain unverified.

[Mac handoff](WIN-HOTFIX-HANDOFF.md) maps the PR #24 client to routes,
errors, credentials, conflicts, cursor and uploads. Generated OpenAPI is
75 paths / 88 operations. Standalone Swift Codable decoded the archive,
rejection replay, cursor page and sparse chunk fixture.

## WIN-HOTFIX-04 verification

- Full backend pytest: **1163 passed, 3 skipped, 0 failed** in 97.71 s.
  FastAPI/Starlette emitted one test-client deprecation warning.
- Targeted gate: **56 passed**.
- Actual loopback HTTP: enrollment, same-key replay, challenge, operation
  replay, chunk resume after server process restart, integrity, issue
  conflict, archive and incremental pull passed.
- Host used macOS arm64. The initial full run exposed Windows-only test
  assumptions. The startup test now uses the running Python interpreter
  and the export peak RSS helper retains the Windows implementation with
  a portable POSIX path. No Windows-native runner or iOS device was used.
- Reproduction commands are in the handoff. Logs are local in
  `/tmp/workcam-win-hotfix-green.log` and `/tmp/workcam-win-hotfix-final-live.log`.
