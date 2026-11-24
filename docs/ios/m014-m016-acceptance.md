# M014–M016 device acceptance and W016 handoff

Record the candidate SHA, configuration, iPhone model, iOS version and result for each step. Use a dedicated test iPhone and photos without personal data. Coordinate device access and deployment with WorkCam 00.

## M014 · Offline capture and recovery

1. Install the signed Debug candidate over the existing test installation. Record existing evidence IDs and original SHA-256 values.
2. Enable Airplane Mode, disable Wi-Fi, then capture two photos. Reverify and export. Record each evidence ID, original hash and displayed status.
3. Lock and unlock, force-quit and relaunch, then restart and unlock. At each checkpoint, reverify and compare the IDs and hashes. Record when each interrupted capture was triggered and whether the interruption actually landed in the write window.
4. In an isolated fault-injection environment, exercise low storage, failed DER write, and interruption before metadata and before promotion. Complete stages should recover once. Partial, contradictory or colliding stages should retain the original bytes. Record failed writes separately from successfully staged originals.

Device acceptance pending. Location and synchronization are Not Implemented.

## M015 · Key continuity

For the same test installation, compare the SHA-256 fingerprints of exported X9.63 public keys from two offline photos and a fresh post-restart capture. Reverify the earlier captures after first unlock. Check the exported signatures independently:

```sh
{ printf '3059301306072a8648ce3d020106082a8648ce3d030107034200' | xxd -r -p; cat public-key.x963; } > public-key.spki.der
openssl dgst -sha256 -verify public-key.spki.der -keyform DER -signature signature.der payload.bin
```

Record signature validity and key continuity. Remote key hardware provenance remains `NOT_ATTESTED`. Device registration and App Attest binding are Not Implemented.

## M016 · Debug LiDAR and W016

Mark two physical endpoints and measure their separation with a tape. Run five attempts per mode at a 1 m reference, then repeat for changed angles or surfaces under separate test conditions. Keep failed attempts in the export. Record the device, lighting, material, angle and orientation. Force-quit and confirm the archived rows remain available after relaunch. Confirm the Lab entry is absent from a signed Release device build.

The current interface is a user-exported CSV for the selected batch. Send the raw CSV plus the acquisition notes to W016 once its analyzer revision and invocation are confirmed. Parse quoted CSV fields with a CSV parser. Group by mode and condition, count attempts and failures, and report valid sample counts, mean absolute error in meters, and maximum absolute error per group. Use `distance_true_m` and `distance_measured_m` for error, with blank numeric fields treated as missing. `relative_error` is a fraction. Preserve `batch_id`, `test_id`, `device_time`, `first_device_time`, tracking and confidence fields for traceability. Lab time is device-reported.

W016 compatibility and analysis of actual device samples are pending. Runtime analyzer integration is Not Implemented. The local CSV schema is provisional.
