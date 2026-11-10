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
  "valid": true,
  "summary": "All checks passed",
  "details": [
    {"check": "structure", "passed": true, "message": "..."},
    {"check": "photo_hash", "passed": true, "message": "SHA-256 match"},
    {"check": "ecdsa_signature", "passed": true, "message": "Signature valid"}
  ]
}
```

## Run Tests

```bash
cd backend
pytest -v
```

## Documentation

- [Capture Schema](../docs/capture-schema.md)
- [Signature Protocol](../docs/signature-protocol.md)

## Security Notes

- Private keys, tokens, and sensitive data are redacted from logs.
- Test keys used in tests are software-generated, clearly marked as non-production.
- `device_reported_time` is NOT trusted as server-verified time.
- This prototype does NOT implement App Attest verification.
- Unanchored offline hash chains cannot guarantee no deletion/replacement/rebuilding.

## Branch

This code lives on `dev/windows-backend`.
