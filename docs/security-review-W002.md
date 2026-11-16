# WorkCam Security Review — WC-W002 (Updated with R1)

## Summary

WC-W002 addresses security issues identified in WC-W001. WC-W002-R1 addresses review feedback on WC-W002. This report documents all fixes and remaining limitations.

## WC-W002 Original Fixes

### 1. Public Key Trust Model

**Problem**: Client-submitted public keys were used directly for verification without registration status.

**Fix**: 
- Added `KeyRegistrationStatus` (registered/unregistered/unknown)
- Added `DeviceAttestationStatus` (attested/not_attested/unknown)
- Added `EvidenceStatus` (verified/partial/unverified/invalid)
- Created `InMemoryKeyRegistry` for key registration (test/prototype)
- Unregistered keys are flagged in warnings but signature validity is still reported

### 2. Missing Photo Verification

**Problem**: When photo bytes were not provided, `photo_hash` check was marked as `passed`.

**Fix**:
- Changed to three-state check: `passed` / `failed` / `not_checked`
- When photo not provided, status is `not_checked` with warning
- `evidence_status` cannot be `verified` without photo verification

### 3. Serialization Protocol

**Problem**: 
- Float formatting could vary across languages
- Strings could contain newlines, enabling injection
- Timestamp formats not validated

**Fix**:
- Added strict ISO 8601 timestamp validation
- Added newline rejection in all string fields
- Added NaN/Infinity rejection in float fields
- Added v1.1 protocol with deterministic float formatting
- Domain separator includes version for future compatibility

### 4. DER Signature Validation

**Problem**: Invalid exception handling allowed malformed DER to pass initial checks.

**Fix**:
- Removed invalid exception catch logic
- Added strict DER structure validation

### 5. Hash Chain Integrity

**Problem**: Sorting records by sequence number could hide original submission order anomalies.

**Fix**:
- Added `original_order_preserved` flag
- Added warnings when submission order differs from sequence order
- Added detection of missing sequence numbers (gaps)
- Added detection of duplicate sequence numbers

## WC-W002-R1 Additional Fixes

### 1. Evidence Status Hierarchy (Critical)

**Problem**: `verified` status was too optimistic. When App Attest is unknown, returning `verified` could be misinterpreted as full device trust.

**Fix**: Redesigned `EvidenceStatus` with clear hierarchy:

```
FULLY_ATTESTED > CRYPTOGRAPHICALLY_VALID > SIGNATURE_VALID > PARTIAL > UNVERIFIED > INVALID
```

| Status | Meaning |
|--------|---------|
| `FULLY_ATTESTED` | All checks passed including device attestation. **Rarely achieved.** |
| `CRYPTOGRAPHICALLY_VALID` | Signature valid, photo verified, key registered. **BUT device attestation unknown.** |
| `SIGNATURE_VALID` | Signature math valid, but key not registered or photo not verified. |
| `PARTIAL` | Some checks passed, some incomplete. |
| `UNVERIFIED` | Checks could not be performed. |
| `INVALID` | One or more checks failed. |

**Key change**: Without App Attest, maximum status is `CRYPTOGRAPHICALLY_VALID`, not `FULLY_ATTESTED`.

### 2. Device Identity Binding

**Problem**: Public key registration recorded `device_id`, but verification didn't check consistency.

**Fix**:
- Added `device_identity_consistent` field to response
- Verification checks if manifest's `device_id` matches registered key's `device_id`
- Mismatch results in `INVALID` status
- Added warning when identity doesn't match

### 3. Replay Detection

**Problem**: `test_same_record_submitted_twice` only verified signature validity, didn't test replay detection.

**Fix**:
- Added `RecordStoreEntry` model for record storage
- Added `/store-record` endpoint for registering records
- Added `/check-record/{record_id}` endpoint for checking existence
- Distinguishes:
  - Read-only verification (allows duplicates)
  - Record registration (detects duplicates and conflicts)
- Same `record_id` with identical content → "Duplicate submission"
- Same `record_id` with different content → "CONFLICT"

### 4. Time Trust Level

**Problem**: No explicit indication of time trustworthiness.

**Fix**:
- Added `TimeTrustLevel` enum: `server_anchored` / `device_reported` / `unknown`
- Current implementation always returns `device_reported`
- Warning explains that device could have falsified time

### 5. Hash Chain Immutability Disclaimer

**Problem**: Test name `test_chain_rebuild_detection` was misleading. Chain structure checks don't prove immutability.

**Fix**:
- Renamed test to `test_chain_reorder_detection`
- Added `test_chain_without_anchor_cannot_prove_immutability` demonstrating that:
  - Original chain passes structure checks
  - Completely rebuilt chain (same private key) also passes
  - Without server anchors, cannot distinguish original from rebuilt
- Updated documentation to clarify limitations

### 6. Input Validation

**Problem**: No length limits on string fields.

**Fix**:
- Added `max_length=256` to `record_id` and `device_id`
- Added `le=1000000` to `sequence_number`

## API Changes (WC-W002-R1)

### VerifyResponse (updated)

```python
class VerifyResponse(BaseModel):
    evidence_status: EvidenceStatus  # Refined hierarchy
    cryptographic_signature_valid: bool
    photo_hash_valid: bool | None  # NEW: explicit field
    key_registration_status: KeyRegistrationStatus
    device_identity_consistent: bool | None  # NEW
    device_attestation_status: DeviceAttestationStatus
    time_trust_level: TimeTrustLevel  # NEW
    summary: str
    details: list[VerifyDetail]
    warnings: list[str] = []
```

### New Endpoints

- `POST /register-key` — Register public key with device_id
- `POST /store-record` — Store record for replay detection
- `GET /check-record/{record_id}` — Check if record exists

## Test Results

```
68 passed, 1 warning in 0.89s
```

### New Tests (WC-W002-R1)

- `test_verify_registered_key_identity_match`
- `test_verify_registered_key_identity_mismatch`
- `test_verify_time_trust_level`
- `test_register_key_endpoint`
- `test_store_record_endpoint`
- `test_store_record_duplicate`
- `test_store_record_conflict`
- `test_check_record_endpoint`
- `test_chain_without_anchor_cannot_prove_immutability`
- `test_readonly_verify_allows_duplicate`
- `test_store_record_detects_replay`
- `test_store_record_detects_conflict`
- `test_evidence_status_hierarchy`

## Remaining Limitations

1. **No App Attest**: Device attestation is not implemented. `device_attestation_status` is always `unknown`. Maximum evidence status is `CRYPTOGRAPHICALLY_VALID`.

2. **In-memory registry**: Key registry and record store are in-memory only. Production needs persistent storage.

3. **No server-side time anchoring**: Timestamps are device-reported only. `time_trust_level=device_reported`.

4. **Test keys only**: All test vectors use software-generated keys, not Secure Enclave keys.

5. **Hash chain limitations**: Without server anchors, chains cannot prove immutability. An attacker with the private key could rebuild the entire chain.

6. **No cross-language signature verification**: Test vectors exist but don't include actual signatures to verify across languages.

## Security Recommendations

1. **Never interpret `CRYPTOGRAPHICALLY_VALID` as "device is genuine"**. It only means the math checks out.

2. **Always check `device_attestation_status`**. If `unknown`, the device could be any computer, not necessarily an iPhone with Secure Enclave.

3. **Use `/store-record` for audit trails**. Read-only `/verify` allows unlimited replays; `/store-record` detects conflicts.

4. **Implement server-side time anchoring** for production. Without it, timestamps are untrusted.

5. **Implement App Attest** before claiming `FULLY_ATTESTED` status.

## Commit

`<commit-hash>` (to be filled after commit)
