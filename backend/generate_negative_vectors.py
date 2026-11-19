"""
Generate negative test vectors for cross-language verification (WC-W005).

Reads the existing fixed-vectors.json, derives negative cases from the
existing positive vectors (no original private key needed), and writes
them back into the same file under "negative_tests".

Negative cases derived:
- tampered_photo: original photo with last byte flipped (hash mismatch)
- tampered_payload: original payload with one byte changed (signature must fail)
- wrong_signature: valid DER signature by a DIFFERENT key over the original payload
- invalid_der_signature: malformed DER (wrong SEQUENCE tag)
- invalid_timestamp_manifest: non-ISO8601 timestamp (must be rejected)
- invalid_public_key: 64-byte value without 0x04 prefix

TEST KEYS ONLY - never use in production.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256


VECTORS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "test-vectors", "fixed-vectors.json"
)


def main() -> None:
    with open(VECTORS_PATH, encoding="utf-8") as f:
        vectors = json.load(f)

    photo_hex = vectors["photo"]["bytes_hex"]
    payload_hex = vectors["canonical_payload"]["hex"]

    photo = bytearray(bytes.fromhex(photo_hex))
    payload = bytearray(bytes.fromhex(payload_hex))

    # 1. tampered_photo: flip last byte of the original photo
    tampered_photo = bytes(photo[:-1] + bytes([photo[-1] ^ 0x01]))
    tampered_photo_hash = hashlib.sha256(tampered_photo).hexdigest()

    # 2. tampered_payload: change one byte inside device_id value
    #    ("test-device-001" -> "test-device-002": '1' 0x31 -> '2' 0x32)
    needle = b"test-device-001"
    idx = bytes(payload).find(needle)
    assert idx != -1, "device_id not found in payload"
    assert payload[idx + len(needle) - 1] == ord("1")
    tampered_payload = bytearray(payload)
    tampered_payload[idx + len(needle) - 1] = ord("2")
    tampered_payload = bytes(tampered_payload)

    # 3. wrong_signature: sign the ORIGINAL payload with a DIFFERENT key
    other_key = ec.generate_private_key(ec.SECP256R1())
    wrong_sig = other_key.sign(bytes(payload), ec.ECDSA(SHA256()))

    # 4. invalid_der_signature: structurally invalid DER
    invalid_der = b"\x31" + bytes(payload[:8])  # wrong tag, garbage body

    # 5. invalid_timestamp_manifest: slash-separated, non ISO 8601
    invalid_ts = "2026/11/16 12:00:00"

    # 6. invalid_public_key: 64 bytes, no 0x04 prefix
    bad_pub = bytes(payload[:64])

    negative = {
        "wrong_photo": vectors["negative_tests"]["wrong_photo"],
        "tampered_photo": {
            "description": (
                "Original photo with last byte flipped. "
                "sha256 does NOT match manifest.photo_hash."
            ),
            "bytes_hex": tampered_photo.hex(),
            "length_bytes": len(tampered_photo),
            "sha256_hex": tampered_photo_hash,
            "expected": "photo_hash_mismatch",
        },
        "tampered_payload": {
            "description": (
                "Original canonical payload with device_id changed from "
                "test-device-001 to test-device-002. The ORIGINAL signature "
                "must NOT verify over this payload."
            ),
            "hex": tampered_payload.hex(),
            "length_bytes": len(tampered_payload),
            "changed_from_hex": payload_hex,
            "expected_signature_verification": False,
        },
        "wrong_signature": {
            "description": (
                "Valid DER ECDSA signature produced by a DIFFERENT P-256 key "
                "over the ORIGINAL payload. Must NOT verify with the fixed "
                "public_key above."
            ),
            "der_hex": wrong_sig.hex(),
            "base64": base64.b64encode(wrong_sig).decode(),
            "length_bytes": len(wrong_sig),
            "expected_verification_with_fixed_public_key": False,
        },
        "invalid_der_signature": {
            "description": (
                "Malformed DER: wrong SEQUENCE tag (0x31 instead of 0x30). "
                "Must be rejected by DER structure checks before crypto."
            ),
            "der_hex": invalid_der.hex(),
            "base64": base64.b64encode(invalid_der).decode(),
            "expected": "rejected_by_der_structure_check",
        },
        "invalid_timestamp_manifest": {
            "description": (
                "capture_timestamp uses 'YYYY/MM/DD HH:MM:SS' which is not "
                "ISO 8601. Backend must reject via structure validation."
            ),
            "capture_timestamp": invalid_ts,
            "expected": "rejected_by_iso8601_validation",
        },
        "invalid_public_key": {
            "description": (
                "64-byte value without the 0x04 uncompressed-point prefix. "
                "Backend must reject via public key format check."
            ),
            "hex": bad_pub.hex(),
            "base64": base64.b64encode(bad_pub).decode(),
            "length_bytes": len(bad_pub),
            "expected": "rejected_by_public_key_format_check",
        },
    }

    vectors["negative_tests"] = negative

    with open(VECTORS_PATH, "w", encoding="utf-8") as f:
        json.dump(vectors, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Negative vectors written to {VECTORS_PATH}")
    print(f"  tampered_photo sha256      : {tampered_photo_hash}")
    print(f"  wrong_signature der len    : {len(wrong_sig)}")
    print(f"  tampered_payload len        : {len(tampered_payload)}")


if __name__ == "__main__":
    main()
