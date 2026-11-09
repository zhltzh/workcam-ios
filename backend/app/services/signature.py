from __future__ import annotations

import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.hashes import SHA256

from app.models.capture import SignatureManifest, SUPPORTED_VERSIONS
from app.services.serialization import canonical_payload


def _decode_public_key(public_key_b64: str) -> bytes:
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
    except Exception:
        raise ValueError("public_key is not valid base64")
    if len(raw) != 65 or raw[0] != 0x04:
        raise ValueError(
            "public_key must be 65-byte uncompressed P-256 point (0x04 + x + y)"
        )
    return raw


def _decode_signature(sig_b64: str) -> bytes:
    try:
        raw = base64.b64decode(sig_b64, validate=True)
    except Exception:
        raise ValueError("signature is not valid base64")
    if len(raw) < 8:
        raise ValueError("signature too short to be valid DER")
    return raw


def _load_public_key(raw_point: bytes) -> ec.EllipticCurvePublicKey:
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), raw_point
        )
    except Exception:
        raise ValueError("public_key is not a valid P-256 point")


def validate_manifest_structure(manifest: SignatureManifest) -> list[str]:
    errors: list[str] = []

    if manifest.version not in SUPPORTED_VERSIONS:
        errors.append(
            f"Unsupported version '{manifest.version}'; "
            f"supported: {sorted(SUPPORTED_VERSIONS)}"
        )

    if len(manifest.record_id) == 0:
        errors.append("record_id must not be empty")

    if len(manifest.photo_hash) != 64:
        errors.append("photo_hash must be 64 hex characters (SHA-256)")
    try:
        bytes.fromhex(manifest.photo_hash)
    except ValueError:
        errors.append("photo_hash is not valid hex")

    if manifest.previous_record_hash is not None:
        if len(manifest.previous_record_hash) != 64:
            errors.append(
                "previous_record_hash must be 64 hex characters when present"
            )
        try:
            bytes.fromhex(manifest.previous_record_hash)
        except ValueError:
            errors.append("previous_record_hash is not valid hex")

    try:
        _decode_public_key(manifest.public_key)
    except ValueError as e:
        errors.append(str(e))

    try:
        _decode_signature(manifest.signature)
    except ValueError as e:
        errors.append(str(e))

    return errors


def verify_photo_hash(manifest: SignatureManifest, photo_bytes: bytes) -> bool:
    actual = hashlib.sha256(photo_bytes).hexdigest()
    return actual == manifest.photo_hash


def verify_ecdsa_signature(manifest: SignatureManifest) -> bool:
    raw_key = _decode_public_key(manifest.public_key)
    pub_key = _load_public_key(raw_key)
    sig_bytes = _decode_signature(manifest.signature)
    payload = canonical_payload(manifest)

    try:
        der_sig = utils.decode_dss_signature(sig_bytes)
        raise ValueError("unexpected: raw decode succeeded for DER")
    except (ValueError, Exception):
        pass

    try:
        pub_key.verify(sig_bytes, payload, ec.ECDSA(SHA256()))
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


def verify_signature_with_details(
    manifest: SignatureManifest,
) -> tuple[bool, str]:
    try:
        raw_key = _decode_public_key(manifest.public_key)
    except ValueError as e:
        return False, f"Invalid public key: {e}"

    try:
        pub_key = _load_public_key(raw_key)
    except ValueError as e:
        return False, f"Invalid public key: {e}"

    try:
        sig_bytes = _decode_signature(manifest.signature)
    except ValueError as e:
        return False, f"Invalid signature encoding: {e}"

    payload = canonical_payload(manifest)

    try:
        pub_key.verify(sig_bytes, payload, ec.ECDSA(SHA256()))
        return True, "Signature valid"
    except InvalidSignature:
        return False, "ECDSA signature verification failed"
    except Exception as e:
        return False, f"Signature verification error: {e}"
