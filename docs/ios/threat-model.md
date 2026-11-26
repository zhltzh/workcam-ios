# Initial threat model

Task 002 stores unsigned camera JPEGs locally. No keys, signatures, location records or network traffic are implemented. Photos may contain sensitive site information; complete file protection and backup exclusion are configured, but no device-level verification has been performed. Files are not authenticated or tamper-evident. No export/deletion UX exists yet. Metadata originates from AVFoundation and is not a trusted time/location assertion.

Assets: original media, metadata, signing keys, device identity and verification results. Boundaries: sensor to app, app to local storage/Keychain, exported artifact to external verifier; future network transport.

Risks: altered media or metadata; replay/substitution; compromised device; key loss/extraction; forged time or GNSS position; offline clock rollback; location privacy; misleading verification UI. A signature alone cannot prove scene truth, trusted capture time or authentic GNSS. Core Location alone does not guarantee satellite-only positioning or resistance to spoofing. Offline positioning needs explicit field validation.

Future work must define canonical signed payloads, key lifecycle and recovery, verification failure semantics, export integrity, permission denial and storage protection. Secure Enclave, App Attest, server trust, sync and AR measurements are out of this task. No current authenticity guarantee is made.

M003 adds local cryptographic checks against accidental or partial modification, with public-key provenance limited to the stored record. An attacker replacing the entire record and key or stripping signing metadata can bypass provenance claims. No server registration or hardware attestation is provided. Device clock remains untrusted. Incomplete writes are isolated as pending folders. Hardware signing acceptance is Pending.

## Experimental time observations
Wall clock and persisted observations are untrusted. Uptime deltas can flag changes within a session but cannot authenticate time, establish reboot continuity or detect every manipulation. Diagnostics never elevate evidence trust. Server anchor validation is Not Implemented in this module.

LiDAR Lab raycasts are estimates, not raw depth-derived distance or verified engineering measurements. Tracking loss discards the first point. Exported rows and reference distances are untrusted; CSV contains device-reported time. No signatures, coordinate authenticity or precision claims. Experimental camera session only starts on explicit action.

Depth observations remain unverified estimates. Confidence filtering does not establish accuracy. Corrupt experimental archive is preserved rather than silently replaced. Local JSON and CSV are unsigned, user-editable experiment records.

Exported signature validity alone does not prove Secure Enclave provenance or capture authenticity. Public package contains personal photo/metadata; user initiates sharing. Fixed-vector export acceptance is desktop-only. Hardware key origin still requires device-side observations.

Pending transaction recovery preserves incomplete or unreadable originals and
never replaces an existing committed destination. Recovery verifies record
integrity. A recovered unsigned record has no signing claim, and signature
verification alone does not establish remote hardware provenance.

### WC-M023 local sync outbox

The standalone outbox file contains operation IDs and caller-provided bodies.
Callers must keep credentials out of those bodies and control where the file is
stored. Local acknowledgments do not establish server acceptance or evidence
verification. Network authentication and credential lifecycle are Not Implemented.

A detached DER file alongside unsigned pending metadata is inconsistent.
Recovery retains the original and the conflicting artifact in staging.
Remote hardware provenance remains NOT_ATTESTED.

## Offline project artifacts

Treat project names and annotation notes as user-provided content. Treat project
timestamps as unverified device time. New originals use evidence capture.
Project membership and marker sidecars are mutable local metadata outside
the signature. Legacy project JPEGs and delivery files carry no signature claim. The JPEG and PDF outputs
include fixed unverified labels. The original-share action sends the camera
JPEG with its embedded metadata. The system share sheet can transfer data out
of the app sandbox. Temporary export files can persist until system cleanup.

Night 03 archive state changes only the project record and preserves photos
and evidence. Watermark output is marked derivative and unverified, with
device timestamps explicitly unverified. Camera flash and lens controls
report only locally enumerated capability. Evidence detail uses the existing
local inspection path and displays NOT_ATTESTED for remote hardware origin.

A capture intent is a local crash-recovery record. It carries no signing or time
claim. Recovery does not replace conflicting committed files or reinterpret
mismatching originals. Failed exports remove their provisional export folder.
The synthetic business workflow is compiled only for Debug Simulator and runs
with isolated storage and an explicit launch argument.

Project membership, annotation styles and issue history are mutable business
metadata outside the photo signature. The local issue history is an operation
record. Photo role associations are human-entered. ZIP CRC and SHA-256
inventories can detect corruption independently after extraction. A recipient
seeking authenticity must independently verify the signed record, its photo
binding and a trusted key. No remote hardware provenance is established.
