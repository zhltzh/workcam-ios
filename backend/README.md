# WorkCam Verification Service

Prototype backend for verifying WorkCam trusted photography records.

## Status

**Development prototype.** This is not a production-grade authentication service.

## Tech Stack

- Python 3.12+
- FastAPI
- Pydantic v2
- cryptography
- pytest

## Quick Start

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Server runs at `http://127.0.0.1:8000`.

## API

### GET /health

Returns service status.

```json
{"status": "ok", "service": "workcam-verify"}
```

### POST /verify

Verifies a capture record.

Request body:

```json
{
  "manifest": { ... },
  "photo_base64": "<optional base64 photo>"
}
```

Response:

```json
{
  "evidence_status": "verified",
  "cryptographic_signature_valid": true,
  "key_registration_status": "registered",
  "device_attestation_status": "unknown",
  "summary": "All checks passed",
  "details": [
    {"check": "structure", "status": "passed", "message": "..."},
    {"check": "photo_hash", "status": "passed", "message": "SHA-256 match"},
    {"check": "ecdsa_signature", "status": "passed", "message": "Signature valid"}
  ],
  "warnings": []
}
```

### Evidence Status Values

- `verified`: All checks passed, key registered
- `partial`: Signature valid but some checks incomplete
- `unverified`: Some checks could not be performed
- `invalid`: One or more checks failed

### Check Status Values

- `passed`: Check succeeded
- `failed`: Check failed
- `not_checked`: Check could not be performed (e.g., photo not provided)

## Run Tests

```bash
cd backend
pytest -v
```

## Documentation

- [Capture Schema](../docs/capture-schema.md)
- [Signature Protocol](../docs/signature-protocol.md)
- [Security Review W002](../docs/security-review-W002.md)

## Test Vectors

Cross-language test vectors are in `test-vectors/fixed-vectors.json`.

## Security Notes

- Private keys, tokens, and sensitive data are redacted from logs.
- Test keys used in tests are software-generated, clearly marked as non-production.
- `device_reported_time` is NOT trusted as server-verified time.
- This prototype does NOT implement App Attest verification.
- Unanchored offline hash chains cannot guarantee no deletion/replacement/rebuilding.
- Public keys must be registered for full evidence verification.

## Branch

This code lives on `dev/windows-security-hardening`.
