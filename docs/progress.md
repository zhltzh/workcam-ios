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
