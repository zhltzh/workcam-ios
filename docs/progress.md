# WC-M-TECH-ALL-001 — 2026-11-19

## M0 initial audit

Working checkout: WorkCam-GitHub-Sync, dev/mac-evidence-m003. Outer workspace is an unrelated uncommitted shell; preserved. Local HEAD c1690eb; GitHub PR #6 head f3b93307043bb54b983f4656528070ac48fa429f. Do not overwrite either history before comparison. GitHub main reported by PR metadata: 17d96996d1bc79798f1c8b1fc832563275c8212e; current remote main not yet independently confirmed by fetch.

Read PR #6 (Draft), #7 (open W005), #8 (open Windows tech lab). Windows results are reported by those PRs, not locally reproduced. No merge performed. Windows branch unchanged. Full experiment protocol audit remains pending retrieval.

Actual commands:

```sh
xcodebuild -version
xcodebuild -showsdks
xcrun xctrace list devices
xcrun devicectl list devices
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamM0Audit build > /tmp/workcam-m0-signed.log 2>&1
```

Xcode 27.2 (27B52), iPhoneOS SDK 27.2. xctrace sandbox attempt aborted on cache permissions; this is a host tooling restriction, not device failure. devicectl outside sandbox reports paired available physical iPhone 17 Pro Max (iPhone18,2). Signed build: BUILD FAILED, requires Development Team. No installation, camera run, device logs, hardware signatures or lifecycle experiments performed. Device OS and current Developer Mode not yet verified. Log is local temporary build output.

M0-01: BLOCKED on user selecting actual development team. M0-02: DEVICE_PENDING, execution blocked by M0-01. No software key or simulator result accepted as hardware evidence. M1–M5: Not Implemented by this task; existing work must be audited separately.

User action: open WorkCam.xcodeproj in this checkout; Xcode Settings > Accounts > sign in with your Apple ID if necessary; target WorkCam > Signing & Capabilities > select your Team and enable automatic signing. Confirm the bundle identifier is available to that team. Select connected iPhone. On iPhone, enable Settings > Privacy & Security > Developer Mode if requested, restart and confirm; unlock and trust the Mac. No identity or team identifier has been fabricated.

Do not mark DEVICE_VERIFIED until actual device acceptance and raw observations exist. Continue independent protocol review and experiment preparation while device setup is pending.

## Independent work resumed — T03 diagnostic core

Created dev/mac-tech-lab from local M003 c1690eb (dependency on PR #6 implementation; remote PR head differs and remains unreconciled). Existing user signing edits preserved, not committed. No Windows branch merged.

Implemented Core/TechLab/ClockObservation.swift: device wall time, uptime, session/sequence, zone identifier/offset; rollback/jump, sequence, invalid numeric values, uptime reset and session boundary diagnostics. Across app sessions uptime comparison is deliberately refused. This is a standalone diagnostic core, not yet linked into app target. Server anchor association, persistence/UI and iPhone clock manipulation tests: Not Implemented. Shared Windows wire compatibility not yet established; native internal types are not a replacement protocol.

Actual validation:
```sh
xcrun swiftc Core/TechLab/ClockObservation.swift Tests/TechLab/main.swift -o /tmp/workcam-clock-tests
xcrun swiftc -module-cache-path /tmp/workcam-clock-module-cache Core/TechLab/ClockObservation.swift Tests/TechLab/main.swift -o /tmp/workcam-clock-tests && /tmp/workcam-clock-tests
```
First command failed because default compiler module cache was denied by sandbox. Second command passed 8 synthetic diagnostic scenarios plus JSON roundtrip. macOS compilation/tests only; no iOS build or device execution established by this check. No measured performance or real-world accuracy claim. T03 partial implementation; not approved for product use.

## 2026-11-20 T04 initial native prototype
Added Debug-only LiDAR Lab navigation and ARSCNView preview, sceneDepth capability check, depth availability/tracking display, two center raycast points with world markers, reference distance, error calculations and user-initiated CSV sharing. Raycast misses/tracking failures retained as rows. Tracking loss invalidates unfinished pair. No raw camera imagery is persisted by this module.

Provisional CSV is internal: Windows 96-shot matrix/protocol integration remains pending. Scene depth pixels/confidence/intrinsics, full environment/device metadata, durable experiment persistence and automatic interruption handling are Not Implemented. Rows currently remain only in memory; export before leaving. Accuracy/performance/physical-device results absent. Not approved for production.

Actual command (first sandbox execution failed because existing SwiftUI macro service was denied; retried outside sandbox):
```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidar CODE_SIGNING_ALLOWED=NO build > /tmp/workcam-lidar-debug.log 2>&1
```
Outside-sandbox retry: Debug BUILD SUCCEEDED (unsigned iPhoneOS compile/link only). Device execution not performed. Release validation pending.

## WC-M-TECH-M1-R1 — partial implementation
Read PR #8 T04 experiment and analyzer at e1ea45fe28644a0c286a2f06ea9cc98135e77519 through GitHub connector. Matrix listed rows total 68 attempts, despite stated 96; requires protocol clarification. Analyzer requires distance_true_m,distance_measured_m,condition,distance_m and ignores additive fields; aggregates modes together and computes standard deviation of absolute errors, not measured values grouped by true distance. Do not use pooled output as mode-specific repeatability. No shared protocol modified.

Added separate raycast/scene_depth/smoothed_depth paths, inverse ARFrame displayTransform mapping of viewport center into normalized captured image, depth pixel center scaled to camera image resolution, intrinsics backprojection with ARKit camera Y/Z signs and camera-to-world transform. Depth requires high confidence (2), finite positive depth <=10m; unavailable data never falls back. 10m is an experimental rejection threshold, not supported precision/range claim. Added atomic JSON archive reload; corrupt archive blocks overwrite and displays error. Mode changes pause session and discard temporary point. Separate setup distance input and condition picker; Windows-required CSV columns preserved. Depth buffer confidence values are currently filtered but not exported.

Initial unsigned Debug command returned BUILD SUCCEEDED:
```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidarR1 CODE_SIGNING_ALLOWED=NO build > /tmp/workcam-lidar-r1.log 2>&1
```
Final incremental checks:
```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidarR1 CODE_SIGNING_ALLOWED=NO build > /tmp/workcam-lidar-r1-final.log 2>&1
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidarR1Signed build > /tmp/workcam-lidar-r1-signed.log 2>&1
```
Remaining: independent geometry/CSV/persistence automated tests, batch identifiers/export, actual device/OS fields, confidence/tracking export, robust CSV quoting, interruption/freshness handling, outlier separation, Windows analyzer execution, raw iPhone CSV, accuracy report, commit/PR. No true-device precision conclusion. Task is not complete or production-approved.

## M1-R1 continuation
Added batch/test IDs, batch selector and restoration, hardware machine identifier/iOS version, per-endpoint depth confidence, depth availability, tracking string, CSV quoting, explicit session-running/stale-frame failures and foreground lifecycle pause. Retains archives across mode/batch changes and refuses corrupt-archive overwrite. Distances outside (0,10]m become explicit failures; threshold is experimental. Existing legacy unquoted rows remain in archive but are not migrated into new batch exports (migration Not Implemented).

Commands:
```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidarR1 CODE_SIGNING_ALLOWED=NO build > /tmp/workcam-lidar-r1-fields.log 2>&1
xcrun swiftc -module-cache-path /tmp/workcam-clock-module-cache Core/TechLab/ClockObservation.swift Tests/TechLab/main.swift -o /tmp/workcam-clock-tests && /tmp/workcam-clock-tests
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidarR1 CODE_SIGNING_ALLOWED=NO build > /tmp/workcam-lidar-r1-batches.log 2>&1
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidarR1Signed -allowProvisioningUpdates build > /tmp/workcam-lidar-r1-provision.log 2>&1
```
Fields build passed. Clock fixtures passed; these are not LiDAR tests. Batch build and provisioning result checked separately. Geometry, buffer stride/mapping and persistence need dedicated automatic tests before acceptance; real-device CSV absent. No accuracy conclusion or completed-task claim.

## M1-R1 geometry and device installation
Previous provisioning retry completed BUILD SUCCEEDED (signed Debug, /tmp/workcam-lidar-r1-provision.log). Actual installation succeeded on paired iPhone, bundle com.example.workcam.dev:
```sh
xcrun devicectl device install app --device 00008150-001251D23482401C /tmp/WorkCamLidarR1Signed/Build/Products/Debug-iphoneos/WorkCam.app
xcrun devicectl device process launch --device 00008150-001251D23482401C com.example.workcam.dev
```
Installed artifact precedes the latest extracted geometry helper; reinstall latest signed artifact before final acceptance. Installation alone does not verify camera/depth/UI or precision.

Extracted DepthGeometry pure Swift helper and used it in depth paths. Synthetic tests cover axes, scaled intrinsics/pixel centers, bounds, nonfinite/negative/overrange depth and invalid focal length. Actual command PASS:
```sh
xcrun swiftc -module-cache-path /tmp/workcam-clock-module-cache Features/TechLab/DepthGeometry.swift Tests/Lidar/main.swift -o /tmp/workcam-depth-tests && /tmp/workcam-depth-tests
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidarR1 CODE_SIGNING_ALLOWED=NO build > /tmp/workcam-lidar-r1-geometry.log 2>&1
```
No physical distance sample collected. DisplayTransform orientation/cropping, AR world transform, device depth confidence and kill/relaunch persistence remain DEVICE_PENDING. CSV/persistence dedicated tests and commit/PR still outstanding.
Geometry integration Debug BUILD SUCCEEDED. Device launch failed: CoreDeviceError 10002 / RequestDenied / Locked; phone must be unlocked for runtime checks. No runtime acceptance recorded.

## M1-R1 runtime launch and dedicated automatic checks
Actual device launch retry succeeded: `xcrun devicectl device process launch --device 00008150-001251D23482401C com.example.workcam.dev`. This proves process launch only, not camera/depth operation. No real measurement CSV yet.

Extracted LabArchive and exercised real helper in tests: missing file, atomic save/reload and corrupt archive refuses overwrite. Swift CSV quoting fixture roundtrips through Python csv.reader including comma, quote, embedded newline and Chinese. All synthetic; no physical sample.
```sh
xcrun swiftc -module-cache-path /tmp/workcam-clock-module-cache Features/TechLab/DepthGeometry.swift Features/TechLab/LabArchive.swift Tests/Lidar/main.swift -o /tmp/workcam-lidar-tests && /tmp/workcam-lidar-tests
```
PASS geometry tests, archive reload/corruption preservation, CSV escape fixture. Python csv.reader assertion passed on /tmp/workcam-csv-fixture.csv. Windows analyzer execution still pending; escape test is not equivalent to complete analysis compatibility.

Latest signed build command:
```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidarR1Signed -allowProvisioningUpdates build > /tmp/workcam-lidar-r1-delivery.log 2>&1
```

## M1-R1 Windows compatibility and PR history
Merged origin/dev/mac-evidence-m003 into dev/mac-tech-lab with `git merge --no-edit origin/dev/mac-evidence-m003`; clean merge, identical tracked content between c1690eb and f3b9330. No PR merged and no Windows branch modified. PR #9 remains Draft.

Fetched unmodified Windows lidar_error_analysis.py at e1ea45fe28644a0c286a2f06ea9cc98135e77519 via connector to /tmp/workcam-windows-lidar-analysis.py. Swift-generated SYNTHETIC two-row fixture (success and failure) consumed directly by analyzer:
```sh
xcrun swiftc -module-cache-path /tmp/workcam-clock-module-cache Features/TechLab/DepthGeometry.swift Features/TechLab/LabArchive.swift Tests/Lidar/main.swift -o /tmp/workcam-lidar-tests && /tmp/workcam-lidar-tests
python3 /tmp/workcam-windows-lidar-analysis.py /tmp/workcam-windows-compatible-synthetic.csv --json /tmp/workcam-lidar-synthetic-analysis.json
```
Passed: analyzer reports 2 attempts / 1 failure, expected synthetic error. This is format compatibility only, not real accuracy. Analyzer emits NaN for insufficient repeatability data; strict JSON interoperability requires Windows follow-up. Original Windows implementation unchanged.

Latest signed Debug artifact installed successfully (second install). Runtime launch success previously observed. Physical measurements, raw hardware CSV and device kill/relaunch data retention have not been observed. Accuracy: no conclusion. Commercial use: not approved.

Changing reference/setup distance now pauses unfinished measurement to prevent mixing endpoints from different experimental settings.
```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidarRelease CODE_SIGNING_ALLOWED=NO build > /tmp/workcam-lidar-r1-release.log 2>&1
```

## WC-M-TECH-M1-R2 — in progress
Baseline 357331f. Real paired physical iPhone 17 Pro Max available in initial query. Attempted real-device launch first; failed CoreDevice 4000 connection invalidated / NWError 54 Connection reset by peer. No raw depth, measured lengths, environment observation or orientation acceptance obtained. Physical known-length setup is not available to the agent; no inferred reference measurement substituted.

Added schemaVersion=2 envelope with parsed field arrays and retained legacy CSV, unsupported-version rejection, pre-v2 backup before atomic replacement. This is an intermediate migration layer: UI still uses strings; named structured record/context model remains unfinished. Tests passed v1 reload -> v2 write -> reload, parsed quoted field and preserved backup. Added checks for DepthFloat32 / OneComponent8 formats and minimum buffer row strides before pixel reads. No hardware buffer-layout acceptance yet.

Actual commands:
```sh
xcrun devicectl list devices
xcrun devicectl device process launch --device 00008150-001251D23482401C com.example.workcam.dev
xcrun swiftc -module-cache-path /tmp/workcam-clock-module-cache Features/TechLab/DepthGeometry.swift Features/TechLab/LabArchive.swift Tests/Lidar/main.swift -o /tmp/workcam-lidar-tests && /tmp/workcam-lidar-tests
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidarR1Signed -allowProvisioningUpdates build > /tmp/workcam-lidar-r2.log 2>&1
```
Tests PASS (synthetic only). Remaining R2: fully named structured records, frozen endpoint contexts, endpoint timestamps/tracking, multi-orientation mapping tests, real three-mode data and environment, real force-quit recovery, original-analyzer output by mode. Not DEVICE_VERIFIED; no precision conclusion.

## WC-M-TECH-M1-R2A — connection diagnosis and context increment
Actual device query/details succeeded. Physical iPhone18,2 (iPhone 17 Pro Max), OS 27.2, Developer Mode Enabled, pairing paired, transport localNetwork/TCP. USB physical cable state cannot be established from this output. No physical reconnection/reboot performed. Current launch fails with explicit Locked / RequestDenied (CoreDevice 10002), not connection reset or established app crash. No rationale to restart Mac while device service/detail query works. User must unlock physical phone before runtime acceptance; do not repeat launch unchanged.

Full local diagnostics (contain device identifiers; not committed): /tmp/workcam-r2a-devices.json, /tmp/workcam-r2a-details.json, /tmp/workcam-r2a-details.log, /tmp/workcam-r2a-launch.log.
```sh
xcrun devicectl list devices --json-output /tmp/workcam-r2a-devices.json
xcrun devicectl device info details --device 00008150-001251D23482401C --json-output /tmp/workcam-r2a-details.json > /tmp/workcam-r2a-details.log 2>&1
xcrun devicectl device process launch --device 00008150-001251D23482401C com.example.workcam.dev > /tmp/workcam-r2a-launch.log 2>&1
```

Added frozen in-memory context snapshot for mode, batch, ID, condition, reference and setup distance; second endpoint uses snapshot. Added first endpoint time/tracking columns (existing device_time/tracking are second/failure endpoint); both confidence columns retained. Tracking loss between points records failure instead of silently discarding unfinished pair. Named persistent model, context-specific automatic tests and multi-orientation mapping acceptance remain unfinished. No claim these gaps are completed.
```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamLidarR1Signed -allowProvisioningUpdates build > /tmp/workcam-r2a-build.log 2>&1
xcrun swiftc -module-cache-path /tmp/workcam-clock-module-cache Features/TechLab/DepthGeometry.swift Features/TechLab/LabArchive.swift Tests/Lidar/main.swift -o /tmp/workcam-lidar-tests && /tmp/workcam-lidar-tests
```
Existing geometry/archive/migration tests PASS. No real Scene Depth, Smoothed Depth, known 1m sample, raw CSV, environment or force-quit recovery evidence this run. T04 remains open, not DEVICE_VERIFIED. Existing PR #9 only; no merge authorized/performed.

## WC-M-006 — 2026-11-23 initial audit
Branch dev/mac-tech-lab, initial clean HEAD 69f1660f91d20a7f1d87adedb88cf4bdfb998f9b. PR #6 f3b9330 and PR #9 69f1660 both Draft/open, not merged. No Windows branch merged.

DEVICE_VERIFIED (limited scope): existing installed WorkCam process launched successfully via devicectl this run. Physical iPhone17 Pro Max/iPhone18,2, OS27.2, paired, Developer Mode Enabled, localNetwork transport. Installed artifact SHA not established; not latest-build runtime acceptance.
```sh
xcrun devicectl list devices
xcrun devicectl device info details --device 00008150-001251D23482401C > /tmp/workcam-m006-device.log 2>&1
xcrun devicectl device process launch --device 00008150-001251D23482401C com.example.workcam.dev > /tmp/workcam-m006-launch.log 2>&1
```
Added evidence folder export of copies: photo.jpg, record.json, exact payload.bin, public-key.x963 and signature.der. Only UUID committed folders; verification before copy; no private-key data read/exported by export routine. UI prepares folder then user shares. Folder sharing/interoperability requires device acceptance. Unsigned records remain visibly unsigned; export does not promote trust.

DESKTOP_VERIFIED: existing M003 fixed-vector/tamper/missing-signature/forged-status tests passed. New export routine itself not yet covered by dedicated test.
```sh
xcrun swiftc -module-cache-path /tmp/workcam-clock-module-cache Core/Security/Evidence.swift Tests/M003/main.swift -o /tmp/workcam-m006-tests && /tmp/workcam-m006-tests test-vectors/fixed-vectors.json
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamM006 -allowProvisioningUpdates build > /tmp/workcam-m006-build.log 2>&1
```
DEVICE_PENDING: actual photo, SHA/signature observation, public package export, independent Windows verification and real-photo byte tamper rejection; all three LiDAR modes/known reference/angle/orientation/raw coordinates/CSV; flight mode and app restart persistence. No physical environment or known length observable from command-line alone. No real sample or accuracy table generated. Windows W007–W009 handoff review outstanding; sync not implemented here. T04 remains open.

## WC-M-006 second stage — export-specific tests
DESKTOP_VERIFIED: exact public export file allowlist, byte-for-byte JPEG/payload/public key, DER signature mathematics, sentinel file exclusion, one-byte photo tamper rejection and refusal to export tampered source. Public fixed vector only, not real JPEG capture or Secure Enclave hardware evidence.
```sh
xcrun swiftc -module-cache-path /tmp/workcam-clock-module-cache Core/Security/Evidence.swift Tests/M003/main.swift -o /tmp/workcam-m006-export-tests && /tmp/workcam-m006-export-tests test-vectors/fixed-vectors.json
```
PASS all existing tests plus export suite. Export routine copies only explicit files; never enumerates Keychain or includes key dataRepresentation. Metadata follows EvidenceRecord allowlist. Dedicated export test injects private-key-test-sentinel and asserts it absent. Actual real-record credential absence still awaits real package inspection.

DEVICE_VERIFIED (installation only): /tmp/WorkCamM006/Build/Products/Debug-iphoneos/WorkCam.app installed successfully via devicectl. This build contains export UI, precedes the test-injection-only optional sourceRoot argument change.
```sh
xcrun devicectl device install app --device 00008150-001251D23482401C /tmp/WorkCamM006/Build/Products/Debug-iphoneos/WorkCam.app > /tmp/workcam-m006-install.log 2>&1
xcrun devicectl device process launch --device 00008150-001251D23482401C com.example.workcam.dev > /tmp/workcam-m006-stage2-launch.log 2>&1
```
BLOCKED P0 physical actions: agent has no direct iPhone screen input/physical camera positioning in current tools. Need real user capture in Open Camera, then Evidence Debug/Reverify/Prepare Evidence Export. No real record yet retrieved/observed. P0 independent W005-R1 verification still DEVICE_PENDING; CryptoKit fixture test does not substitute the Windows verifier. Device-end Secure Enclave creation observations are pending. P1 known 1m five repeats and P2 actual flight-mode network loss/kill-relaunch are DEVICE_PENDING; no invented measurements, no completed-task claim.

## WC-M-006 second-stage real package retrieval attempt
HEAD 5399a74. Clean worktree at start. Attempted device evidence copy:
```sh
xcrun devicectl device copy from --device 00008150-001251D23482401C --domain-type appDataContainer --domain-identifier com.example.workcam.dev --source 'Library/Application Support/Evidence' --destination /tmp/workcam-m006-device-evidence --timeout 25
xcrun devicectl device info files --device 00008150-001251D23482401C --domain-type appDataContainer --domain-identifier com.example.workcam.dev --subdirectory Library --recurse --timeout 20
```
Copy failed CoreDevice 7000 failed to retrieve file node. Subsequent successful Library listing shows caches/preferences/saved-state, no Application Support/Evidence. No actual photo record available at expected app storage path. Not a signature rejection. BLOCKED P0 at physical capture; no Secure Enclave creation observed. Do not claim independent real-photo validation. Full local logs /tmp/workcam-m006-copy.log and /tmp/workcam-m006-files.log.

Read Windows dev/windows-w005 docs/ios-backend-w005-handoff.md via GitHub connector; register-key uses query params, X9.63 public key/DER signature and canonical v1.1. Specific W005-R1 standalone tool still requires identification/retrieval; no real package exists to run it against yet.

Latest HEAD signed Debug rebuild /tmp/workcam-m006-stage2-latest.log: BUILD SUCCEEDED. Latest install/launch attempted, logs /tmp/workcam-m006-latest-install.log and /tmp/workcam-m006-latest-launch.log. Exact commands same devicectl installation and launch documented above. P1/P2 remain DEVICE_PENDING, no measured CSV. Export fixture suite remains DESKTOP_VERIFIED; not hardware provenance.

## WC-M-006 actual photo received — 2026-11-23
User reported successful real capture. DEVICE_VERIFIED limited to persisted artifacts: device listing shows committed UUID folder with ~1.6 MB photo.jpg, 822-byte record.json, 70-byte signature.der and local-status.txt. Directory copy failed (CoreDevice7000/POSIX1), but subsequent per-file copy succeeded for photo/record/DER. Lock state query: passcodeRequired=false, unlockedSinceBoot=true. Do not attribute initial copy error to capture/signature failure.

Private local material only (not committed): /tmp/workcam-m006-real-files/{photo.jpg,record.json,signature.der,public-key.x963,payload.bin,verification-report.json}. Public key extracted from record; payload reconstructed on desktop from v1.1 canonical fields, not yet compared against device-exported payload.bin. Record status reports local verification pending; not treated as proof.

DESKTOP_VERIFIED on actual photo: SHA256(photo)==record.sha256==manifest.photo_hash; X9.63 public-key hash matches device_id/publicKeyID; detached DER equals manifest DER. Independent OpenSSL P-256 SHA256 verification returned Verified OK. A copy with first photo byte XOR1 fails original hash; replacing signed photo_hash with changed hash fails signature. Original JPEG preserved. Local report verification-report.json records results. Python cryptography unavailable (ModuleNotFoundError), so used OpenSSL; this is NOT execution of Windows W005-R1 code.

Commands used devicectl copy from appDataContainer com.example.workcam.dev, each source Library/Application Support/Evidence/<record UUID>/{record.json,photo.jpg,signature.der}, destination /tmp/workcam-m006-real-files. OpenSSL commands:
```sh
openssl pkey -pubin -inform DER -in /tmp/workcam-m006-real-files/public-key.spki.der -out /tmp/workcam-m006-real-files/public-key.pem
openssl dgst -sha256 -verify /tmp/workcam-m006-real-files/public-key.pem -signature /tmp/workcam-m006-real-files/signature.der /tmp/workcam-m006-real-files/payload.bin
openssl dgst -sha256 -verify /tmp/workcam-m006-real-files/public-key.pem -signature /tmp/workcam-m006-real-files/signature.der /tmp/workcam-m006-real-files/tampered-payload.bin
```
First verify success, second nonzero failure expected. Generated SPKI wraps X9.63 key with prime256v1 public algorithm header; no private material used. Temp folder includes deliberately tampered copies; these are not original evidence.

DEVICE_PENDING: hardware provenance/creation telemetry (capture source uses SecureEnclave.P256.Signing.PrivateKey without software fallback but math alone is insufficient), exact device-exported payload comparison, Windows W005-R1 execution, LiDAR real 1m×5 and flight mode/restart. P0 progressed but not fully accepted. No screenshot attached in user's last message; none claimed inspected.

## WC-M-006 user-triggered export inspection
User reports tapping Prepare Evidence Export. Read app tmp directory: 0 files. Full container listing likewise has no WorkCam-Evidence folder, payload.bin or public-key.x963. It does show a second committed evidence UUID folder (21:12 device-reported modification time); not yet verified. Lock state now passcodeRequired=true, unlockedSinceBoot=true. Cannot conclude export success or precise failure cause from directory listing. Need unlocked foreground app and observed result/error after Prepare Evidence Export. Original real-photo math verification remains valid; device exact payload comparison still pending.
```sh
xcrun devicectl device info files --device 00008150-001251D23482401C --domain-type appDataContainer --domain-identifier com.example.workcam.dev --subdirectory tmp --recurse --timeout 20
xcrun devicectl device info files --device 00008150-001251D23482401C --domain-type appDataContainer --domain-identifier com.example.workcam.dev --subdirectory . --recurse --timeout 20
xcrun devicectl device info lockState --device 00008150-001251D23482401C
```
Local outputs /tmp/workcam-m006-export-list.log and /tmp/workcam-m006-export-all.log. No export package claimed received. No private imagery committed.

## 2026-11-24 export button feedback repair
User reports tapping export with no visible response. Exact root cause not proven. Replaced synchronous main-thread preparation with background task, progress/disabled button, persistent result text and explicit success/error alert. Empty export now throws noExportableRecords instead of reporting empty folder success. Existing evidence preserved. No claim that folder sharing itself is fixed/verified.

DESKTOP_VERIFIED: full M003 suite including allowlist/exact payload/tamper export tests and new empty-source explicit-error test PASS. Signed Debug build BUILD SUCCEEDED (/tmp/workcam-m006-export-feedback.log). Command uses same /tmp/WorkCamM006 derived data and -allowProvisioningUpdates documented above. Device install/launch attempts logged /tmp/workcam-m006-feedback-install.log and /tmp/workcam-m006-feedback-launch.log. Actual button tap/result and exact device payload retrieval remain DEVICE_PENDING.

## WC-M006-R1 — 2026-11-24 wired deployment recovery
Initial HEAD5399a74 plus pending export-feedback repair. Device confirmed connected/paired, wired transport, iOS27.2, Developer Mode Enabled. Lock query passcodeRequired=true. Signed Debug rebuild succeeded /tmp/workcam-r1-build.log. Original app updated in place successfully /tmp/workcam-r1-install.log; no uninstall, container reset or key deletion. Installed directly from /tmp/WorkCamM006/Build/Products/Debug-iphoneos/WorkCam.app produced by this build. Existing CFBundleVersion does not distinguish revisions; independent installed binary identity comparison remains pending.

Launch failed Locked/RequestDenied CoreDevice10002 (/tmp/workcam-r1-launch.log), not 4000/4016. File-list after update succeeded and shows both historical photo/record/DER sets still present (~1.6MB and ~2.7MB originals), /tmp/workcam-r1-preserved.log. File presence is DEVICE_VERIFIED; byte/hash preservation and Keychain continuity after this update still pending unlocked verification. No claim of newly completed hardware provenance.

Actual commands: same xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination generic/platform=iOS -derivedDataPath /tmp/WorkCamM006 -allowProvisioningUpdates build; devicectl device install app from that artifact; device process launch com.example.workcam.dev; device info files appDataContainer Library/Application Support/Evidence. No destructive recovery action used.

DESKTOP_VERIFIED export feedback core regression suite PASS:
```sh
xcrun swiftc -module-cache-path /tmp/workcam-clock-module-cache Core/Security/Evidence.swift Tests/M003/main.swift -o /tmp/workcam-r1-tests && /tmp/workcam-r1-tests test-vectors/fixed-vectors.json
```
UI progress/success/error/repeated tap acceptance remains DEVICE_PENDING. Empty-source explicit error test PASS; no historical data removed to manufacture empty-device state. P2 real photo OpenSSL verification from prior stage remains DESKTOP_VERIFIED, not hardware provenance proof. P3 known-reference measurement and P4 confirmed offline + restart remain DEVICE_PENDING. P0 launch currently BLOCKED by lock screen. M007 planning not started.

## WC-M006-R1 actual device export acceptance — 2026-11-24
User screenshot shows Export ready, Share Public Evidence Folder and native share sheet with ~4.4MB folder. DEVICE_VERIFIED: successful UI feedback and native share sheet presentation (progress transient not independently observed). Device tmp listing shows two complete export folders at21:20/21:21, each containing same two record UUIDs and five public files. Repeated preparation produces distinct folders. No need for user to repeat click.

Actual device-generated package copied successfully:
```sh
xcrun devicectl device info files --device 00008150-001251D23482401C --domain-type appDataContainer --domain-identifier com.example.workcam.dev --subdirectory tmp --recurse --timeout 20
xcrun devicectl device copy from --device 00008150-001251D23482401C --domain-type appDataContainer --domain-identifier com.example.workcam.dev --source tmp/WorkCam-Evidence-ABA3611A-65B4-416C-8B1F-DA5F4ADABC53 --destination /tmp/workcam-r1-device-export --timeout 30
```
Local files /tmp/workcam-r1-device-export; directory enumeration /tmp/workcam-r1-export-ready.log. No raw photo/package committed or uploaded.

DESKTOP_VERIFIED on BOTH actual exported records: exact allowlist photo.jpg,record.json,payload.bin,public-key.x963,signature.der; device payload.bin byte-for-byte equals independently reconstructed v1.1 canonical payload; SHA256 matches photo/manifest/record; X9.63 key and DER match manifest; key hash equals publicKeyID/device_id; OpenSSL SHA256 P256 verifies both signatures. XOR first byte on photo copy fails hash; substituting modified hash into payload fails original signature. Original evidence untouched. Report /tmp/workcam-r1-export-verification.json. OpenSSL public-key conversion and dgst commands are as documented in prior section; ran against device payload.bin this time, not reconstructed file. Only five explicit public files present; record schema contains no private-key reference/token. Not Windows W005-R1 executable; verifier is independent OpenSSL.

P0 deployment/real photo/export/math verification chain now has actual evidence. Remaining P2 hardware origin is DEVICE_PENDING: source uses SecureEnclave.P256.Signing.PrivateKey with no software fallback, and prior phone screenshot verifies matches installation key; no recorded first-key-creation telemetry/attestation. Cannot equate public signature validity to proven hardware origin. P3 LiDAR real known1m×5, orientation, recovery and P4 verified flight mode/new capture/kill-relaunch still pending. UI failure/empty-export behavior desktop-tested, not induced destructively on device. P0-P4 not all complete; M007 plan not started.

## 2026-11-25 WorkCam Mac Batch 02

基线 4676a30，隔离分支 codex/workcam-mac-batch-02。GitHub 连接器确认 PR #6、#9 为 open draft 且 #9 基于 #6。初次受限构建出现 sandbox_apply 与 SwiftUI StateMacro 错误。同命令在普通 macOS 权限下重试，基线 Debug/Release 成功。独立 worktree 候选同样执行以下命令。

```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination generic/platform=iOS -derivedDataPath /tmp/WorkCamBatch02-Debug CODE_SIGNING_ALLOWED=NO build
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Release -destination generic/platform=iOS -derivedDataPath /tmp/WorkCamBatch02-Release CODE_SIGNING_ALLOWED=NO build
xcrun swiftc -module-cache-path /tmp/workcam-batch02-module-cache Core/Security/Evidence.swift Tests/M003/main.swift -o /tmp/workcam-batch02-evidence-tests && /tmp/workcam-batch02-evidence-tests test-vectors/fixed-vectors.json
xcrun swiftc -module-cache-path /tmp/workcam-batch02-module-cache Features/TechLab/DepthGeometry.swift Features/TechLab/LabArchive.swift Tests/Lidar/main.swift -o /tmp/workcam-batch02-lidar-tests && /tmp/workcam-batch02-lidar-tests
xcrun devicectl list devices --timeout 5
```

设备枚举超时。无签名候选未安装，未产生本轮真机证据。详细状态见 WORKCAM-MAC-BATCH-02-REPORT.md。

候选 Debug 与 Release 均 BUILD SUCCEEDED，日志为 /tmp/workcam-batch02-Debug-candidate.log 与 /tmp/workcam-batch02-Release-candidate.log。macOS 证据回归六组通过，包含暂存恢复、重复恢复及不完整原图保留。LiDAR 桌面回归三组通过，覆盖合成坐标几何、归档和 CSV、v1→v2 迁移。

## 2026-11-25 Mac 研发统一调度回归

当前 managed worktree 起始为 detached 4676a30，工作区干净。创建 codex/workcam-mvp-integration 并继承已审计的 97b0c84。命令与实测如下。

```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination generic/platform=iOS -derivedDataPath /tmp/WorkCamUnified-Debug CODE_SIGNING_ALLOWED=NO build
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Release -destination generic/platform=iOS -derivedDataPath /tmp/WorkCamUnified-Release CODE_SIGNING_ALLOWED=NO build
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination generic/platform=iOS -derivedDataPath /tmp/WorkCamUnified-Signed build
xcrun swiftc -module-cache-path /tmp/workcam-unified-module-cache Core/Security/Evidence.swift Tests/M003/main.swift -o /tmp/workcam-unified-evidence && /tmp/workcam-unified-evidence test-vectors/fixed-vectors.json
xcrun swiftc -module-cache-path /tmp/workcam-unified-lidar-cache Features/TechLab/DepthGeometry.swift Features/TechLab/LabArchive.swift Tests/Lidar/main.swift -o /tmp/workcam-unified-lidar && /tmp/workcam-unified-lidar
xcrun devicectl list devices --timeout 10
```

无签名 Debug/Release 和签名 Debug 均 BUILD SUCCEEDED。桌面证据回归 6 组及 LiDAR 回归 3 组通过。设备枚举识别到已配对物理 iPhone。以上结果对应 97b0c84 继承基线。

配对设备连接复查命令为 `xcrun devicectl device info lockState --device "$wc_device" --timeout 10`，其中变量取自设备枚举的物理 iPhone 行。CoreDevice 4000，设备连接未建立。本轮未安装、启动候选或生成真机截图。已脱敏记录，设备标识仅留于本地临时日志。
## 2026-11-25 · WC-M023–M024 QA

从 97b0c8445793f2ed4110db84d80c540468a4b5d5 创建 codex/workcam-batch02-qa。实际命令如下。

```sh
git ls-remote origin refs/heads/dev/windows-prototype-w014
for c in Debug Release; do
  xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration "$c" -destination 'generic/platform=iOS' -derivedDataPath "/tmp/workcam-batch02-qa-$c" CODE_SIGNING_ALLOWED=NO build
done
xcrun swiftc -parse-as-library -module-cache-path /tmp/workcam-batch02-qa-modules Core/Sync/SyncOutbox.swift Tests/Sync/main.swift -o /tmp/workcam-batch02-qa-sync && /tmp/workcam-batch02-qa-sync
```

- Debug、Release 首次受限构建因 SwiftUI StateMacro 插件失败。解除沙箱限制重跑后均为 BUILD SUCCEEDED，日志 /tmp/workcam-batch02-qa-{Debug,Release}-retry.log。两份为 generic iOS 无签名构建。真机安装、权限、稳定性、性能与异常场景待 00 协调。
- Git CLI 无法解析 github.com。GitHub 连接器可读取 [Windows W013 分支头](https://github.com/zhltzh/workcam-ios/commit/31917e34dd45df095853db31faf242de3adac914) 和 [W014 同名分支头](https://github.com/zhltzh/workcam-ios/commit/23ca20af715735bb8730d0bfbd793cf6d1dcfb36)。后者提交主题为设计色板更新，部署候选 SHA 仍需交接。GitHub compare 本地基线与 W014 返回 404，联合冲突尚未验证。
- [W013 PR #19](https://github.com/zhltzh/workcam-ios/pull/19) 描述旧推导 token 拒绝、同公钥重注册保持身份及数据归属、SQLite 吊销与挑战路径收敛。该描述需在部署的固定 SHA 复验。验收需比较重新注册前后资源归属，验证旧凭据和已吊销凭据拒绝、重启后状态保持，以及 [W008 PR #14](https://github.com/zhltzh/workcam-ios/pull/14) 所述相同操作 ID 重传、不同内容冲突和批次部分确认。缺已审查部署和私网地址。
- 本地 Mock 队列桌面测试退出 0，覆盖重读、重复入队、ID 冲突和部分确认。队列独立编译验证，尚未接入 App target。Windows 网络同步、认证、事件游标和业务写入 Not Implemented。

总控复核 `xcrun xctrace list devices`，物理 iPhone 位于 Devices Offline。模拟器用以下命令通过 Debug 构建、安装和启动。截图在启动过渡后重新拍摄并目视核对首页。

```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/WorkCamUnified-Simulator CODE_SIGNING_ALLOWED=NO build
xcrun simctl list devices booted
xcrun simctl install "$wc_sim" /tmp/WorkCamUnified-Simulator/Build/Products/Debug-iphonesimulator/WorkCam.app
xcrun simctl launch "$wc_sim" com.example.workcam.dev
xcrun simctl io "$wc_sim" screenshot /Users/zhaohui/.codex/visualizations/2026/11/24/01a20f8b-b70f-7793-b626-b9e0f03bf6b2/workcam-baseline-simulator.png
xcrun swiftc -parse-as-library -module-cache-path /tmp/workcam-unified-module-cache Core/Sync/SyncOutbox.swift Tests/Sync/main.swift -o /tmp/workcam-unified-sync && /tmp/workcam-unified-sync
```

`wc_sim` 取已启动的 iPhone 18 Pro 模拟器。Mock 队列在集成分支复跑通过，未接入 App target。Git CLI `git ls-remote --heads origin` 返回传输超时，远端分支与 PR 用 GitHub 连接器核验。

## 2026-11-25 · M014–M016 core handoff

```sh
xcodebuild -version
xcrun swiftc -module-cache-path /tmp/workcam-core-cache Core/Security/Evidence.swift Tests/M003/main.swift -o /tmp/workcam-core-evidence && /tmp/workcam-core-evidence test-vectors/fixed-vectors.json
xcrun swiftc -module-cache-path /tmp/workcam-core-cache Features/TechLab/DepthGeometry.swift Features/TechLab/LabArchive.swift Tests/Lidar/main.swift -o /tmp/workcam-core-lidar && /tmp/workcam-core-lidar
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination generic/platform=iOS -derivedDataPath /tmp/WorkCamCore-Debug CODE_SIGNING_ALLOWED=NO build > /tmp/workcam-core-Debug.log 2>&1
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Release -destination generic/platform=iOS -derivedDataPath /tmp/WorkCamCore-Release CODE_SIGNING_ALLOWED=NO build > /tmp/workcam-core-Release.log 2>&1
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination generic/platform=iOS -derivedDataPath /tmp/WorkCamCore-Debug CODE_SIGNING_ALLOWED=NO build > /tmp/workcam-core-Debug-retry.log 2>&1
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Release -destination generic/platform=iOS -derivedDataPath /tmp/WorkCamCore-Release CODE_SIGNING_ALLOWED=NO build > /tmp/workcam-core-Release-retry.log 2>&1
git diff --check
```

Xcode 27.2 (27B52). Both macOS fixture suites passed. Evidence tests cover signed recovery, missing/mismatched/orphan DER, collision preservation and repeat recovery. Initial sandboxed iOS builds exited 65 with a malformed SwiftUI macro plugin response. Both unsigned retry builds outside the sandbox exited 0. Signed deployment and iPhone execution pending coordination with WorkCam 00. W016 analyzer SHA and invocation unavailable in local refs. Device and CSV steps are in [M014–M016 acceptance](ios/m014-m016-acceptance.md).

## 2026-11-25 · M018–M022 offline project candidate

- M018/M020: English project creation, rename, gallery, photo detail, original share and project camera save. Uses the existing AVFoundation permission flow. Project capture is local and unsigned. W017 retention and bulk operations: Not Implemented.
- M019: single project/date/unverified JPEG derivative. W011 device-time wording and original preservation applied. W011 template/field parity, content-addressed derivative records and geometry comparison: Not Implemented.
- M021: one editable human-added numbered marker and note per photo, with normalized coordinates after orientation correction. Remaining W013 shapes, issue history, before/after and W014 sync: Not Implemented.
- M022: site photo A4 PDF, one photo per page, marker and note, page count and unverified footer. Other W012 layouts and evidence appendix, W015 delivery ZIP and six samples: Not Implemented.

Builds used a clean writable staging copy of the requested baseline and these changes. The first sandboxed Debug run failed because the SwiftUI macro plugin server produced a malformed response. With escalated execution, both commands succeeded:

```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /private/tmp/wc-ui-Debug CODE_SIGNING_ALLOWED=NO build
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Release -destination 'generic/platform=iOS' -derivedDataPath /private/tmp/wc-ui-Release CODE_SIGNING_ALLOWED=NO build
```

Unsigned builds establish compilation only. Device capture, offline persistence
after restart, rendered output and device share are pending coordinated testing.

### 三组增量的集成回归

纳入 QA 2af79cb、核心 b8f4b5a、界面 2536165。总控将项目拍摄连到统一证据存储，项目只保留事务引用。按上节四条基线命令中的无签名 Debug、Release、模拟器构建与证据回归原样复跑。三种构建均 BUILD SUCCEEDED（日志 /tmp/workcam-unified-integrated-{Debug,Release,Simulator}.log）。证据回归七组通过，包含完整签名、缺失/不匹配/孤立 DER、目标冲突和重试。App 功能验收仍需真机。测试开关符号扫描命令为 `nm "$wc_binary" | rg -c 'tamperReport|LidarLabView'`，扫描 Debug、Release App 内 WorkCam 开头的可执行文件及 dylib；基线 Debug 有匹配，Release 无匹配。

候选在启动的 iPhone 18 Pro 模拟器执行 `xcrun simctl install "$wc_sim" /tmp/WorkCamUnified-Simulator/Build/Products/Debug-iphonesimulator/WorkCam.app` 与 `xcrun simctl launch --terminate-running-process "$wc_sim" com.example.workcam.dev` 成功。用 `xcrun simctl io "$wc_sim" screenshot "$wc_png"` 重拍启动后的首页，目视确认 Projects。路径见统一报告。

## 2026-11-25 WorkCam Night 03

Independent codex/workcam-mac-night-03 from 08cc07b. An early compile rejected
an NSNumber for the Swift flash-mode array. Changed to the typed enum and
rebuilt. Final code including Quick Look passed all four Xcode builds.

```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamNight03-Debug CODE_SIGNING_ALLOWED=NO build
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Release -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamNight03-Release CODE_SIGNING_ALLOWED=NO build
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/WorkCamNight03-Sim CODE_SIGNING_ALLOWED=NO build
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamNight03-Signed build
xcrun swiftc -module-cache-path /tmp/night03-module Core/Security/Evidence.swift Tests/M003/main.swift -o /tmp/night03-evidence && /tmp/night03-evidence test-vectors/fixed-vectors.json
xcrun swiftc -module-cache-path /tmp/night03-module Features/TechLab/DepthGeometry.swift Features/TechLab/LabArchive.swift Tests/Lidar/main.swift -o /tmp/night03-lidar && /tmp/night03-lidar
xcrun swiftc -parse-as-library -module-cache-path /tmp/night03-module Core/Sync/SyncOutbox.swift Tests/Sync/main.swift -o /tmp/night03-sync && /tmp/night03-sync
xcrun swiftc -module-cache-path /tmp/night03-module Core/Security/Evidence.swift Core/Storage/CaptureStore.swift "$wc_test/main.swift" -o "$wc_test/test" && "$wc_test/test"
xcrun xctrace list devices
```

Evidence regression 7 groups passed, LiDAR 3 groups passed, and Mock outbox
passed. The temporary archive fixture decoded legacy JSON without an archive
flag and verified archive-flag round trip with stable ID and name. Physical
iPhone listed offline. Xcode logs /tmp/workcam-night03-{Debug,Release,Sim,Signed}.log.

## Night 04 — 2026-11-25

Branch codex/workcam-mac-night-04 from 008dfea.

```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamNight04-Debug CODE_SIGNING_ALLOWED=NO build
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Release -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamNight04-Release CODE_SIGNING_ALLOWED=NO build
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/WorkCamNight04-Sim CODE_SIGNING_ALLOWED=NO build
xcrun swiftc -module-cache-path /tmp/night04-cache Core/Security/Evidence.swift Tests/M003/main.swift -o /tmp/night04-evidence && /tmp/night04-evidence test-vectors/fixed-vectors.json
xcrun swiftc -module-cache-path /tmp/night04-cache Features/TechLab/DepthGeometry.swift Features/TechLab/LabArchive.swift Tests/Lidar/main.swift -o /tmp/night04-lidar && /tmp/night04-lidar
xcrun swiftc -parse-as-library -module-cache-path /tmp/night04-cache Core/Sync/SyncOutbox.swift Tests/Sync/main.swift -o /tmp/night04-sync && /tmp/night04-sync
xcrun simctl install "$wc_sim" /tmp/WorkCamNight04-Sim/Build/Products/Debug-iphonesimulator/WorkCam.app
xcrun simctl launch --terminate-running-process "$wc_sim" com.example.workcam.dev --night04-fixture
xcrun simctl get_app_container "$wc_sim" com.example.workcam.dev data
xcrun simctl io "$wc_sim" screenshot "$wc_artifacts/workflow.png"
xcrun xctrace list devices
```

All three builds passed. Simulator used the booted iPhone 18 Pro, read the
fixture result from its temporary container and collected six PDFs, a JPEG
derivative and a screenshot. The fixture is synthetic. Physical capture,
Secure Enclave use and UI tap flows were not part of this run.

Evidence desktop regression passed nine groups and LiDAR three groups.
Physical iPhone was listed offline. Targeted nm scans found Lab and tamper
symbols in Debug and no matches for those or the fixture in Release.
