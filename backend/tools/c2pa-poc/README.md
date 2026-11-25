# B02-W019 C2PA PoC (independent experiment, not wired into any production code)

Independent proof-of-concept for C2PA (Content Credentials) compatibility research.
This directory is **not imported by any WorkCam production module**, and the PoC
uses its own virtual environment so the backend `.venv` stays at baseline.

## What the PoC does

`poc_c2pa.py` runs the following steps and records real outcomes in
`output/poc_results.json`:

1. Generate a 320x240 test JPEG (Pillow).
2. Generate a test-only mini PKI **in memory** (self-signed test CA + leaf
   signing cert, P-256, EKU id-kp-documentSigning). Private keys are never
   written to disk or committed.
3. Try to sign with default SDK settings (self-signed certs are rejected —
   honest record of SDK trust enforcement).
4. Sign the JPEG offline (ES256, no TSA) with the PoC CA installed as a trust
   anchor via `Settings`.
5. Read back with no trust anchors (state: `Valid`) and with the PoC CA
   anchor (state: `Trusted`).
6. Flip one byte of the signed file and re-read (hard-binding tamper check →
   `Invalid`).
7. Attempt an online RFC 3161 timestamp (TSA `https://timestamp.digicert.com`)
   — network result is recorded honestly (failed in our sandbox: the SDK's
   native HTTP client does not use the Windows proxy).
8. Create a derivative: rotate the plain JPEG and sign it with the signed JPEG
   as a `parentOf` ingredient (ingredient/derivative chain experiment).
9. Sign a 4000x3000 (12MP-class) JPEG to measure overhead and timing on a
   realistic file size.
10. HEIC round-trip: encode the test image to HEIC (pillow-heif), sign and
    read back (container-format experiment).

## How to run

```powershell
# one-time setup (uses the local proxy for pip)
backend\.venv\Scripts\python.exe -m venv backend\tools\c2pa-poc\.venv-poc
backend\tools\c2pa-poc\.venv-poc\Scripts\python.exe -m pip install --proxy http://127.0.0.1:10809 c2pa-python pillow pillow-heif

# run
backend\tools\c2pa-poc\.venv-poc\Scripts\python.exe backend\tools\c2pa-poc\poc_c2pa.py
```

Environment used for the recorded run (2026-11-25):

- Python 3.12.10 (Windows)
- c2pa-python 0.39.0 → c2pa-rs SDK 0.92.0
- Pillow 13.0.0, pillow-heif 1.9.0
- cryptography (pulled by c2pa-python)

## Real outputs (run of 2026-11-25)

```
SDK: c2pa 0.92.0, binding c2pa-python 0.39.0
[step] generate_test_jpeg: {"bytes": 5355, "sha256": "5dde5ba0d20ad2ab..."}
[step] generate_test_key: {"curve": "secp256r1 (P-256)",
        "structure": "test CA (self-signed) + leaf signing cert issued by CA",
        "leaf_eku": "id-kp-documentSigning (1.3.6.1.5.5.7.3.36)",
        "written_to_disk": false}
[step] sign_no_trust_settings: {"rejected": false, "unexpected": true}
[step] sign_offline: {"src_bytes": 5355, "dst_bytes": 40684,
        "overhead_bytes": 35329, "overhead_pct": 659.74, "wall_ms": 56.2,
        "tsa": "none (offline)"}
[step] read_back_no_trust: {"validation_state": "Valid", "read_wall_ms": 3.6,
        "active_manifest_found": true}
[step] read_back_with_poc_trust: {"validation_state": "Trusted", "read_wall_ms": 3.9}
[step] tamper_one_byte: {"reader_raised": false, "validation_state_after_tamper": "Invalid"}
[step] online_tsa_attempt: {"ok": false, "error_type": "_C2paSignature",
        "error": "Signature: internal error (an error occurred from the underlying
        http resolver: error sending request for url (https://timestamp.digicert.com/))"}
[step] derivative_ingredient_test: {"ok": true, "validation_state": "Trusted",
        "ingredient_chain_depth": 1}
[step] large_image_perf: {"plain_bytes": 322116, "signed_bytes": 358447,
        "overhead_bytes": 36331, "overhead_pct": 11.279, "sign_wall_ms": 242.8,
        "read_wall_ms": 7.5}
[step] heic_roundtrip: {"ok": true, "plain_bytes": 19593,
        "signed_bytes": 23483, "sign_wall_ms": 42.2, "read_wall_ms": 11.4,
        "validation_state": "Trusted"}
```

Note: the earlier iterations of this PoC (also recorded, see study report)
show that the SDK rejects a self-signed leaf certificate with
`Signature: the certificate was self-signed`, and rejects a CA+leaf chain
whose CA lacks a SubjectKeyIdentifier / leaf lacks AuthorityKeyIdentifier
with `Signature: the certificate is invalid`. Both match the certificate
profile requirements of C2PA spec 2.5 §13.5.1.

## Findings captured by the PoC (evidence for the study report)

| Finding | Evidence |
|---|---|
| Offline (no TSA) signing works; timestamp is optional at sign time | `sign_offline` step, `tsa: none (offline)` |
| Trust model is explicit: `Valid` without anchors, `Trusted` with the signer chain anchored | `read_back_no_trust` vs `read_back_with_poc_trust` |
| Hard binding detects a single flipped byte | `tamper_one_byte` → `Invalid` |
| Ingredient/derivative chain works via `parentOf` + `set_intent(EDIT)` | `derivative_ingredient_test` |
| Manifest overhead is dominated by the certificate chain (~35-36 KB with 2 small test certs), roughly constant across file sizes | 5,355 B → +35,329 B; 322,116 B → +36,331 B |
| Signing cost is dominated by hashing the payload: ~56 ms (5 KB) vs ~243 ms (322 KB) on this machine | `sign_offline` vs `large_image_perf` |
| HEIC container embed works (ISO-BMFF path) | `heic_roundtrip` → `Trusted` |
| SDK online TSA call does not honor the Windows user proxy; direct connection blocked in this sandbox | `online_tsa_attempt` error |
| SDK enforces the C2PA certificate profile at signing time (no self-signed leaf; SKI/AKI required) | iteration history, quoted above |

## Compliance notes / red lines

- This PoC is a **spec-conformance experiment only**. C2PA has not certified,
  audited, or endorsed WorkCam; nothing here may be described as "C2PA
  certified".
- Test private keys are generated in memory only and never persisted
  (AGENTS.md: never commit signing secrets).
- No production evidence-protocol code was modified (backend production code
  untouched; `requirements.txt` untouched; backend `.venv` restored to
  baseline after the experiment).
- Output binaries (jpg/heic) are not committed; only the JSON evidence files
  (`output/poc_results.json`, `output/manifest_definition.json`,
  `output/manifest_read_trusted.json`) are committed for inspection.

## Files

| File | Purpose |
|---|---|
| `poc_c2pa.py` | The PoC script (10 steps above) |
| `output/poc_results.json` | Machine-readable record of the run (all steps) |
| `output/manifest_definition.json` | The manifest definition used for signing |
| `output/manifest_read_trusted.json` | Manifest as read back from the signed JPEG (Trusted state) |
| `.gitignore` | Keeps `.venv-poc/` and output binaries out of git |
