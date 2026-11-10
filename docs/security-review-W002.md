# WorkCam Security Review — WC-W002

## Summary

WC-W002 addresses security issues identified in WC-W001. This report documents the fixes and remaining limitations.

## Issues Fixed

### 1. Public Key Trust Model

**Problem**: Client-submitted public keys were used directly for verification without registration status.

**Fix**: 
- Added `KeyRegistrationStatus` (registered/unregistered/unknown)
- Added `DeviceAttestationStatus` (attested/not_attested/unknown)
- Added `EvidenceStatus` (verified/partial/unverified/invalid)
- Created `InMemoryKeyRegistry` for key registration (test/prototype)
- Unregistered keys are flagged in warnings but signature validity is still reported

**Result**: `cryptographic_signature_valid` is now separate from `key_registration_status`.

### 2. Missing Photo Verification

**Problem**: When photo bytes were not provided, `photo_hash` check was marked as `passed`.

**Fix**:
- Changed to three-state check: `passed` / `failed` / `not_checked`
- When photo not provided, status is `not_checked` with warning
- `evidence_status` cannot be `verified` without photo verification

**Result**: Clear distinction between "verified" and "not checked".

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

**Result**: Deterministic serialization across Python and Swift.

### 4. DER Signature Validation

**Problem**: Invalid exception handling allowed malformed DER to pass initial checks.

**Fix**:
- Removed invalid exception catch logic
- Added strict DER structure validation:
  - Must start with SEQUENCE tag (0x30)
  - Must contain exactly two INTEGER values
  - No trailing data allowed
  - Length must match declared length

**Result**: Malformed DER signatures are rejected before cryptographic verification.

### 5. Hash Chain Integrity

**Problem**: Sorting records by sequence number could hide original submission order anomalies.

**Fix**:
- Added `original_order_preserved` flag
- Added warnings when submission order differs from sequence order
- Added detection of missing sequence numbers (gaps)
- Added detection of duplicate sequence numbers

**Result**: Chain verification now reports reordering and gaps explicitly.

## New Security Tests

Added `tests/test_security.py` with 16 tests covering:

- Unregistered key signatures
- Missing photo returns `not_checked`
- Newline injection in record_id and device_id
- NaN/Infinity rejection in location
- Invalid timestamp formats
- Malformed DER signatures
- DER with trailing data
- Wrong DER tags
- Duplicate sequence numbers
- Missing sequences in chain
- Chain rebuild detection
- Unsupported protocol versions
- Same record submitted twice
- Cross-language fixed vectors

## Cross-Language Test Vectors

Created `test-vectors/fixed-vectors.json` with:

- Fixed test public key (hex and base64)
- Fixed photo bytes (hex)
- Expected SHA-256 hash
- Canonical payload (hex)
- Swift verification instructions

## API Changes

### VerifyResponse (new fields)

```python
class VerifyResponse(BaseModel):
    evidence_status: EvidenceStatus
    cryptographic_signature_valid: bool
    key_registration_status: KeyRegistrationStatus
    device_attestation_status: DeviceAttestationStatus
    summary: str
    details: list[VerifyDetail]
    warnings: list[str] = []
```

### VerifyDetail (changed)

```python
class VerifyDetail(BaseModel):
    check: str
    status: CheckStatus  # was: passed: bool
    message: str = ""
```

### CheckStatus (new enum)

```python
class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_CHECKED = "not_checked"
```

## Remaining Limitations

1. **No App Attest**: Device attestation is not implemented. `device_attestation_status` is always `unknown`.

2. **In-memory registry**: Key registry is in-memory only. Production needs persistent storage.

3. **No server-side anchoring**: Hash chains without server timestamps cannot guarantee immutability.

4. **Test keys only**: All test vectors use software-generated keys, not Secure Enclave keys.

5. **No replay protection**: Same record can be submitted multiple times without detection.

## Test Results

```
58 passed, 1 warning in 0.79s
```

## Commit

`<commit-hash>` (to be filled after commit)
