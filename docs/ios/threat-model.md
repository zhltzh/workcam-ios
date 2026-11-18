# Initial threat model

Task 002 stores unsigned camera JPEGs locally. No keys, signatures, location records or network traffic are implemented. Photos may contain sensitive site information; complete file protection and backup exclusion are configured, but no device-level verification has been performed. Files are not authenticated or tamper-evident. No export/deletion UX exists yet. Metadata originates from AVFoundation and is not a trusted time/location assertion.

Assets: original media, metadata, signing keys, device identity and verification results. Boundaries: sensor to app, app to local storage/Keychain, exported artifact to external verifier; future network transport.

Risks: altered media or metadata; replay/substitution; compromised device; key loss/extraction; forged time or GNSS position; offline clock rollback; location privacy; misleading verification UI. A signature alone cannot prove scene truth, trusted capture time or authentic GNSS. Core Location alone does not guarantee satellite-only positioning or resistance to spoofing. Offline positioning needs explicit field validation.

Future work must define canonical signed payloads, key lifecycle and recovery, verification failure semantics, export integrity, permission denial and storage protection. Secure Enclave, App Attest, server trust, sync and AR measurements are out of this task. No current authenticity guarantee is made.

M003 adds local cryptographic checks against accidental or partial modification, with public-key provenance limited to the stored record. An attacker replacing the entire record and key or stripping signing metadata can bypass provenance claims. No server registration or hardware attestation is provided. Device clock remains untrusted. Incomplete writes are isolated as pending folders. Hardware signing acceptance is Pending.
