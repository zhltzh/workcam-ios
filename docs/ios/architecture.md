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

An unsigned pending record with a detached DER file stays isolated for
inspection. The committed-record inspector applies the same consistency check.

## Offline project capture

Projects use UUID directories under Application Support/WorkProjects. Project
metadata is JSON. A new directory is staged then renamed into place. Project
capture reserves a project reference and commits the original through the same
evidence store used by standalone capture. The project list resolves each
reference after evidence promotion. The original is stored once. A reference
without a committed original stays out of the list and can resolve when
recovery promotes a complete transaction. Older project JPEGs remain readable.
The timestamp represents the device clock at the time of save.

A human-added marker and note use a separate JSON sidecar. ImageIO applies
orientation before resolving normalized coordinates. The watermark and A4
site-photo PDF render from the originals and sidecars into separate temporary
share files. Exported originals retain the camera JPEG bytes.

Night 03 adds rear lens discovery and serialized input switching, a bounded
thumbnail for the most recent saved capture, project archive metadata with
legacy decoding, and local evidence inspection in project photo detail.
Watermark preferences are stored locally. Rendering reads the original into
an orientation-corrected bounded image and writes a separate protected JPEG.
Site Photo PDF supports A4 and US Letter with system Quick Look previews.

Night 04 stages a SHA-256 capture intent before writing the original. Recovery
can promote a matching photo without signing metadata as unsigned. A new
transaction commits its signed manifest before the detached DER; recovery can
recreate a missing DER from a verified manifest and matching capture intent.
Annotation sidecars extend the legacy marker with an optional shape and
normalized points. Screen overlays, JPEG derivatives and PDF pages share the
same annotation renderer.

Night 05 stages project membership in the same directory as evidence capture
and publishes the directory with one rename. Project browsing also resolves
legacy external references and keeps their annotation paths. Multiple
annotations use an atomic collection sidecar. Legacy single-marker sidecars
are used only before a collection is saved. The issue store keeps snapshots
of state, description, severity and photo roles. Delivery archives include
originals, generated copies, annotation and issue data, reports, evidence
metadata and SHA-256 inventories.

Integration 01 keeps the current evidence wire format. The export and
report paths check that committed directory identity, original bytes and
detached signature agree. ZIP preflights filesystem entries and includes
local headers, central directory and trailer in its serialized byte cap.
TM02 and TM32 parity runners are standalone desktop checks.

Integration 02 adds an opt-in Backend Sync screen and a URLSession client.
Endpoint-scoped credentials live in Keychain; the atomic protected journal
stores frozen requests, dependencies, ID mappings and confirmed IDs.
Chunk uploads hash read-only originals and reconcile server chunk indexes on
retry. Only accepted receipts or confirmed original integrity advance the
journal. TM01/TM10/TM12 diagnostics remain outside the app target.

Super 01 adds a per-endpoint, per-filter server inbox. Each page commits its
cursor and latest server snapshots in one protected atomic file. The UI
shows snapshots separately from editable local project files, and queues
explicit remote project rename/archive operations with expected revisions.

Super 02 schedules Outbox jobs by known server references, explicit
dependencies and relative order within an entity. Deterministic failures
stay in the journal while independent jobs can complete. Association is
a user-confirmed endpoint-scoped mapping and stores revision/provenance.
Remote issue controls use the versioned batch API and role comparison.

## KEY-01 camera controls

The preview layer converts a tap from view coordinates to an AVFoundation
device point. The camera queue applies focus and exposure independently when
the active lens supports them. Pinch updates zoom within the active device's
available range. The camera queue also coalesces outstanding authorization
requests and retries startup after a media-services reset while the camera is
active. Capture orientation remains portrait. These controls leave the original
JPEG save and evidence pipeline unchanged.
