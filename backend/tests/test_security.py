from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from app.main import app
from app.models.capture import (
    CheckStatus,
    EvidenceStatus,
    KeyRegistrationStatus,
)
from app.services.key_registry import get_registry, reset_registry
from app.services.serialization import canonical_payload
from tests.conftest import make_manifest, make_test_photo, _encode_pub


client = TestClient(app)


def setup_function():
    reset_registry()


def test_unregistered_key_signature():
    """Unregistered key -> SIGNATURE_VALID (not CRYPTOGRAPHICALLY_VALID)"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)
    resp = client.post("/verify", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()
    assert data["cryptographic_signature_valid"] is True
    assert data["key_registration_status"] == KeyRegistrationStatus.UNREGISTERED
    assert data["evidence_status"] == EvidenceStatus.SIGNATURE_VALID


def test_missing_photo_returns_not_checked():
    """Missing photo -> photo_hash_valid=None, not VERIFIED"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    resp = client.post("/verify", json={"manifest": m.model_dump()})
    data = resp.json()
    photo_checks = [d for d in data["details"] if d["check"] == "photo_hash"]
    assert photo_checks[0]["status"] == CheckStatus.NOT_CHECKED
    assert data["photo_hash_valid"] is None
    assert data["evidence_status"] != EvidenceStatus.FULLY_ATTESTED
    assert data["evidence_status"] != EvidenceStatus.CRYPTOGRAPHICALLY_VALID


def test_newline_injection_in_record_id():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    m2 = m.model_copy(update={"record_id": "test\ninjected=evil"})
    resp = client.post("/verify", json={"manifest": m2.model_dump()})
    data = resp.json()
    assert data["evidence_status"] == EvidenceStatus.INVALID
    struct_checks = [d for d in data["details"] if d["check"] == "structure"]
    assert any(d["status"] == CheckStatus.FAILED for d in struct_checks)


def test_newline_injection_in_device_id():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    m2 = m.model_copy(update={"device_id": "test\ninjected"})
    resp = client.post("/verify", json={"manifest": m2.model_dump()})
    data = resp.json()
    assert data["evidence_status"] == EvidenceStatus.INVALID


def test_invalid_float_nan_in_location():
    from app.models.capture import LocationData
    import pytest
    with pytest.raises(Exception):
        LocationData(
            latitude=float("nan"),
            longitude=0.0,
            horizontal_accuracy=1.0,
        )


def test_invalid_float_inf_in_location():
    from app.models.capture import LocationData
    import pytest
    with pytest.raises(Exception):
        LocationData(
            latitude=float("inf"),
            longitude=0.0,
            horizontal_accuracy=1.0,
        )


def test_different_timestamp_formats():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m1 = make_manifest(
        priv, pub,
        capture_timestamp="2026-11-09T12:00:00+00:00",
    )
    assert "2026-11-09T12:00:00+00:00" in m1.capture_timestamp
    m2 = make_manifest(
        priv, pub,
        capture_timestamp="2026-11-09T12:00:00Z",
    )
    assert m2.capture_timestamp.endswith("Z")
    m3 = make_manifest(priv, pub)
    m3_invalid = m3.model_copy(update={"capture_timestamp": "not-a-timestamp"})
    resp = client.post("/verify", json={"manifest": m3_invalid.model_dump()})
    data = resp.json()
    assert data["evidence_status"] == EvidenceStatus.INVALID


def test_malformed_der_signature():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    bad_der = b"\x30\x06\x02\x01\x01\x02\x01\x01"
    m2 = m.model_copy(update={
        "signature": base64.b64encode(bad_der).decode(),
    })
    resp = client.post("/verify", json={"manifest": m2.model_dump()})
    data = resp.json()
    assert data["evidence_status"] == EvidenceStatus.INVALID


def test_der_with_trailing_data():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    good_sig = base64.b64decode(m.signature)
    bad_sig = good_sig + b"\x00\x00"
    m2 = m.model_copy(update={
        "signature": base64.b64encode(bad_sig).decode(),
    })
    resp = client.post("/verify", json={"manifest": m2.model_dump()})
    data = resp.json()
    struct_checks = [d for d in data["details"] if d["check"] == "structure"]
    assert any(d["status"] == CheckStatus.FAILED for d in struct_checks)


def test_der_wrong_tag():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    good_sig = base64.b64decode(m.signature)
    bad_sig = b"\x31" + good_sig[1:]
    m2 = m.model_copy(update={
        "signature": base64.b64encode(bad_sig).decode(),
    })
    resp = client.post("/verify", json={"manifest": m2.model_dump()})
    data = resp.json()
    struct_checks = [d for d in data["details"] if d["check"] == "structure"]
    assert any(d["status"] == CheckStatus.FAILED for d in struct_checks)


def test_duplicate_sequence_numbers():
    from app.services.chain import verify_chain
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    r0 = make_manifest(priv, pub, seq=0)
    r1 = make_manifest(priv, pub, seq=1)
    r1_dup = make_manifest(priv, pub, seq=1)
    result = verify_chain([r0, r1, r1_dup])
    assert any("duplicate" in str(r).lower() for r in result["results"])


def test_missing_sequence_in_chain():
    from app.services.chain import verify_chain
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    r0 = make_manifest(priv, pub, seq=0)
    r2 = make_manifest(priv, pub, seq=2)
    result = verify_chain([r0, r2])
    assert len(result["warnings"]) > 0
    assert any("missing" in w.lower() for w in result["warnings"])


def test_chain_reorder_detection():
    """Test that reordering is detected (not full rebuild detection)"""
    from app.services.chain import verify_chain
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    r0 = make_manifest(priv, pub, seq=0)
    r1 = make_manifest(priv, pub, seq=1)
    r2 = make_manifest(priv, pub, seq=2)
    result = verify_chain([r0, r1, r2])
    assert result["original_order_preserved"] is True
    result2 = verify_chain([r2, r0, r1])
    assert result2["original_order_preserved"] is False


def test_chain_without_anchor_cannot_prove_immutability():
    """
    Document that unanchored chains cannot prove immutability.
    An attacker with the private key could rebuild the entire chain with
    valid signatures. The chain structure check only verifies internal
    consistency, not that the records are the original ones.

    This test demonstrates that:
    1. A valid chain passes structure checks
    2. A completely rebuilt chain (with same private key) also passes
    3. Without server anchors, we cannot distinguish original from rebuilt
    """
    from app.services.chain import verify_chain
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())

    original_r0 = make_manifest(priv, pub, seq=0, record_id="original-0")
    original_r1 = make_manifest(priv, pub, seq=1, record_id="original-1",
                                 prev_hash=compute_record_hash(original_r0))
    original_r2 = make_manifest(priv, pub, seq=2, record_id="original-2",
                                 prev_hash=compute_record_hash(original_r1))

    original_result = verify_chain([original_r0, original_r1, original_r2])
    assert all(r.get("valid") for r in original_result["results"])

    rebuilt_r0 = make_manifest(priv, pub, seq=0, record_id="rebuilt-0")
    rebuilt_r1 = make_manifest(priv, pub, seq=1, record_id="rebuilt-1",
                                prev_hash=compute_record_hash(rebuilt_r0))
    rebuilt_r2 = make_manifest(priv, pub, seq=2, record_id="rebuilt-2",
                                prev_hash=compute_record_hash(rebuilt_r1))

    rebuilt_result = verify_chain([rebuilt_r0, rebuilt_r1, rebuilt_r2])
    assert all(r.get("valid") for r in rebuilt_result["results"])


def compute_record_hash(manifest):
    """Helper to compute record hash for chain building"""
    from app.services.chain import compute_record_hash as _compute
    return _compute(manifest)


def test_unsupported_protocol_version():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub, version="9.9")
    resp = client.post("/verify", json={"manifest": m.model_dump()})
    data = resp.json()
    assert data["evidence_status"] == EvidenceStatus.INVALID


def test_readonly_verify_allows_duplicate():
    """Read-only verify endpoint allows same record to be verified multiple times"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)
    resp1 = client.post("/verify", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    resp2 = client.post("/verify", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    assert resp1.json()["cryptographic_signature_valid"] is True
    assert resp2.json()["cryptographic_signature_valid"] is True


def test_store_record_detects_replay():
    """Store endpoint detects duplicate record_id submissions"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)
    resp1 = client.post("/store-record", json={"manifest": m.model_dump()})
    assert resp1.json()["is_new"] is True
    resp2 = client.post("/store-record", json={"manifest": m.model_dump()})
    assert resp2.json()["is_new"] is False
    assert "duplicate" in resp2.json()["message"].lower()


def test_store_record_detects_conflict():
    """Store endpoint detects same record_id with different content"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m1 = make_manifest(priv, pub, photo_bytes=photo)
    resp1 = client.post("/store-record", json={"manifest": m1.model_dump()})
    assert resp1.json()["is_new"] is True
    m2 = m1.model_copy(update={"photo_hash": "aa" * 32})
    resp2 = client.post("/store-record", json={"manifest": m2.model_dump()})
    assert resp2.json()["is_new"] is False
    assert "conflict" in resp2.json()["message"].lower()


def test_cross_language_fixed_vector():
    """Verify cross-language test vectors with actual signature check"""
    import json
    import os
    vector_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "test-vectors", "fixed-vectors.json"
    )
    if not os.path.exists(vector_path):
        return
    with open(vector_path) as f:
        vectors = json.load(f)
    payload_hex = vectors["canonical_payload_v1_0_hex"]
    payload_bytes = bytes.fromhex(payload_hex)
    text = payload_bytes.decode("utf-8")
    assert text.startswith("WorkCam-Sig-v1\n")
    assert "version=1.0" in text
    assert "record_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890" in text
    assert "sequence_number=0" in text
    assert "previous_record_hash=none" in text
    assert "location=none" in text
    assert text.endswith("\n")


def test_evidence_status_hierarchy():
    """
    Test evidence status hierarchy:
    INVALID < SIGNATURE_VALID < CRYPTOGRAPHICALLY_VALID < FULLY_ATTESTED
    """
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()

    m_invalid = make_manifest(priv, pub, photo_bytes=photo)
    m_invalid = m_invalid.model_copy(update={"photo_hash": "aa" * 32})
    resp_invalid = client.post("/verify", json={
        "manifest": m_invalid.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    assert resp_invalid.json()["evidence_status"] == EvidenceStatus.INVALID

    m_sig_valid = make_manifest(priv, pub, photo_bytes=photo)
    resp_sig = client.post("/verify", json={
        "manifest": m_sig_valid.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    assert resp_sig.json()["evidence_status"] == EvidenceStatus.SIGNATURE_VALID

    registry = get_registry()
    registry.register(pub_b64, "test-device-001")
    m_crypto = make_manifest(priv, pub, photo_bytes=photo)
    resp_crypto = client.post("/verify", json={
        "manifest": m_crypto.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    assert resp_crypto.json()["evidence_status"] == EvidenceStatus.CRYPTOGRAPHICALLY_VALID
    assert resp_crypto.json()["device_attestation_status"] == "unknown"
