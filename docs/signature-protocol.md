# WorkCam Signature Protocol

## Version

`1.0` (stable), `1.1` (enhanced float formatting)

## Cryptographic Primitives

| Component         | Choice                                      |
|-------------------|---------------------------------------------|
| Hash              | SHA-256                                     |
| Curve             | NIST P-256 (secp256r1)                      |
| Signature         | ECDSA with SHA-256                          |
| Signature encoding| DER (RFC 3279)                              |
| Public key format | Uncompressed point: `0x04 ‖ x ‖ y` (65 bytes) |
| Public key encoding| Base64 (standard, with padding)            |
| Signature encoding | Base64 (standard, with padding)            |
| Text encoding     | UTF-8                                       |

## Domain Separation

All signed payloads begin with a version-specific ASCII string:

- v1.0: `WorkCam-Sig-v1`
- v1.1: `WorkCam-Sig-v1.1`

followed by a newline (`0x0a`).

## Canonical Serialization

The signed payload is constructed as follows:

1. Start with the domain separator line (version-specific)
2. Append each field in **fixed order** (one per line, `key=value`):
   - `version`
   - `record_id`
   - `sequence_number`
   - `previous_record_hash` (or `none` if null)
   - `photo_hash`
   - `capture_timestamp`
   - `device_reported_time`
   - `device_id`
   - `location` (see format below)
3. End with a final newline.
4. Encode the entire text as UTF-8.

### Field Validation Rules

- **Strings**: Must not contain newlines (`\n` or `\r`)
- **Timestamps**: Must be ISO 8601 format (e.g., `2026-11-09T12:00:00+00:00` or `2026-11-09T12:00:00Z`)
- **Floats (v1.1)**: Must be finite (no NaN or Infinity), formatted with consistent precision
- **Hex strings**: Must be valid lowercase hex

### Location Format

- If location is null: `location=none`
- If location is present: `location={lat},{lon},{accuracy},{altitude}`
  - altitude is `none` if null
  - v1.1: floats formatted with `.10g` precision, ensuring decimal point

### Example Payload (v1.0)

```
WorkCam-Sig-v1
version=1.0
record_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890
sequence_number=0
previous_record_hash=none
photo_hash=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
capture_timestamp=2026-11-09T12:00:00+00:00
device_reported_time=2026-11-09T12:00:00+00:00
device_id=test-device-001
location=none
```

## DER Signature Format

ECDSA signatures must be DER-encoded (RFC 3279):

- Must start with SEQUENCE tag (`0x30`)
- Must contain exactly two INTEGER values (r, s)
- No trailing data allowed
- Total length must match declared length

Invalid DER structures are rejected before cryptographic verification.

## Signing

1. Construct the canonical payload bytes (UTF-8).
2. Sign with P-256 ECDSA using SHA-256.
3. DER-encode the signature (RFC 3279).
4. Base64-encode the DER bytes.

## Verification

1. Decode the `public_key` from base64 → 65-byte uncompressed point.
2. Decode the `signature` from base64 → DER bytes.
3. Validate DER structure strictly.
4. Reconstruct the canonical payload from the manifest fields.
5. Verify the ECDSA signature over the payload using SHA-256.

## Evidence Status

The verification result includes multiple status fields with a clear hierarchy:

### Evidence Status Hierarchy

```
FULLY_ATTESTED > CRYPTOGRAPHICALLY_VALID > SIGNATURE_VALID > PARTIAL > UNVERIFIED > INVALID
```

| Status | Meaning | When Achieved |
|--------|---------|---------------|
| `FULLY_ATTESTED` | All checks passed including device attestation | **Rarely achieved.** Requires App Attest implementation. |
| `CRYPTOGRAPHICALLY_VALID` | Signature valid, photo verified, key registered | **BUT device attestation unknown.** Cannot prove device is genuine. |
| `SIGNATURE_VALID` | Signature math valid | Key not registered or photo not verified. |
| `PARTIAL` | Some checks passed, some incomplete | Photo not provided, or other checks incomplete. |
| `UNVERIFIED` | Checks could not be performed | Structural issues, unsupported version. |
| `INVALID` | One or more checks failed | Signature invalid, photo mismatch, identity mismatch. |

### Response Fields

| Field | Description |
|-------|-------------|
| `evidence_status` | Overall evidence trust level (see hierarchy above) |
| `cryptographic_signature_valid` | Mathematical signature validity |
| `photo_hash_valid` | Photo bytes match signed hash (null if not checked) |
| `key_registration_status` | Whether public key is registered |
| `device_identity_consistent` | Whether manifest device_id matches registered key |
| `device_attestation_status` | Whether device passed attestation (always `unknown` currently) |
| `time_trust_level` | How much we trust the timestamp (`device_reported` currently) |

### Important Warnings

**Never interpret `CRYPTOGRAPHICALLY_VALID` as "device is genuine".** It only means the math checks out. Without App Attest, the device could be any computer.

**Always check `device_attestation_status`.** If `unknown`, cannot prove the device is an iPhone with Secure Enclave.

**Always check `time_trust_level`.** If `device_reported`, the device could have falsified the time.

## Hash Chain

Each record's hash is computed as:

```
record_hash = SHA-256(canonical_payload ‖ signature_ascii)
```

where `signature_ascii` is the base64 signature string encoded as ASCII.

- Genesis record (seq=0): `previous_record_hash = null`
- Subsequent records: `previous_record_hash = record_hash of (seq-1)`

### Chain Verification

1. Preserve original submission order and detect reordering
2. Check no duplicate sequence numbers
3. Report missing sequence numbers (gaps may indicate rebuilding)
4. For seq=0: verify `previous_record_hash` is null
5. For seq>0: verify `previous_record_hash` matches the computed hash of the previous record
6. Report any gaps, mismatches, or duplicates

## Limitations

### Hash Chain Immutability

An offline hash chain **cannot** guarantee no deletion, replacement, or rebuilding of records.

**What chain verification proves:**
- Internal consistency: each record's `previous_record_hash` matches the computed hash of the previous record
- No duplicate sequence numbers
- No gaps in sequence numbers (if present)
- Original submission order (if preserved)

**What chain verification does NOT prove:**
- That the records are the original ones (an attacker with the private key could rebuild the entire chain)
- That no records were deleted (gaps could indicate deletion OR could indicate the records were never created)
- That the chain is immutable (without server-side anchors, the chain can be reconstructed)

**To achieve stronger guarantees:**
- Implement server-side time anchoring (server records timestamp when chain is submitted)
- Implement periodic anchoring to external systems (blockchain, notary service)
- Use append-only storage with cryptographic proofs

### Device Attestation

This protocol does **not** implement App Attest verification. Without App Attest:
- Cannot prove the device is a genuine Apple device
- Cannot prove the private key is stored in Secure Enclave
- Maximum evidence status is `CRYPTOGRAPHICALLY_VALID`, not `FULLY_ATTESTED`

### Time Trust

Timestamps are device-reported only (`time_trust_level=device_reported`). Without server-side anchoring:
- Device could have falsified the time
- Cannot prove when the photo was actually taken
- Cannot correlate with external events

## Test Vectors

See `test-vectors/fixed-vectors.json` for cross-language test vectors.
All tests use software-generated P-256 keys (clearly marked as test keys).
