# WorkCam TECH-MARATHON-04 — 120-item execution queue

Version: 2026-11-28 / ACTIVE
Owner: Windows main development session (TM-04, continuous autonomous run)
Source of truth: this task sheet and `TECH-MARATHON-04-STATUS.json`
Baseline: `dev/windows-tm-geometry` HEAD `a8b59ed` (TM-03 final; RC3 frozen tree `1f645b6`, 1602 passed / 3 skipped / 0 failed, gate 12/12)
Development branch: `dev/windows-tm04` (created from `a8b59ed`; RC3 line untouched)
Mac reference: PR #27 `c6c6eda` · Division of labor: PR #28 · Historical task management: PR #25

## Instructions

Continue one session, no duplicate implementations, no feature-per-task branches.
120 items are engineering work packages, not 120 new product features. Inherit
TM-01/02/03, TM01–TM100 and W-X01–W-X10 verified results; reconcile each item
against actual code and tests before touching it. Already DESKTOP_VERIFIED items
must not be redone. Keep the machine-readable matrix (`TECH-MARATHON-04-STATUS.json`)
as the single status source; every state change cites a real commit SHA and a
real test file. Preserve originals and original evidence. No force push, no
automatic merge into main, no public deployment, no paid services, no fabricated
tests or Swift compiles (Windows toolchain is BLOCKED — Swift stays
CROSS_PLATFORM_PENDING/XCODE_PENDING). Wall-clock ordering assertions are
forbidden (two flake root-causes); timing may be reported, never gated — assert
functional invariants only. New versions must re-run `tools/quality_gate.py`.

## A — historical closeout & Mac ready, 10 (P0)
- A01 Verify the RC3 freeze: checklist, code SHA, remote commits, test evidence; keep later work off the frozen tree.
- A02 Reconcile the original 80-item matrix and TM01–TM100; auto-generate remaining-task and dependency lists.
- A03 Reconcile W-X01–W-X10 actual delivery state; complete missing implementations first.
- A04 Check the official Windows Swift toolchain, SwiftPM, compiler version and real run capability (honest re-check).
- A05 Fold all cross-platform Swift components into the single WorkCamCore package; eliminate duplicate models.
- A06 Run Windows-side syntax/dependency/interface/test-vector/platform-isolation checks for every XCODE_PENDING component.
- A07 Build Mac one-command intake and one-command test scripts with pinned dependency versions, no personal temp dirs.
- A08 Document every component's real API, examples, data formats, error semantics and migration path.
- A09 Generate machine-readable `handoff/mac-ready/integration-manifest.json`: source paths, SHAs, dependencies, test results, pending-verification states.
- A10 Deliver a complete `handoff/mac-ready/` so Mac needs no long history reading to integrate.

## B — bidirectional sync & conflict resolution, 15 (P0)
- B01 Inherit the Mac Super 02 Outbox behavior; extract the reusable pure-Swift state machine.
- B02 Conflict-resolution core: keep-local, take-server, manual merge, defer.
- B03 Field-level diff: identical edits, independent edits, true conflicts.
- B04 Stale-intent audit, new operation IDs, dependency rebinding, historical receipt retention.
- B05 Versioned local-UUID ↔ server-UUID mapping; no duplicate association.
- B06 Server project import plan: preview, confirm, duplicate-import idempotency.
- B07 Bidirectional sync core for remediation content/status/role links.
- B08 Before/Progress/After replace, withdraw, revert and CAS conflict tests.
- B09 Sync semantics for delete tombstones, archive, closed-terminal states and history.
- B10 Full and filtered cursors, pagination, atomic commit, resume from breakpoint.
- B11 Differentiated recovery for HTTP 401/403/409/429/500/502/503.
- B12 Retry-After, bounded exponential backoff, testable clock, random jitter.
- B13 Multi-client concurrency test harness covering same-entity simultaneous edits.
- B14 Upload session expiry, cancel, duplicate chunks, server restart, process-kill recovery.
- B15 Deliver the complete sync SDK: real-HTTP tests, Swift intake example, failure matrix.

Forbidden: silent overwrite of user edits, treating duplicate-rejection as success, deleting originals.

## C — Swift common core components, 15 (P0)
- C01 Unified WorkCamCore module boundaries and dependency direction.
- C02 Unified Project/Photo/Evidence/Issue/Measurement/Report data models.
- C03 Unified Repository protocols and dependency injection.
- C04 Testable local-persistence transaction abstraction.
- C05 Atomic file write, verification and crash-recovery component.
- C06 Evidence directory index, pagination and incremental scan.
- C07 Derived-image cache, LRU, byte budget and invalidation.
- C08 Type-safe API client, request cancellation and error model.
- C09 Sync task scheduler and dependency graph.
- C10 Cross-platform evidence protocol parsing, version detection, canonical serialization.
- C11 Time anchors, monotonic clock abstraction, uncertainty intervals.
- C12 Location quality assessment, source classification, anomaly hints.
- C13 Unified permission-state, feature-availability and offline-state models.
- C14 Injectable clock, filesystem, network, UUID and test-data generators.
- C15 Cross-platform Swift unit tests and Python/Swift/OpenSSL fixed-vector cross-checks.

Components that Windows cannot compile stay CROSS_PLATFORM_PENDING or XCODE_PENDING. Never pass Python tests off as Swift compiles.

## D — engineering business productization, 15
- D01 Close out the eight English engineering project templates.
- D02 Dynamic engineering form schema.
- D03 Checklist rules engine.
- D04 Required-photo, note and measurement link validation.
- D05 Remediation state machine: severity, owner, deadline, history.
- D06 Before/Progress/After photo complete linkage.
- D07 Area/floor/room/location-label models.
- D08 Engineering photo naming rules and path-safety validation.
- D09 Batch classification, tagging, notes and report selection.
- D10 Unified engineering search index and pagination.
- D11 Daily engineering activity digest and work-log candidates.
- D12 Equipment/asset record independent candidates.
- D13 Measurement records: raw points, algorithm version, calibration models.
- D14 Project archive, restore strategy and history queries.
- D15 Package all business components as directly-adoptable Swift Models/Services/ViewModels with tests.

Business rules must match existing Mac and the fixed backend. Unconfirmed enterprise business and new states stay experimentally isolated.

## E — image processing & measurement algorithms, 15
- E01 All 8 EXIF orientation conversions and coordinate tests.
- E02 Watermark layout, font size, margins, background, opacity, logo.
- E03 Geometry for the six annotation tools.
- E04 Annotation selection, hit testing, drag, scale, undo/redo.
- E05 Blur/exposure/contrast/resolution quality advisories.
- E06 SHA-256 and perceptual-hash duplicate photo candidates.
- E07 (was TM-03 C06) Image auto-registration selection review + isolated PoC on real synthetic transforms.
- E08 (was TM-03 C07) Before/After side-by-side, slider, opacity and manual-align core logic.
- E09 (was TM-03 C08) OCR selection, engineering-label recognition, human-confirmation data structures.
- E10 Privacy-redaction derived-image processing; originals untouched.
- E11 3D coordinate transforms, two-point distance, horizontal/vertical distance.
- E12 Multi-segment distance, angles, slope.
- E13 Polygon area, coplanarity, degenerate and self-intersection checks.
- E14 Plane fitting, point-cloud filtering and volume PoC.
- E15 Measurement uncertainty, calibration statistics, per-mode error analysis, Swift fixed vectors.

Real ARKit/LiDAR capture stays Mac. Never claim measurement accuracy without physical-device validation. OCR/auto-registration/volume must not leak into stable production modules.

## F — professional reports & evidence delivery, 10
- F01 Unified data-assembly engine for the six report types.
- F02 Twelve real PDF samples: six types × A4/Letter.
- F03 Complex pagination, long text, tables, photo-ratio handling.
- F04 Company logo, report ID, date-range and template configuration.
- F05 Evidence ZIP V2 manifest.
- F06 Streaming ZIP64, large-file chunking, cancellation, memory budget.
- F07 ZIP safety: path traversal, duplicates, zip bombs, corrupt archives.
- F08 W018 standalone verifier and offline browser-verification tool.
- F09 JSON/CSV/GeoJSON/KML/measurement open formats.
- F10 C2PA, RFC3161, long-term signature experiments and applicability report.

Hash, signature, device origin, time and location shown separately. No fake unified trust score, no legal-effect promises.

## G — performance, stability & security, 10
- G01 Fixed-seed 100k engineering-metadata test set.
- G02 10k-photo scale directory/index/cache/search tests.
- G03 SQLite concurrency, lock contention, pagination, migration performance.
- G04 Chunked-upload throughput, weak network, resume, memory.
- G05 100/500/1000-photo report-generation stress.
- G06 Large-file ZIP64 streaming export stress.
- G07 5-minute and 30-minute tiered soak beyond the 30-second base.
- G08 Disk-full, permission, process-kill, corruption, DB-lock injections.
- G09 Local-security suite, parser fuzzing, sensitive-info scanning.
- G10 Repeatable performance baselines and regression gates (P50/P95/P99, error rate, resource growth).

Record real host, config, sample size and duration per test. Desktop results are never iPhone results.

## H — enterprise reserves, 10 (P2, isolated, never blocks pro launch)
- H01 Multi-tenant isolated data model.
- H02 Enterprise/team/project permission matrix.
- H03 Owner/Admin/Inspector/Member/Viewer rules.
- H04 Team invitation lifecycle and revocation; no real email.
- H05 Enterprise project sharing and access-control experiments.
- H06 Enterprise audit records and traceable operation history.
- H07 Multi-user edit conflict and data-isolation experiments.
- H08 Enterprise report templates and branding.
- H09 Enterprise backup, restore and data portability.
- H10 Cross-tenant security tests, isolation gates, future deployment architecture.

Never swap the experimental multi-tenant architecture into the stable single-user system.

## I — commercialization & launch prep, 10 (candidate designs)
- I01 Free/Pro/Team entitlement abstraction.
- I02 Testable offline license-grace model.
- I03 StoreKit 2 adapter protocol; real purchases stay Mac.
- I04 Storage-quota and derived-cache management strategy.
- I05 User data export and account deletion candidate flows.
- I06 Privacy data inventory, permission inventory, data lifecycle.
- I07 English user-visible strings, glossary, localization checks.
- I08 Accessibility design spec, accessibility IDs, test references.
- I09 Local Docker/Compose/health/backup/restore scripts.
- I10 License/SBOM/release-risk/App Store technical checklist.

No pricing decisions, no purchases, no public deployment, no automatic user-evidence deletion.

## J — RC4/RC5 integration acceptance, 10 (continuous)
- J01 Stand up the RC4 development candidate; keep RC3 frozen.
- J02 Versioned dependency management for cross-platform Swift components.
- J03 Backend OpenAPI vs running service consistency gate.
- J04 Swift DTO vs backend protocol fixed-vector gate.
- J05 Original-evidence immutability, backup-restore and migration gates.
- J06 Full real-HTTP engineering end-to-end tests.
- J07 Clean clone, isolated env, DB migration, full test run.
- J08 Form the RC4 stable common-component candidate; deliver to Mac in batches.
- J09 Fold validated advanced capabilities into an isolated RC5 experimental candidate.
- J10 Unified report, full task matrix, pinned SHAs, final Mac-ready delivery.

RC5 is not a release. No experimental capability that failed stable gates may pollute RC3 or RC4.

## Execution order
Batch 1: A01–A10, B01–B15, C01–C15 (Mac needs the generic Swift, conflict and
sync components first). Batch 2: D01–D15, E01–E15. Batch 3: F01–F10, G01–G10.
Batch 4: H01–H10, I01–I10. J runs throughout. Adjust by dependencies; do not
mechanically wait for a whole queue. Stable modules ship to Mac immediately.

## Standing orders (unchanged from TM-03)
Persist this sheet and the JSON matrix; inherit verified work; run focused
tests per completed package; fix defects and add regression tests; full gate at
each stable stage; commit and push continuously; on network failure keep local
commits and a Git bundle under `D:\定时\工程相机\backups\` and continue with
executable work; ordinary development needs no per-item user confirmation.
Windows writes standalone Swift Packages, business rules, backend, sync cores,
algorithms, ViewModels, tokens, reference SwiftUI and tests. Windows does not
touch Xcode project config, signing/entitlements, AVFoundation capture, Secure
Enclave, App Attest, Core Location capture, ARKit/LiDAR, or the live iOS pages
(`ContentView.swift`, `WorkCamAPI.swift`, `Evidence.swift`) without explicit handoff.
