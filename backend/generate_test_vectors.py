"""
Generate real cross-language test vectors for WorkCam signature verification.

WARNING: These keys are for TESTING ONLY. Never use in production.
"""

import base64
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def generate_test_vectors():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    pub_bytes = public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    pub_b64 = base64.b64encode(pub_bytes).decode()

    photo_bytes = b"\x42" * 1024
    photo_sha256 = hashlib.sha256(photo_bytes).hexdigest()

    version = "1.1"
    record_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    sequence_number = 0
    capture_timestamp = "2026-11-16T12:00:00+00:00"
    device_reported_time = "2026-11-16T12:00:00+00:00"
    device_id = "test-device-001"

    lines = [
        "WorkCam-Sig-v1.1",
        f"version={version}",
        f"record_id={record_id}",
        f"sequence_number={sequence_number}",
        "previous_record_hash=none",
        f"photo_hash={photo_sha256}",
        f"capture_timestamp={capture_timestamp}",
        f"device_reported_time={device_reported_time}",
        f"device_id={device_id}",
        "location=none",
    ]
    canonical_text = "\n".join(lines) + "\n"
    canonical_bytes = canonical_text.encode("utf-8")

    signature_der = private_key.sign(canonical_bytes, ec.ECDSA(SHA256()))
    signature_b64 = base64.b64encode(signature_der).decode()

    vectors = {
        "_description": "Real cross-language test vectors for WorkCam.",
        "_warning": "TEST KEYS ONLY - Never use in production.",

        "public_key": {
            "hex": pub_bytes.hex(),
            "base64": pub_b64,
            "length_bytes": len(pub_bytes),
            "format": "uncompressed P-256 point (0x04 + x + y)"
        },

        "photo": {
            "bytes_hex": photo_bytes.hex(),
            "length_bytes": len(photo_bytes),
            "sha256_hex": photo_sha256
        },

        "manifest": {
            "version": version,
            "record_id": record_id,
            "sequence_number": sequence_number,
            "previous_record_hash": None,
            "photo_hash": photo_sha256,
            "capture_timestamp": capture_timestamp,
            "device_reported_time": device_reported_time,
            "device_id": device_id,
            "location": None
        },

        "canonical_payload": {
            "text": canonical_text,
            "hex": canonical_bytes.hex(),
            "length_bytes": len(canonical_bytes),
            "encoding": "UTF-8"
        },

        "signature": {
            "der_hex": signature_der.hex(),
            "base64": signature_b64,
            "length_bytes": len(signature_der),
            "algorithm": "ECDSA with SHA-256",
            "curve": "P-256 (secp256r1)",
            "encoding": "DER (RFC 3279)"
        },

        "swift_verification": {
            "step1": "Decode public_key.base64 to get 65-byte uncompressed P-256 point",
            "step2": "Create P256.Signing.PublicKey using x963Representation",
            "step3": "Decode canonical_payload.hex to get exact bytes that were signed",
            "step4": "Decode signature.base64 to get DER-encoded signature bytes",
            "step5": "Create P256.Signing.ECDSASignature from DER representation",
            "step6": "Call publicKey.verify(signature, for: canonical_payload_bytes)",
            "step7": "Should return true for valid signature",
            "step8": "Modify one byte of canonical_payload and verify should fail"
        },

        "negative_tests": {
            "wrong_photo": {
                "bytes_hex": (b"\x43" * 1024).hex(),
                "sha256_hex": hashlib.sha256(b"\x43" * 1024).hexdigest()
            }
        }
    }

    return vectors


if __name__ == "__main__":
    vectors = generate_test_vectors()
    print(json.dumps(vectors, indent=2))
