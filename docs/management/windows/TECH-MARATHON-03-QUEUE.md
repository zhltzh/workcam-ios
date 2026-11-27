# WorkCam TECH-MARATHON-03 — restored execution queue

Version: 2026-11-27 / RESTORED
Owner: Windows main development session
Source of truth: this task sheet and `TECH-MARATHON-03-STATUS.json`
Baseline: Windows hotfix code `2a53712a179ad8880f75cc72d1762fb5b14ec0a8` and report `5d9c61db14cc75cca0c60fe656d4539563cf56df`
Mac reference: PR #24, code `53440feaf734c9ab21e29ddde9666fb8b5ef77cc`

## Instructions
Continue one session, do not create duplicate implementations or 80 feature branches. Preserve original photos and original evidence; no force push, no automatic merge into main, no public deployment or paid services. Do not label Windows/macOS simulator tests as real iPhone verification. Each task requires code/tests where applicable and an honest state: NOT_STARTED, IN_PROGRESS, DESKTOP_VERIFIED, CROSS_PLATFORM_PENDING, XCODE_PENDING, DEVICE_PENDING, POC_VERIFIED, INTEGRATED or BLOCKED. Keep a machine-readable task matrix, record concrete SHAs and test commands, and update the single final report. Prioritize P0 and stable Mac-ready artifacts; keep commercial and advanced experimental reserves isolated.

The 80 IDs below are the continuation of TECH-MARATHON-02 TM01–TM100, **not** 80 additional product requirements. Work completed in TM01–TM100 or WIN-HOTFIX must be reused, not redone. Identify the actual implementation and tests before marking an item done.

## A — historical completion, 8
- [ ] A01 Check hotfix/report pushes, clean working tree, safe backups, full branch chain.
- [ ] A02 TM91: local weak-network fault injector, HTTP timeout/drop/reordering/retry tests on actual outbox and resumable upload.
- [ ] A03 TM93: migrations v1–v14, legacy evidence/project/annotation/issue/backup formats, rollback and original-byte immutability.
- [ ] A04 TM95: idempotent Windows/macOS bootstrap scripts; mark macOS unexecuted steps pending.
- [ ] A05 Verify official Swift toolchain and actually run cross-platform Swift tests; do not claim an unsupported native build.
- [ ] A06 Diff Night05 and PR24 data contracts: ProjectEvidenceLink, annotations, issues, archive, sync receipt, ZIP manifest.
- [ ] A07 Extend actual local HTTP end-to-end for device lifecycle, rotation, chunk restart, issue conflict, checkpoint and independent verification.
- [ ] A08 Strengthen TM94 gates: Swift, API, protocol vectors, safe ZIP, licenses, no secrets, Mac handoff. Allowlist only known public test key fingerprints, never arbitrary secret material.

## B — engineering workflows, 12
- [ ] B01 TM74: English versioned templates: Construction, Site Inspection, Property, Facility Maintenance, Roofing, Electrical, Plumbing, HVAC.
- [ ] B02 TM75: checklist engine Pass/Fail/NA/Not Inspected and evidence requirements.
- [ ] B03 Versioned dynamic form schema: text, number, date, choice, conditional, photo, measurement with validation and Swift Codable.
- [ ] B04 TM76: issue lifecycle and history; map exactly to Mac Night05/PR24 and server closed-terminal behavior.
- [ ] B05 Before/Progress/After photo roles, compare-and-swap replace/unlink and cross-project rejection.
- [ ] B06 Project photo logical filename templates and safe path sanitization, originals unchanged.
- [ ] B07 Bulk metadata/tag/report/export operations, partial errors, no silent overwrite.
- [ ] B08 Offline indexed search by project/date/tag/note/area/equipment/issue/OCR/evidence state.
- [ ] B09 TM78 isolated optional asset/equipment record and QR/OCR human confirmation.
- [ ] B10 TM79 measurement raw points, calibration, units, algorithm, provenance and uncertainty.
- [ ] B11 Deterministic English engineering daily report from observed activity, no fabricated weather/progress.
- [ ] B12 Codable Swift business models/validators/services/repository protocols/tests for B01–B11.

## C — image algorithms, 10
- [ ] C01 TM24/TM64 watermark layout/extreme aspect ratios/logo/text/line-break/golden geometry.
- [ ] C02 TM25/TM63 EXIF orientation all 8 orientations and geometric transformation golden vectors.
- [ ] C03 Multi-annotation layer order/hit-testing/edit/undo/redo/serialization; originals untouched.
- [ ] C04 TM26 photo blur/exposure/contrast/resolution advisories, never auto-delete.
- [ ] C05 TM27 exact SHA256 and approximate perceptual duplicate *candidates*, never merge/delete.
- [ ] C06 TM28 image alignment exploratory PoC with failure threshold; never infer completed repair.
- [ ] C07 Side-by-side, slider, opacity overlay and manual-align Swift reference interface.
- [ ] C08 TM29 offline OCR for labels/instruments/serials with manual review, no automatic fact upgrade.
- [ ] C09 TM30 reversible editing workflow with irreversible-on-derivative redaction, original intact.
- [ ] C10 Reference Swift image processing module, fixtures, goldens, performance and limitations.

## D — standards/deliverables, 10
- [ ] D01 TM37 isolated C2PA claim/ingredient/provenance PoC using maintained upstream implementation; no certification claims.
- [ ] D02 TM38 local RFC3161 test TSA, nonce, imprint and certificate verification; not an external trusted timestamp.
- [ ] D03 TM39 long-term verification, historical keys, revocation, algorithm agility and evidence preservation.
- [ ] D04 TM40/TM81 open ZIP V2 manifest for originals/derivatives/evidence/annotations/issues/measurements/reports/audit; safe paths.
- [ ] D05 Streaming ZIP64, bounded memory, large-file hash, cancellation, safe cleanup only for temp artifacts.
- [ ] D06 TM41 six English PDF report types, A4/Letter: 12 verified samples.
- [ ] D07 Pagination, repeated headers, long notes, tables and photo aspect-ratio handling.
- [ ] D08 Company branding/contact/report ID; signature *text* is not cryptographic signature.
- [ ] D09 TM42 local-only offline verifier web UI, independent hash/signature outcomes, no green universal trust score.
- [ ] D10 Interoperability exports JSON/CSV/GeoJSON/KML/PDF/ZIP with format regression tests.

## E — Apple integration reference, 10
- [ ] E01 Audit Night05/PR24 UI code first, avoid duplicate feature implementations.
- [ ] E02 TM82 outdoor readable, accessible English Design System token/reference previews.
- [ ] E03 TM83 camera SwiftUI reference with camera protocol, leave AVFoundation hardware to Mac.
- [ ] E04 TM84 projects and photo manager search/archive/batch/empty/error states.
- [ ] E05 TM85 annotation, issue checklist and before-after interface; reuse Mac existing views.
- [ ] E06 TM86 measurement UI; real ARKit/LiDAR data acquisition stays Mac/DEVICE_PENDING.
- [ ] E07 TM87 report/export/10-dimension trust details without an invented total score.
- [ ] E08 Sync center pending/ack/retry/conflict/status, no false server-confirmed labels.
- [ ] E09 settings/data portability/backup/cache versus original retention boundaries.
- [ ] E10 TM90 XCUITest reference tests stable accessibility IDs; actual execution only Mac/Xcode.

## F — scale/reliability, 10
- [ ] F01 Deterministic synthetic project/photo/metadata/issue dataset generator with 10k photos, 100k metadata.
- [ ] F02 SQLite indexing/pagination/writer concurrency/migration benchmarks P50/P95/P99.
- [ ] F03 Night05 filesystem scan cost, evidence folder scale and safe cache hygiene.
- [ ] F04 Resumable chunk upload throughput/memory/retries under network faults.
- [ ] F05 10/100/500/1000-photo report generation resource measurements and safe budgets.
- [ ] F06 ZIP64 large input bounded-memory generation, integrity and resource limits.
- [ ] F07 TM92 controlled long-run stress and growth, distinguish host from iPhone.
- [ ] F08 Race-condition tests for enrollment/rotation/sync/archive/backup/checkpoints.
- [ ] F09 Disk full/corruption/kill/lock/network/export/migration failure injection, no loss of originals.
- [ ] F10 Machine-readable environment-tagged benchmark baselines and history.

## G — enterprise reserves, 8 (isolated, not MVP blockers)
- [ ] G01 TM48 tenant isolation PoC for accounts/projects/evidence.
- [ ] G02 TM49 roles Owner/Admin/Inspector/Member/Viewer and authorization matrix.
- [ ] G03 Team invitations/permissions reference only, no actual email.
- [ ] G04 Business operations audit and explicit trust limitations.
- [ ] G05 TM50/TM97 entitlement model, offline grace and restore; no StoreKit purchase/price.
- [ ] G06 TM51 portability/account deletion distinction, no automatic deletion of original evidence.
- [ ] G07 Data inventory/permissions/privacy/SBOM; no tracking/ads SDK.
- [ ] G08 TM98 English localization, accessibility and App Store technical risk checklist only.

## H — integration/handoff, 12
- [ ] H01 TM47 dependency versions/license/SBOM/security check; report actual coverage.
- [ ] H02 OpenAPI==actual running service, Swift Codable and documented revision/receipt semantics.
- [ ] H03 TM07 ten-dimensional trust engine isolated until protocol/product approval.
- [ ] H04 Signed checkpoint experiments and external anchor limits, no false append-only guarantees.
- [ ] H05 Legacy buffer upload→TM35 resumable upload compatibility and migration/rollback.
- [ ] H06 TM99 RC3 stable candidate, reviewed core modules, complete gates, no main merge.
- [ ] H07 RC4 experimental advanced modules isolated from RC3.
- [ ] H08 TM96 local Docker Compose/backup/health/TLS configuration, no public deployment.
- [ ] H09 TM56–TM73 modular Mac-ready Swift Packages with actual executed tests and honest Apple limits.
- [ ] H10 TM100 `handoff/mac-ready/` with 01-START-HERE through 16-INTEGRATION-MANIFEST and machine-readable manifest.
- [ ] H11 Clean clone and repro: venv/DB migrations/live API/Swift tests/gates/manifest; no private tmp dependencies.
- [ ] H12 Single final `WORKCAM-WINDOWS-TECH-MARATHON-03-REPORT.md`, SHAs, task matrix, device blockers and exact tests.

## Immediate P0 sprint after the verified hotfix
1. Verify enrollment 30-second SQLite writer contention no longer reproduces on the integrated Mac simulator and on Windows native when available. Restore ordinary client registration timeout only after verified; do not blindly change Mac.
2. Mac PR24 integration contract: `project_update`, `project_archive`, `issue_update`, `issue_transition`, `issue_photo_unlink`, guarded `issue_photo_link`; page/filter cursor persistence and rejected receipts. Distinguish old direct REST role replacement semantics.
3. Database/contract concurrency: true repeated retries, stale revisions, tombstones, independent owner cursor, snapshot-at-read not event snapshot. No cross-device unsupported claims.
4. A02/A03/A05/A06/A08 then B01–B05, B12, D04–D06, H02/H09/H10/H11, using dependency order. Remaining work pools continue when free.
5. Keep code and report on explicit reviewable branches. Request review for production changes; never silently merge. Publish status periodically but do not stop and wait for human input on ordinary coding.

## Persistent working protocol
At beginning of every new conversation: read this file, the adjacent JSON, the hotfix handoff `docs/mac/WIN-HOTFIX-HANDOFF.md`, relevant recent Mac PR reports, then inspect code and continue the next unblocked P0 task. Commit/push progress. Never overwrite task-state entries without comparing actual SHAs/tests. The branch containing this file is a planning branch; do not assume its contents exist on `main` until they are explicitly merged.
