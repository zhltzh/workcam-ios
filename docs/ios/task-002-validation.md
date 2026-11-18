# Task 002 device acceptance checklist

Not executed: no connected iPhone was found. Compilation does not validate camera behavior.

1. Select a development team, use a unique Bundle ID and run on an unlocked iPhone with Developer Mode. Open Camera; permission should be requested only then.
2. Fresh install: deny access. Check disabled Capture, clear denial status and Open Settings. Grant access in Settings, return and check preview resumes. Also check restricted access where device policy permits.
3. Enable Airplane Mode; open camera and capture. Verify a unique JPEG exists in Application Support/Captures via Xcode device container download, can be decoded and has portrait orientation. Confirm no Photos permission or network is needed. Multiple captures must use distinct names.
4. Rapidly tap Capture; only one capture should be in flight. Navigate back during capture; check no crash and reopen camera. Background/foreground and lock/unlock; preview should stop while inactive and resume when active.
5. Exercise an interruption (another camera client/call), return and verify readiness. If a runtime error persists, close and reopen the camera.
6. Test low storage / file write failure: UI must report Save failed, never successful save. Test a device without a usable rear camera or simulator: show unavailable state with Capture disabled.
7. Inspect local files: captures excluded from backup, file protection complete. Verify no signing, verification, location, Photos export or cloud claim exists.

Task 002 deliberately limits UI to portrait, rear wide camera and JPEG. Photo gallery, deletion, export and signing are not implemented. Uninstalling the app removes locally saved photos. Captures consume storage until app data is removed; manage this before production use.
