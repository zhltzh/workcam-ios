# WorkCam iOS-Backend Joint Debugging Handoff (WC-W005)

Windows backend is ready for Mac iOS joint debugging. This is the short,
authoritative reference. Where it conflicts with `ios-backend-integration.md`
(PR #4), **this document and the backend code on `main` win** — in particular
`/register-key` takes **query parameters**, not a form body.

## 1. Start the backend (Windows)

```powershell
cd backend
powershell -ExecutionPolicy Bypass -File ..\tools\start_backend.ps1   # port 8000
# or manually:
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then run the full self-check (no iPhone needed):

```powershell
.venv\Scripts\python.exe ..\tools\integration_check.py http://127.0.0.1:8000
# expect: "RESULT: 13 passed, 0 failed"
```

For the iPhone to reach the PC use `--host 0.0.0.0` and the PC's LAN IP.

## 2. API summary

| Endpoint | Method | Body / Params | Purpose |
|---|---|---|---|
| `/health` | GET | — | liveness |
| `/register-key` | POST | **query params** (see §3) | register device public key (TEST flow) |
| `/verify` | POST | JSON | verify signature manifest (+ optional photo) |
| `/store-record` | POST | JSON | store verified evidence (replay detection) |
| `/check-record/{id}` | GET | — | record existence |
| `/time-anchor/anchor` | POST | JSON | request signed time anchor |
| `/time-anchor/verify` | POST | JSON (+ `?check_storage=false` to skip storage check) | verify anchor receipt |
| `/time-anchor/public-key` | GET | — | server public key + key_id |

## 3. Register the device public key

Params go in the **query string** (backend declares scalar params; FastAPI
binds them from the query). URL-encode the Base64 (`+`→`%2B`, `=`→`%3D`,
`/`→`%2F`):

```
POST /register-key?public_key_b64=<urlencoded>&device_id=<id>&allow_test_registration=true
```

```json
{ "public_key_hash": "163d...", "registration_status": "registered",
  "device_id": "iphone-pro-001", "environment": "development",
  "warning": "This is a TEST registration. Production will use App Attest formal registration." }
```

`device_id` must equal the `device_id` inside every manifest signed by that
key, otherwise `device_identity_consistent=false` and evidence is INVALID.
Duplicate same key + same device_id is idempotent (`ALREADY_REGISTERED`);
same key + different device_id is rejected (`DEVICE_ID_CONFLICT`).

## 4. Evidence verification (`POST /verify`)

Body: `{"manifest": {...}, "photo_base64": "<base64 of the photo FILE bytes>"}`.

Manifest JSON fields (snake_case): `version` ("1.1"), `record_id`,
`sequence_number`, `previous_record_hash` (null for genesis),
`photo_hash` (lowercase hex SHA-256 of the saved file),
`capture_timestamp`, `device_reported_time` (ISO 8601),
`device_id`, `location` (null for M003), `signature` (Base64 DER),
`public_key` (Base64 X9.63, 65 bytes starting `0x04`).

Key response fields:

```json
{
  "evidence_status": "cryptographically_valid",
  "cryptographic_signature_valid": true,
  "photo_hash_valid": true,
  "key_registration_status": "registered",
  "device_identity_consistent": true,
  "device_attestation_status": "unknown",
  "time_trust_level": "device_reported",
  "warnings": ["..."]
}
```

**Trust levels are NOT the same thing**:

| Signal | Field |
|---|---|
| signature math | `cryptographic_signature_valid` |
| key registered | `key_registration_status` |
| device identity proven | `device_identity_consistent` |
| photo bytes verified | `photo_hash_valid` |
| time server-anchored | only after `/time-anchor/...`, and only proves the digest existed before `server_received_at` |

Unregistered key can never exceed `signature_valid`. Without App Attest the
ceiling is `cryptographically_valid`, never `fully_attested`.

## 5. Store evidence (`POST /store-record`)

Same body as `/verify` but `photo_base64` is **mandatory**. Rejects:
`photo_missing`, `photo_empty`, `invalid_signature`, `unregistered_key`,
`device_identity_mismatch`, `photo_hash_mismatch`. Identical resubmission →
idempotent duplicate; same `record_id` with different content → rejected
(content is signed). Invalid evidence never occupies `record_id`.

## 6. Time anchor

```json
POST /time-anchor/anchor
{ "batch_id": "batch-001", "chain_head_hash": "<64 hex>", "chain_tail_hash": "<64 hex>",
  "record_count": 3, "device_reported_start_time": "2026-11-19T08:00:00Z",
  "device_reported_end_time": "2026-11-19T09:00:00Z" }
```

Response = `TimeAnchorResponse`. To call `/time-anchor/verify`, build the
full `TimeAnchorReceipt` by copying the response **plus**
`device_reported_start_time`, `device_reported_end_time` (from your request)
and `stored_at` (= `server_received_at`). `verification_status` is part of
the v2 signature - tampering it breaks the signature.

Note: `?check_storage=false` still requires network (it only skips the
storage cross-check). True offline verification = local CryptoKit check of
the receipt signature against the pre-trusted server public key from
`/time-anchor/public-key`.

## 7. Test vectors

`test-vectors/fixed-vectors.json` - one fixed key pair, photo bytes, canonical
payload (hex), valid DER signature, plus negative vectors (tampered photo /
payload, wrong signature, invalid DER, invalid timestamp, invalid public
key). All keys are software TEST keys. Swift usage steps are inside the file
under `swift_verification`.

## 8. Mac joint-debugging steps

1. Start backend (§1), run `integration_check.py`, confirm 13/13.
2. In Swift, verify the fixed vectors first (`public_key.base64` →
   `P256.Signing.PublicKey(x963:)`; `canonical_payload.hex` → bytes;
   `signature.base64` → `ECDSASignature(derRepresentation:)` → must verify).
3. Register the M003 key: `device_id` must equal the value used in
   `EvidenceManifest.device_id` (Mac currently uses
   `sha256(publicKey.x963Representation)` hex).
4. Capture → `photo_hash` over the **saved file bytes** → sign canonical
   payload (v1.1) → POST `/verify` with photo → expect
   `cryptographically_valid`.
5. POST `/store-record`; retry to confirm the duplicate path.
6. POST `/time-anchor/anchor` → build receipt → `/time-anchor/verify`.

## 9. Protocol compatibility with Mac PR #6 (W005-2 conclusion)

Verified `Core/Security/Evidence.swift` against backend code and the fixed
vectors — **compatible, no backend changes required**:

| Check | Mac implementation | Backend expectation | Result |
|---|---|---|---|
| public_key | `publicKey.x963Representation` + Base64 | 65-byte X9.63, `0x04` prefix | OK |
| signature | `signature.derRepresentation` + Base64 | DER + Base64 | OK |
| photo_hash | SHA-256 hex of saved file | lowercase 64-hex of file bytes | OK |
| canonical payload | 9 fields, `\n`-joined, trailing `\n`, UTF-8, domain `WorkCam-Sig-v1.1` | identical `FIELD_ORDER` | OK |
| timestamps | `ISO8601DateFormatter` (`...Z`) | `ISO8601_REGEX` accepts `Z` | OK |
| location | `nil` only (M003) | `null` → `location=none` | OK |
| genesis | seq=0, prev=null | chain rules satisfied | OK |

One caveat: PR #4's `/register-key` "form-urlencoded" description is wrong
(§3); Mac must use query parameters as documented here.

## 10. Not implemented / limitations

- No App Attest: `device_attestation_status` always `unknown`; ceiling is
  `cryptographically_valid`, never `fully_attested`.
- Time anchor proves digest existence before `server_received_at` only —
  never the real capture time; unanchored offline chains cannot prove
  immutability.
- Key registry / record store are in-memory per process; time anchors persist
  to a JSON file only when `WORKCAM_PERSIST` is set. Restarting the plain
  `uvicorn` command loses registered keys and stored records (anchors survive
  with persistence enabled).
- `/register-key` is a TEST endpoint: disabled in production
  (`WORKCAM_ENV=production`) and requires `allow_test_registration=true`.
- No database, no accounts, no cloud sync in this round.
- Real iPhone joint debugging has NOT been performed yet (Windows-side
  tooling and vectors are ready for it).
