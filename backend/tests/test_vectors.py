"""
Tests for cross-language test vectors.

These tests verify that the fixed test vectors in test-vectors/fixed-vectors.json
are mathematically consistent and can be used for cross-language verification.

All test keys are clearly marked as TEST ONLY.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


VECTORS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "test-vectors", "fixed-vectors.json"
)


@pytest.fixture(scope="module")
def vectors():
    """Load test vectors. FAIL if file doesn't exist."""
    if not os.path.exists(VECTORS_PATH):
        pytest.fail(f"Test vectors file not found: {VECTORS_PATH}")
    with open(VECTORS_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_vectors_file_exists(vectors):
    """Test vectors file must exist and be loadable."""
    assert vectors is not None
    assert "public_key" in vectors
    assert "signature" in vectors


def test_public_key_format(vectors):
    """Public key must be valid 65-byte uncompressed P-256 point."""
    pub_hex = vectors["public_key"]["hex"]
    pub_bytes = bytes.fromhex(pub_hex)
    assert len(pub_bytes) == 65, f"Public key must be 65 bytes, got {len(pub_bytes)}"
    assert pub_bytes[0] == 0x04, "Public key must start with 0x04 (uncompressed)"


def test_public_key_base64_matches_hex(vectors):
    """Base64 and hex representations must match."""
    pub_hex = vectors["public_key"]["hex"]
    pub_b64 = vectors["public_key"]["base64"]
    assert base64.b64encode(bytes.fromhex(pub_hex)).decode() == pub_b64


def test_photo_sha256_correct(vectors):
    """Photo SHA-256 must match the actual hash of photo bytes."""
    photo_hex = vectors["photo"]["bytes_hex"]
    photo_bytes = bytes.fromhex(photo_hex)
    expected_sha256 = vectors["photo"]["sha256_hex"]
    actual_sha256 = hashlib.sha256(photo_bytes).hexdigest()
    assert actual_sha256 == expected_sha256, (
        f"Photo SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
    )


def test_manifest_photo_hash_matches(vectors):
    """Manifest photo_hash must match computed photo SHA-256."""
    photo_sha256 = vectors["photo"]["sha256_hex"]
    manifest_photo_hash = vectors["manifest"]["photo_hash"]
    assert manifest_photo_hash == photo_sha256


def test_canonical_payload_hex_decodes_correctly(vectors):
    """Canonical payload hex must decode to valid UTF-8."""
    payload_hex = vectors["canonical_payload"]["hex"]
    payload_bytes = bytes.fromhex(payload_hex)
    text = payload_bytes.decode("utf-8")
    assert text.startswith("WorkCam-Sig-v1.1\n")
    assert "version=1.1" in text
    assert "record_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890" in text


def test_canonical_payload_matches_manifest(vectors):
    """Canonical payload must contain all manifest fields."""
    payload_text = vectors["canonical_payload"]["text"]
    manifest = vectors["manifest"]
    assert f"version={manifest['version']}" in payload_text
    assert f"record_id={manifest['record_id']}" in payload_text
    assert f"sequence_number={manifest['sequence_number']}" in payload_text
    assert f"photo_hash={manifest['photo_hash']}" in payload_text
    assert f"device_id={manifest['device_id']}" in payload_text


def test_signature_der_format(vectors):
    """Signature must be valid DER format."""
    sig_hex = vectors["signature"]["der_hex"]
    sig_bytes = bytes.fromhex(sig_hex)
    assert sig_bytes[0] == 0x30, "DER must start with SEQUENCE tag (0x30)"
    assert len(sig_bytes) >= 8, "DER signature too short"


def test_signature_base64_matches_hex(vectors):
    """Signature base64 and hex must match."""
    sig_hex = vectors["signature"]["der_hex"]
    sig_b64 = vectors["signature"]["base64"]
    assert base64.b64encode(bytes.fromhex(sig_hex)).decode() == sig_b64


def test_signature_verifies_with_public_key(vectors):
    """
    CRITICAL TEST: The fixed signature must verify with the fixed public key
    over the fixed canonical payload.

    This proves the test vectors are mathematically consistent.
    """
    pub_hex = vectors["public_key"]["hex"]
    pub_bytes = bytes.fromhex(pub_hex)
    pub_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub_bytes)

    payload_hex = vectors["canonical_payload"]["hex"]
    payload_bytes = bytes.fromhex(payload_hex)

    sig_hex = vectors["signature"]["der_hex"]
    sig_bytes = bytes.fromhex(sig_hex)

    try:
        pub_key.verify(sig_bytes, payload_bytes, ec.ECDSA(SHA256()))
    except InvalidSignature:
        pytest.fail("Signature verification failed - test vectors are inconsistent!")


def test_modified_payload_fails_verification(vectors):
    """
    CRITICAL TEST: Modifying any byte in the payload must invalidate the signature.
    """
    pub_hex = vectors["public_key"]["hex"]
    pub_bytes = bytes.fromhex(pub_hex)
    pub_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub_bytes)

    payload_hex = vectors["canonical_payload"]["hex"]
    payload_bytes = bytearray(bytes.fromhex(payload_hex))

    sig_hex = vectors["signature"]["der_hex"]
    sig_bytes = bytes.fromhex(sig_hex)

    payload_bytes[-1] ^= 0x01
    modified_payload = bytes(payload_bytes)

    with pytest.raises(InvalidSignature):
        pub_key.verify(sig_bytes, modified_payload, ec.ECDSA(SHA256()))


def test_wrong_public_key_fails_verification(vectors):
    """
    CRITICAL TEST: A different public key must not verify the signature.
    """
    other_key = ec.generate_private_key(ec.SECP256R1())
    other_pub = other_key.public_key()

    payload_hex = vectors["canonical_payload"]["hex"]
    payload_bytes = bytes.fromhex(payload_hex)

    sig_hex = vectors["signature"]["der_hex"]
    sig_bytes = bytes.fromhex(sig_hex)

    with pytest.raises(InvalidSignature):
        other_pub.verify(sig_bytes, payload_bytes, ec.ECDSA(SHA256()))


def test_wrong_photo_hash_not_in_payload(vectors):
    """The wrong photo hash must not appear in the canonical payload."""
    payload_text = vectors["canonical_payload"]["text"]
    wrong_photo_hash = vectors["negative_tests"]["wrong_photo"]["sha256_hex"]
    assert wrong_photo_hash not in payload_text


def test_negative_test_photo_differs(vectors):
    """Negative test photo must have different SHA-256 than the real photo."""
    real_hash = vectors["photo"]["sha256_hex"]
    wrong_hash = vectors["negative_tests"]["wrong_photo"]["sha256_hex"]
    assert real_hash != wrong_hash
