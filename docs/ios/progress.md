# WC-001 / Task 001 — 2026-11-07

## Environment and compatibility
- macOS 26.6 (25G72), arm64.
- Xcode 27.2 (27B52), selected at /Applications/Xcode.app/Contents/Developer.
- Apple Swift 6.4.2; project Swift language mode 6.
- iPhoneOS and iPhoneSimulator SDK 27.2 installed.
- Git initial state: main, no commits, empty working tree.
- Minimum deployment iOS 17.0; toolchain successfully compiles arm64 for this target. Physical iOS 17 compatibility not tested.

## Actual validation
Executed both commands (unsigned device SDK builds):

```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamDerivedData CODE_SIGNING_ALLOWED=NO build
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCamDerivedData CODE_SIGNING_ALLOWED=NO build
```

Both returned BUILD SUCCEEDED. Logs: /tmp/workcam-build.log and /tmp/workcam-release.log (temporary local files). These establish compilation/linking only; no runtime or security validation. No automated test target or test cases created for this static shell. UI has not been visually checked on device/simulator.

`xcrun devicectl list devices` initially timed out under sandbox with CoreDeviceService initialization failure; authorized retry outside sandbox returned `No devices found.` No phone was available, so installation/launch could not be attempted. No Development Team configured; signed device build not attempted. Connect/unlock/trust an iPhone running iOS 17+, enable Developer Mode, select a team and a unique bundle ID in Xcode, then Run and check the English status screen. Camera/location permissions are intentionally absent because no capture/location service runs.

## Delivered / remaining
Delivered native Xcode project, separate configuration files, SwiftUI shell, reserved module directories and initial documentation. All three statuses and Capture explicitly show Not Implemented. No photos, keys, signatures, location, storage, sync, verification, AR or payment implemented.

Task 002 suggestion: review and authorize minimal offline camera preview/capture with permission denial and lifecycle handling; define acceptance checks on a physical phone. Await review; Task 002 has not begun.

No Git commit created by this task.

## WC-001 / Task 002 — 2026-11-08

Authorized by user after scope clarification. Added CameraController, CameraView/CameraModel/preview and CaptureStore; updated home navigation, permission usage description and portrait orientation. No signing or location added.

Actual commands:
```sh
xcrun devicectl list devices
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCam002 CODE_SIGNING_ALLOWED=NO build
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCam002 CODE_SIGNING_ALLOWED=NO build
```

Device query outside sandbox: No devices found. No installation, camera capture, offline behavior or lifecycle test could be executed. Follow task-002-validation.md on a real iPhone with a signing team configured. Simulator has no physical rear camera and cannot establish capture acceptance.

First Debug attempt failed due to a notification type qualification error (fixed) and sandbox SwiftUI macro service failure. Retried outside sandbox. Final build results recorded below. No test target or security verification added. No commit created. Task 003 has not started.

Final Debug and Release unsigned arm64 device builds both returned BUILD SUCCEEDED after the final code change. Logs: /tmp/workcam002-debug.log and /tmp/workcam002-release.log. Non-blocking App Intents metadata extraction warning: no AppIntents dependency. Generated Debug Info.plist checked using PlistBuddy: NSCameraUsageDescription contains the English purpose string; UISupportedInterfaceOrientations contains portrait. This does not establish real-device permission or camera behavior.

## WC-M-SYNC-001 — 2026-11-17

In the independent clone, ran:

```sh
xcodebuild -project WorkCam.xcodeproj -scheme WorkCam -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath /tmp/WorkCam-MacSync-DerivedData CODE_SIGNING_ALLOWED=NO build
```

Result: BUILD SUCCEEDED. Unsigned compile/link validation only. iPhone installation and camera acceptance remain pending.

## WC-M003 — 2026-11-19

Latest main fetched successfully. Local main fast-forwarded to 17d9699; independent dev/mac-evidence-m003 created, original branch retained. Added evidence hashing, Secure Enclave key reference persistence, DER signatures, protected staged local records and a debug verification entry. See m003.md for exact build/test commands, PASS results, protocol limitations and Pending hardware acceptance. Backend unchanged. No production network synchronization added.
