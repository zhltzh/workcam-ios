# WorkCam Security Review — WC-W002 (Updated with R1, R2, and R3)

## Summary

WC-W002 addresses security issues identified in WC-W001. WC-W002-R1 addresses review feedback on WC-W002. WC-W002-R2 addresses final review feedback including cross-language test vectors and record storage security. WC-W002-R3 addresses merge-blocking issues including mandatory photo validation and key registration protection.

## Test Results

```
93 passed, 1 warning in 1.24s
```

## WC-W002-R3 Fixes (Merge-Blocking)

### 1. Mandatory Photo Validation in /store-record

**Problem**: /store-record accepted requests without photo_base64, allowing records to be stored without verifying photo integrity.

**Fix**:
- photo_base64 is now MANDATORY (cannot be None)
- Empty string photo_base64 is explicitly rejected
- Photo hash MUST match manifest.photo_hash (not optional)
- Returns clear rejection_reason for each failure:
  - `photo_missing`: photo_base64 field not provided
  - `photo_empty`: photo_base64 is empty string
  - `photo_hash_mismatch`: provided photo doesn't match hash
- Zero-byte photos are supported if explicitly provided and hash matches
- Invalid evidence does NOT occupy record_id

**Tests Added**:
- `test_store_record_requires_photo`: Verifies missing photo is rejected
- `test_store_record_rejects_empty_photo`: Verifies empty string is rejected
- `test_store_record_rejects_photo_hash_mismatch`: Verifies hash mismatch is rejected

### 2. Protected Key Registration Interface

**Problem**: /register-key allowed unauthenticated external calls to register or overwrite device public keys.

**Fix**:
- Added `allow_test_registration` parameter (must be explicitly true)
- Added environment check via `WORKCAM_ENV` environment variable
- Production environment (`WORKCAM_ENV=production`) disables test registration entirely
- Returns clear error messages for each rejection case:
  - `TEST_REGISTRATION_NOT_ALLOWED`: Missing allow_test_registration flag
  - `REGISTER_DISABLED`: Production environment
  - `DEVICE_ID_CONFLICT`: Key already registered to different device
  - `ALREADY_REGISTERED`: Idempotent registration (same key+device)
- Prevents overwriting existing registrations
- Clear distinction between test registration and future App Attest formal flow

**Tests Added**:
- `test_register_key_requires_explicit_flag`: Verifies flag is required
- `test_register_key_with_flag`: Verifies registration works with flag
- `test_register_key_idempotent`: Verifies idempotent behavior
- `test_register_key_device_id_conflict`: Verifies conflict detection
- `test_register_key_production_disabled`: Verifies production blocking

## WC-W002-R2 Fixes

### 1. Cross-Language Test Vectors (Critical)

**Problem**: Previous test vectors didn't include real cryptographic signatures.

**Fix**:
- Generated real P-256 key pair using Python cryptography library
- Created fixed photo bytes with correct SHA-256
- Generated v1.1 canonical payload
- Created real DER-encoded ECDSA signature
- Added 14 tests in `test_vectors.py` that verify:
  - Public key format (65-byte uncompressed P-256 point)
  - Photo SHA-256 correctness
  - Canonical payload matches manifest
  - **Signature verifies with public key** (critical test)
  - **Modified payload fails verification** (critical test)
  - Wrong public key fails verification
- Tests FAIL if test vectors file doesn't exist

**Test vectors location**: `test-vectors/fixed-vectors.json`

### 2. Record Storage Security

**Problem**: `/store-record` could store records without validating evidence.

**Fix**:
- Store endpoint now validates before storing:
  1. Signature must be mathematically valid
  2. Public key must be registered
  3. Device identity must be consistent
  4. If photo provided, photo hash must match
- Invalid evidence does NOT occupy record_id
- Returns `stored: false` with `rejection_reason` for each failure case
- Duplicate submissions return idempotent result
- Added tests for all rejection scenarios

### 3. Evidence Status Verification

**Confirmed**: Without App Attest, maximum status is `CRYPTOGRAPHICALLY_VALID`, never `FULLY_ATTESTED`.

Evidence hierarchy:
```
FULLY_ATTESTED > CRYPTOGRAPHICALLY_VALID > SIGNATURE_VALID > PARTIAL > UNVERIFIED > INVALID
```

### 4. GitHub Merge Status

PR #2 status: `mergeable=True`, `mergeStateStatus=clean` — no conflicts.

## All Fixes Summary (WC-W002 + R1 + R2 + R3)

| Issue | Fix |
|-------|-----|
| Public key trust model | Separate `cryptographic_signature_valid` from `key_registration_status` |
| Missing photo verification | Three-state check: `passed`/`failed`/`not_checked` |
| Serialization protocol | ISO 8601 validation, newline rejection, NaN/Infinity rejection, v1.1 float format |
| DER validation | Strict structure checks |
| Hash chain | Preserve original order, detect reordering |
| Evidence status hierarchy | 6 levels, no `FULLY_ATTESTED` without App Attest |
| Device identity binding | Check manifest device_id matches registered key |
| Replay detection | `/store-record` validates evidence before storing |
| Cross-language vectors | Real P-256 keys, real signatures, 14 verification tests |
| Time trust level | Explicit `device_reported` with warnings |
| **Mandatory photo** | `/store-record` requires photo_base64, rejects missing/empty/mismatch |
| **Protected registration** | `/register-key` requires explicit flag, production disabled, conflict detection |

## Remaining Limitations

1. **No App Attest**: `device_attestation_status=unknown`, max evidence is `CRYPTOGRAPHICALLY_VALID`
2. **In-memory storage**: Registry and record store are in-memory only
3. **No server time anchor**: `time_trust_level=device_reported`
4. **Test keys only**: All vectors use software-generated keys
5. **Hash chain limitations**: Without server anchors, cannot prove immutability

## Files Changed (R3)

- `backend/app/api/verify.py` — Mandatory photo validation, protected registration
- `backend/tests/test_api.py` — 7 new tests for R3 fixes
- `docs/security-review-W002.md` — This report

## Security Recommendations

1. **Never interpret `CRYPTOGRAPHICALLY_VALID` as "device is genuine"**. It only means the math checks out.

2. **Always check `device_attestation_status`**. If `unknown`, the device could be any computer, not necessarily an iPhone with Secure Enclave.

3. **Use `/store-record` for audit trails**. Photo is mandatory, all evidence is validated.

4. **Implement server-side time anchoring** for production. Without it, timestamps are untrusted.

5. **Implement App Attest** before claiming `FULLY_ATTESTED` status.

6. **Disable `/register-key` in production**. Set `WORKCAM_ENV=production` to block test registration.

## Commit

`<commit-hash>` (to be filled after commit)
