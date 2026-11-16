# WorkCam Security Review — WC-W002 (Updated with R1 and R2)

## Summary

WC-W002 addresses security issues identified in WC-W001. WC-W002-R1 addresses review feedback on WC-W002. WC-W002-R2 addresses final review feedback including cross-language test vectors and record storage security.

## Test Results

```
86 passed, 1 warning in 1.63s
```

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

## All Fixes Summary (WC-W002 + R1 + R2)

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

## Remaining Limitations

1. **No App Attest**: `device_attestation_status=unknown`, max evidence is `CRYPTOGRAPHICALLY_VALID`
2. **In-memory storage**: Registry and record store are in-memory only
3. **No server time anchor**: `time_trust_level=device_reported`
4. **Test keys only**: All vectors use software-generated keys
5. **Hash chain limitations**: Without server anchors, cannot prove immutability

## Files Changed

- `backend/app/api/verify.py` — Store endpoint security
- `backend/app/models/capture.py` — New fields and enums
- `backend/app/services/key_registry.py` — Device identity check
- `backend/tests/test_api.py` — Store endpoint tests
- `backend/tests/test_security.py` — Security tests
- `backend/tests/test_vectors.py` — Cross-language vector tests (NEW)
- `backend/generate_test_vectors.py` — Vector generator (NEW)
- `test-vectors/fixed-vectors.json` — Real test vectors
- `docs/security-review-W002.md` — This report
- `docs/signature-protocol.md` — Updated documentation

## Commit

`<commit-hash>` (to be filled after commit)
