from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.main import app
from app.models.capture import CheckStatus, EvidenceStatus, KeyRegistrationStatus
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


def test_verify_valid_with_photo():
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
    photo_checks = [d for d in data["details"] if d["check"] == "photo_hash"]
    assert photo_checks[0]["status"] == CheckStatus.PASSED


def test_verify_no_photo_returns_not_checked():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    resp = client.post("/verify", json={
        "manifest": m.model_dump(),
    })
    data = resp.json()
    assert data["cryptographic_signature_valid"] is True
    photo_checks = [d for d in data["details"] if d["check"] == "photo_hash"]
    assert photo_checks[0]["status"] == CheckStatus.NOT_CHECKED
    assert data["evidence_status"] == EvidenceStatus.PARTIAL
    assert len(data["warnings"]) > 0
    assert any("photo" in w.lower() for w in data["warnings"])


def test_verify_tampered_photo():
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
    photo_checks = [d for d in data["details"] if d["check"] == "photo_hash"]
    assert photo_checks[0]["status"] == CheckStatus.FAILED
    assert data["evidence_status"] == EvidenceStatus.INVALID


def test_verify_wrong_key():
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


def test_verify_unregistered_key():
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
    assert any("not registered" in w.lower() for w in data["warnings"])


def test_verify_registered_key():
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
    assert data["key_registration_status"] == KeyRegistrationStatus.REGISTERED
    assert data["evidence_status"] == EvidenceStatus.VERIFIED


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
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)
    resp = client.post("/verify", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()
    assert data["device_attestation_status"] == "unknown"
    assert any("attestation" in w.lower() for w in data["warnings"])
