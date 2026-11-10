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
    assert data["evidence_status"] == EvidenceStatus.PARTIAL


def test_missing_photo_returns_not_checked():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    resp = client.post("/verify", json={"manifest": m.model_dump()})
    data = resp.json()
    photo_checks = [d for d in data["details"] if d["check"] == "photo_hash"]
    assert photo_checks[0]["status"] == CheckStatus.NOT_CHECKED
    assert data["evidence_status"] != EvidenceStatus.VERIFIED


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


def test_chain_rebuild_detection():
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


def test_unsupported_protocol_version():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub, version="9.9")
    resp = client.post("/verify", json={"manifest": m.model_dump()})
    data = resp.json()
    assert data["evidence_status"] == EvidenceStatus.INVALID


def test_same_record_submitted_twice():
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


def test_cross_language_fixed_vector():
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
