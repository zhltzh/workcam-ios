# Architecture

Task 002: WorkCamApp → ContentView → CameraView. CameraModel publishes UI state on MainActor; CameraController confines capture configuration, start/stop, capture requests and file writes to one serial queue. Session is exposed to AVCaptureVideoPreviewLayer solely for preview. Notification observers handle interruptions and runtime errors. Single in-flight capture prevents overlapping requests.

Core/Storage/CaptureStore writes original AVFoundation JPEG bytes atomically to Application Support/Captures with UUID names, complete file protection and backup exclusion. No image re-encoding, metadata augmentation, location lookup, signature, history database or network is added. UI displays filename only after write succeeds. Captures are unsigned and not verified. Rear wide camera, fixed portrait orientation; no front camera, zoom or flash UI.

Only entering Camera requests permission. Page disappearance or inactive scene stops session; returning active rechecks authorization and starts session. Runtime error asks user to close/reopen camera; interruption end attempts restart. Permission denied/restricted leaves Capture disabled and offers Settings.

Reserved modules: CaptureHistory, Verification, Security, Location, Sync, Models, Resources and Tests. No third-party dependencies. Device acceptance steps: task-002-validation.md. Unit/UI automated tests are not yet configured; physical capture remains unvalidated.

M003 routes new JPEG captures to EvidenceStore. Protected pending directories become committed records after photo, JSON and optional DER writes. Evidence Debug reloads and verifies hashes/signatures on demand. Keychain persists the Secure Enclave opaque reference. See m003.md for independent-genesis scope and supported null location.

## Experimental clock diagnostics (2026-11-19)
Standalone Core/TechLab/ClockObservation.swift has no app integration or network dependencies. Session-scoped uptime comparisons diagnose wall-clock divergence; session boundaries prohibit continuity claims. Internal Codable is not a negotiated Windows schema.

Debug-only LiDAR Lab uses an isolated ARSCNView session, explicit user Start, scene-depth support gating, estimated-plane raycasts and world-space point markers. Stops session on leaving screen. Existing capture/evidence pipeline unchanged. CSV uses a provisional internal schema, not yet Windows-compatible; sharing is user initiated.

M1-R1 partial: depth modes unproject display-mapped depth pixel centers with scaled intrinsics and camera transform; high-confidence depth only, no fallback. Experimental rows are atomically archived separately from evidence. Schema and matrix compatibility concerns recorded in progress.md.

WC-M-006 public evidence export creates explicit copies of JPEG, EvidenceRecord, exact canonical payload, X9.63 public key and DER signature. Export verifies source mathematics first; no private key access in export path. Dedicated fixed-vector allowlist/byte/tamper tests added.

Evidence capture, re-verification and export retry promotion of complete pending
transactions. Promotion checks the directory identifier, original hash, and
signed record plus detached DER when present. Write and promotion are serialized
within the app process. Incomplete pending originals remain isolated for inspection.

### WC-M023 local sync outbox

The standalone Swift outbox atomically persists ordered operation IDs and opaque
bodies. One actor owns each file. A caller supplies the stable ID and removes an
operation only after an accepted or already-applied acknowledgment. The desktop
fixture uses Mock bodies. App wiring, network transport, and event cursors are
Not Implemented.
