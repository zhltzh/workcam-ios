"""T18 - Offline anchor-receipt verification support (tech-lab).

Standalone reconstruction of the time-anchor v2 signed payload, so the
CLI verifier can check anchor receipts WITHOUT the backend service
process (which owns private keys - an offline verifier must never need
private keys).

The byte format must be IDENTICAL to TimeAnchorService._create_sign_data
(PROTOCOL_VERSION v2). A consistency test pins this against the real
service output.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


PROTOCOL_VERSION = "v2"
_SIGNED_FIELDS = [
    "anchor_id", "key_id", "batch_id", "server_received_at",
    "challenge", "chain_head_hash", "chain_tail_hash", "record_count",
    "verification_status",
]


def reconstruct_anchor_sign_data(receipt: dict) -> bytes:
    """Rebuild the exact bytes the server signed (v2). Raises ValueError
    on control characters - same guard as the server side.

    Values may arrive as str-Enum objects (e.g. VerificationStatus when
    a receipt dict came from model_dump()); normalize via .value so the
    reconstruction matches the server's canonical formatting."""
    lines = [f"WorkCam-TimeAnchor-{PROTOCOL_VERSION}"]
    for field in _SIGNED_FIELDS:
        value = receipt[field]
        if field == "record_count":
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        else:
            # unwrap str-Enum members FIRST: a VerificationStatus member
            # IS a str instance, so the isinstance check below would
            # otherwise bypass normalization (str-enum __str__ prints
            # 'VerificationStatus.X', not the canonical value).
            value = getattr(value, "value", value)
            if not isinstance(value, str):
                value = str(value)
            for i, ch in enumerate(value):
                if ord(ch) < 32 or ord(ch) == 127:
                    raise ValueError(
                        f"{field} contains control character at position {i}: "
                        f"0x{ord(ch):02x}"
                    )
            if "=" in value or "\n" in value:
                raise ValueError(f"{field} contains invalid characters")
        lines.append(f"{field}={value}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def verify_anchor_receipt_standalone(
    receipt: dict, public_key_pem: str, key_id_expected: str | None = None
) -> dict:
    """Offline anchor receipt verification.

    Returns a dict (machine + human friendly):
      {valid, signature_valid, key_id_matches, reason}
    - valid=False reasons are HONEST: 'unknown_key_id',
      'signature_invalid', or a ValueError message (malformed fields).
    - This verifies the SIGNATURE path only: whether the server still
      holds this anchor is a storage question (not offline-verifiable).
    """
    out = {
        "valid": False,
        "signature_valid": False,
        "key_id_matches": None,
        "reason": "",
    }
    if key_id_expected is not None:
        out["key_id_matches"] = receipt.get("key_id") == key_id_expected
        if not out["key_id_matches"]:
            out["reason"] = (
                f"unknown_key_id: receipt says {receipt.get('key_id')}, "
                f"trusted key is {key_id_expected}"
            )
            return out

    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            out["reason"] = "trusted key is not an EC public key"
            return out
        payload = reconstruct_anchor_sign_data(receipt)
        signature = base64.b64decode(receipt["server_signature"], validate=True)
        public_key.verify(signature, payload, ec.ECDSA(hashes.SHA256()))
        out["signature_valid"] = True
        out["valid"] = True
        out["reason"] = "anchor receipt signature valid (offline signature path)"
    except Exception as e:
        out["reason"] = f"signature_invalid: {e}"
    return out
