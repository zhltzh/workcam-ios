# WorkCam cross-platform ownership — revision 01

Date: 2026-11-28
Decision: Windows is the **default implementation owner** for any work it can build and test with available tooling. Mac is the **Apple-platform owner and integration/release gate**. Neither team duplicates the other's implementation merely to increase task counts.

## Immutable review baselines
- Mac: PR #27 `codex/workcam-mac-super-02`, SHA `c6c6edaf2da1587455abd4d0f1ac06c7d685467b`, implementation code `809caf3840b9167da0aacd1f9d859e69fad8c721`.
- Windows backend: `2a53712a179ad8880f75cc72d1762fb5b14ec0a8`; original Marathon 03 tracking PR #25.
- No automatic main merges, no forced pushes, no silently rebasing active branches; integrate via fixed SHA and reviewed stacked PRs.
- This policy is a planning branch/PR; preserve existing code, local photos, evidence and unrelated WIP.

## Assignment rule
**Windows first:** backend/database/API/auth/idempotency/migrations; sync and conflict state machines; cross-platform Swift packages if the official toolchain is installed and tests actually run (otherwise label MAC_COMPILE_PENDING); business models/rules/schema, geometry, watermark, annotations, report data, ZIP and PDF generation engines, independent verifier, protocol vectors, fixtures, stress/fault/performance/security tests, localization strings, accessibility checklists, UI view models and design tokens, Mac-ready docs.
**Mac only/last-mile:** Xcode project and entitlement/signing/provisioning, iOS/macOS SwiftUI real screen integration, AVFoundation camera and Core Location/ARKit/LiDAR actual acquisition, Secure Enclave APIs and Apple App Attest on device, iOS background URLSession/Keychain specifics, XCUITest/visual acceptance, native PDFKit/UIKit if Windows implementation cannot prove equivalence, physical device validation, stable Windows package integration, product UX approval and candidate distribution.
**Shared boundary:** Windows supplies implementation, tests and fixed-sha contract; Mac checks ability to compile/link/run on Xcode, integrates into actual app, executes simulator/device tests, reports gaps back as Windows issues rather than reimplementing the core. Mac must not idle for a missing component: run independent Apple tasks or interface mocks clearly marked mocks.

## Immediate Windows P0 transfer after Super 02
W-X01 Conflict resolution state machine: compare server/local/revisions/intent; actions use-server, keep-local/rebase, manual merge, defer/cancel with explicit confirmation and audit; preserve old operations, new ID for changed intent; no automatic overwrites. Deliver pure Swift logic + fixtures plus backend alignment; Mac owns actual SwiftUI presentation and UI clicks.
W-X02 Offline import and binding: typed local/server project/issue/photo IDs, versioned mapping, idempotent import, tombstone handling, atomic cursor+data, migration/rollback, orphan/foreign-owner constraints; no silent import into editable local records. Deliver Swift reference repository and deterministic test vectors; Mac owns persistence adapter/integration.
W-X03 Deep sync failure suite: independent conflict A, unrelated B, blocked C, restart recovery, HTTP 401/403/409/429/5xx with Retry-After, exponential bounded backoff with deterministic jitter, cancellation, limit windows, stale revision, concurrent same-resource, chunk expiry/poison, server restart and crash-before-ack. Preserve original bytes & operation-id replay.
W-X04 Rate-limit-safe test orchestration: segregated disposable identity/database per integration test, unique ports, reliable teardown; test 429 semantics separately. Never disable/change production limits merely to make the suite green.
W-X05 Endpoint consistency & contract: OpenAPI/actual service/Swift Codable/transaction boundaries/idempotent rejection; zero unsupported fake endpoints, old REST role overwrite boundary, issue closed terminal, filter cursor semantics and event state-at-read caveat.
W-X06 Massive reliability: 10k photo/100k metadata fixtures, SQLite WAL/cache/index benchmarks, file scans, ZIP64 streaming large-file memory ceilings, PDF page limits, fault injection, migration v1–14; host performance distinct from iPhone.
W-X07 Photo/engineering business components: reusable templates, checklists, issue and photo role rules, batch tags, search indexes, report assembly, image EXIF/geometry/annotations and watermark goldens. Stable modules only; do not replace existing Mac UI.
W-X08 Standards/verifier: W018 + evidence ZIP V2/manifest/DER vectors, C2PA/RFC3161 isolated PoCs, trust dimensions as **unapproved experimental** only, report hash and original bytes checks.
W-X09 Cross-platform Swift build: actually run supported Swift Package Manager on available Windows official toolchain. If unavailable, do not claim it compiled; deliver versioned source with CROSS_PLATFORM_PENDING and Mac test steps. Prefer separate package for non-Apple components.
W-X10 Mac-ready: `handoff/mac-ready/` fixed SHA, install steps, exact package API, fixtures, executable tests, licensing, known limitations, coverage matrix, and migration notes. No "ready" label without concrete reproducible evidence.

## Immediate Mac P0 retain
M-K01 Review PR #27 and ownership boundary; preserve existing branch/data; identify already working Swift code and don't transplant unfinished experiments.
M-K02 iPhone already installed prior candidate: when accessible, unlock and run in-place offline capture, keys, App Attest/server, time/location, three LiDAR modes, power/reboot recovery, upload, PDF/ZIP plus independent verifier. Report DEVICE_PENDING when inaccessible; no uninstall/reset, no fake evidence.
M-K03 Finish device-only AVFoundation/Core Location/ARKit/Keychain, Secure Enclave/App Attest, background transfer and camera/measurement UI.
M-K04 Build Xcode integration adapter around W-X01–X10; validate actual Swift live HTTP, simulator XCUITest and Release signed build; detect runtime regressions, take real UI screenshots and improve product UX.
M-K05 Keep PR chain merge-tree/CI/Release audit; screen entrypoints/features, UI/UX owner review, alpha readiness and redacted evidence logs. Production App Attest or calibrated measurement remains pending until actual device proof.

## Work routing / avoid collisions
1. Each task has a single owner. Windows modifications live on an independent branch based on the reviewed server baseline, not on the Mac PR #27 head.
2. Mac and Windows exchange contract + tests + immutable SHAs. If a task is transferable, Windows gets implementation; Mac gets integration or device acceptance only.
3. No competing edits to `App/ContentView.swift`, `Core/Sync/WorkCamAPI.swift`, `WorkCam.xcodeproj`, `Core/Security/Evidence.swift` without explicit handoff. Windows should implement new independent modules rather than touching Apple-owned app files.
4. Existing TM01–TM100/Marathon-03 and Mac 80-task matrix are inventories: **reclassify** not reset, mark duplicate responsibility TRANSFERRED_TO_WINDOWS or MAC_INTEGRATION_PENDING only when actual history and proof is reviewed; never fake completion.
5. Escalate unresolved contract issues with exact request/response, expected behavior, minimum reproduction and fixed commit. An unmerged PR does not itself deploy code.
6. Priority P0 sync stability + trustworthy original data before polishing or advanced features. Enterprise and advanced measurements are optional R&D, not launch blockers.
7. Never leak credentials, delete originals, clear iPhone data, assert legal evidence validity, certify GNSS/time/LiDAR accuracy, make purchases or deploy publicly.

## Acceptance
Windows independently produces stable core library / backend / contracts / fixtures / verified tests and handoff. Mac builds and exercises production iOS integration, simulator + physical device if available. Both separately report **actual executed** tests with environment and SHA. Overall Alpha **not accepted** until local app journeys, real hardware validations, and device evidence/restore checks pass.
