# WorkCam Capture Schema

## Overview

This document defines the data structure for a single WorkCam capture record.
Each record binds a photo's SHA-256 digest to device metadata via a digital signature.

## SignatureManifest

| Field                  | Type            | Required | Description                                         |
|------------------------|-----------------|----------|-----------------------------------------------------|
| `version`              | string          | yes      | Protocol version: `"1.0"` or `"1.1"`                |
| `record_id`            | string (UUID)   | yes      | Unique identifier for this record                   |
| `sequence_number`      | integer (>=0)   | yes      | Monotonic counter; 0 = genesis                      |
| `previous_record_hash` | string or null  | yes      | SHA-256 hex of previous record; null for genesis    |
| `photo_hash`           | string (hex)    | yes      | SHA-256 hex digest of raw photo bytes               |
| `capture_timestamp`    | string (ISO)    | yes      | Device capture time, UTC, ISO 8601                  |
| `device_reported_time` | string (ISO)    | yes      | Device-reported time; NOT server-trusted            |
| `device_id`            | string          | yes      | Opaque device identifier                            |
| `location`             | object or null  | no       | GNSS data (see below)                               |
| `signature`            | string (base64) | yes      | DER-encoded ECDSA signature                         |
| `public_key`           | string (base64) | yes      | Uncompressed P-256 point (65 bytes)                 |

### Location Object

| Field                  | Type   | Description                          |
|------------------------|--------|--------------------------------------|
| `latitude`             | float  | WGS-84 latitude [-90, 90], finite    |
| `longitude`            | float  | WGS-84 longitude [-180, 180], finite |
| `horizontal_accuracy`  | float  | Meters, > 0, finite                  |
| `altitude`             | float  | Meters above WGS-84 ellipsoid, finite, nullable |

### Field Validation

- **Strings**: Must not contain newlines (`\n` or `\r`)
- **Timestamps**: Must be ISO 8601 format (e.g., `2026-11-09T12:00:00+00:00`)
- **Floats**: Must be finite (no NaN or Infinity)
- **Hex strings**: Must be valid lowercase hex

## Important Notes

- `photo_hash` is a digest, not a signature. It only proves the photo bytes match.
- `device_reported_time` is self-reported by the device and is NOT trusted as server-verified time.
- `capture_timestamp` and `device_reported_time` may differ; both are recorded for audit.
- `public_key` must be registered for full evidence verification.
