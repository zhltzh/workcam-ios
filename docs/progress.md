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
