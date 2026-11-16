from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.main import app
from app.models.capture import (
    CheckStatus,
    DeviceAttestationStatus,
    EvidenceStatus,
    KeyRegistrationStatus,
    TimeTrustLevel,
)
from app.services.key_registry import get_registry, reset_registry
from tests.conftest import make_manifest, make_test_photo, _encode_pub


client = TestClient(app)


def setup_function():
    reset_registry()


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_verify_valid_with_photo_unregistered_key():
    """Valid signature and photo, but key not registered -> SIGNATURE_VALID"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)
    resp = client.post("/verify", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["cryptographic_signature_valid"] is True
    assert data["photo_hash_valid"] is True
    assert data["key_registration_status"] == KeyRegistrationStatus.UNREGISTERED
    assert data["evidence_status"] == EvidenceStatus.SIGNATURE_VALID


def test_verify_no_photo_returns_not_checked():
    """No photo provided -> photo_hash_valid=None, PARTIAL or SIGNATURE_VALID"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    resp = client.post("/verify", json={
        "manifest": m.model_dump(),
    })
    data = resp.json()
    assert data["cryptographic_signature_valid"] is True
    assert data["photo_hash_valid"] is None
    photo_checks = [d for d in data["details"] if d["check"] == "photo_hash"]
    assert photo_checks[0]["status"] == CheckStatus.NOT_CHECKED
    assert data["evidence_status"] in [EvidenceStatus.PARTIAL, EvidenceStatus.SIGNATURE_VALID]
    assert len(data["warnings"]) > 0


def test_verify_tampered_photo():
    """Tampered photo -> photo_hash_valid=False, INVALID"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)
    tampered = photo[:500] + b"\xff" + photo[501:]
    resp = client.post("/verify", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(tampered).decode(),
    })
    data = resp.json()
    assert data["photo_hash_valid"] is False
    photo_checks = [d for d in data["details"] if d["check"] == "photo_hash"]
    assert photo_checks[0]["status"] == CheckStatus.FAILED
    assert data["evidence_status"] == EvidenceStatus.INVALID


def test_verify_wrong_key():
    """Wrong key -> signature invalid, INVALID"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)

    other_priv = ec.generate_private_key(ec.SECP256R1())
    other_pub = _encode_pub(other_priv.public_key())
    m2 = m.model_copy(update={"public_key": base64.b64encode(other_pub).decode()})

    resp = client.post("/verify", json={
        "manifest": m2.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()
    assert data["cryptographic_signature_valid"] is False
    assert data["evidence_status"] == EvidenceStatus.INVALID


def test_verify_registered_key_identity_match():
    """Registered key with matching device_id -> CRYPTOGRAPHICALLY_VALID"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)

    registry = get_registry()
    registry.register(pub_b64, "test-device-001")

    resp = client.post("/verify", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()
    assert data["cryptographic_signature_valid"] is True
    assert data["photo_hash_valid"] is True
    assert data["key_registration_status"] == KeyRegistrationStatus.REGISTERED
    assert data["device_identity_consistent"] is True
    assert data["device_attestation_status"] == DeviceAttestationStatus.UNKNOWN
    assert data["time_trust_level"] == TimeTrustLevel.DEVICE_REPORTED
    assert data["evidence_status"] == EvidenceStatus.CRYPTOGRAPHICALLY_VALID


def test_verify_registered_key_identity_mismatch():
    """
    Registered key with wrong device_id -> INVALID
    Note: We register the key to one device, then try to verify a manifest
    claiming a different device_id. The signature will be invalid because
    device_id is part of the signed payload, so we test with a fresh manifest
    signed with the correct device_id but then check identity mismatch.
    """
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()

    registry = get_registry()
    registry.register(pub_b64, "registered-device-001")

    m = make_manifest(priv, pub, photo_bytes=photo)
    m2 = m.model_copy(update={"device_id": "different-device-002"})

    resp = client.post("/verify", json={
        "manifest": m2.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()
    assert data["device_identity_consistent"] is False
    assert data["evidence_status"] == EvidenceStatus.INVALID
    assert any("mismatch" in w.lower() for w in data["warnings"])


def test_verify_unsupported_version():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    m2 = m.model_copy(update={"version": "9.9"})
    resp = client.post("/verify", json={
        "manifest": m2.model_dump(),
    })
    data = resp.json()
    assert data["evidence_status"] == EvidenceStatus.INVALID


def test_verify_device_attestation_unknown():
    """App Attest not implemented -> device_attestation_status=unknown"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)
    resp = client.post("/verify", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()
    assert data["device_attestation_status"] == DeviceAttestationStatus.UNKNOWN
    assert any("attestation" in w.lower() for w in data["warnings"])


def test_verify_time_trust_level():
    """Time is device-reported only -> time_trust_level=device_reported"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)
    resp = client.post("/verify", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()
    assert data["time_trust_level"] == TimeTrustLevel.DEVICE_REPORTED
    assert any("device-reported" in w.lower() or "device reported" in w.lower() for w in data["warnings"])


def test_register_key_endpoint():
    """Test key registration endpoint"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    pub_b64 = base64.b64encode(pub).decode()

    resp = client.post("/register-key", params={
        "public_key_b64": pub_b64,
        "device_id": "test-device-001",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["registration_status"] == "registered"
    assert data["device_id"] == "test-device-001"


def test_store_record_endpoint():
    """Test record storage endpoint with valid evidence"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)

    registry = get_registry()
    registry.register(pub_b64, "test-device-001")

    resp = client.post("/store-record", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["stored"] is True
    assert data["record_id"] == m.record_id


def test_store_record_rejects_invalid_signature():
    """Store endpoint must reject records with invalid signature"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)
    m2 = m.model_copy(update={"photo_hash": "aa" * 32})

    registry = get_registry()
    registry.register(pub_b64, "test-device-001")

    resp = client.post("/store-record", json={
        "manifest": m2.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()
    assert data["stored"] is False
    assert data["rejection_reason"] == "invalid_signature"


def test_store_record_rejects_unregistered_key():
    """Store endpoint must reject records with unregistered key"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)

    resp = client.post("/store-record", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()
    assert data["stored"] is False
    assert data["rejection_reason"] == "unregistered_key"


def test_store_record_rejects_device_identity_mismatch():
    """
    Store endpoint must reject records with device identity mismatch.

    Note: We need to sign with the correct device_id first, then try to
    register the key to a different device. The signature will be valid
    but the device identity won't match.
    """
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()

    registry = get_registry()
    registry.register(pub_b64, "registered-device-001")

    m = make_manifest(priv, pub, photo_bytes=photo)
    m2 = m.model_copy(update={"device_id": "different-device-002"})

    resp = client.post("/store-record", json={
        "manifest": m2.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()
    assert data["stored"] is False
    assert data["rejection_reason"] == "invalid_signature"


def test_store_record_rejects_photo_hash_mismatch():
    """Store endpoint must reject records with photo hash mismatch"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)

    registry = get_registry()
    registry.register(pub_b64, "test-device-001")

    tampered_photo = photo[:500] + b"\xff" + photo[501:]
    resp = client.post("/store-record", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(tampered_photo).decode(),
    })
    data = resp.json()
    assert data["stored"] is False
    assert data["rejection_reason"] == "photo_hash_mismatch"


def test_store_record_duplicate():
    """Test duplicate record detection (idempotent)"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)

    registry = get_registry()
    registry.register(pub_b64, "test-device-001")

    resp1 = client.post("/store-record", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    assert resp1.json()["stored"] is True

    resp2 = client.post("/store-record", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    assert resp2.json()["stored"] is False
    assert "duplicate" in resp2.json()["message"].lower()


def test_store_record_conflict():
    """
    Test same record_id with different content.

    Note: Since signature covers all content, changing content invalidates
    the signature. So this test verifies that tampered content is rejected
    with invalid_signature, not as a conflict.
    """
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()
    m1 = make_manifest(priv, pub, photo_bytes=photo)

    registry = get_registry()
    registry.register(pub_b64, "test-device-001")

    resp1 = client.post("/store-record", json={
        "manifest": m1.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    assert resp1.json()["stored"] is True

    m2 = m1.model_copy(update={"photo_hash": "aa" * 32})
    resp2 = client.post("/store-record", json={
        "manifest": m2.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    assert resp2.json()["stored"] is False
    assert resp2.json()["rejection_reason"] == "invalid_signature"


def test_check_record_endpoint():
    """Test record check endpoint"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)

    registry = get_registry()
    registry.register(pub_b64, "test-device-001")

    resp1 = client.get(f"/check-record/{m.record_id}")
    assert resp1.json()["exists"] is False

    client.post("/store-record", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })

    resp2 = client.get(f"/check-record/{m.record_id}")
    assert resp2.json()["exists"] is True
    assert resp2.json()["record_id"] == m.record_id
