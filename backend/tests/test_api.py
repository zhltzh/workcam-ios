from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import make_manifest, make_test_photo, _encode_pub


client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_verify_valid():
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
    assert data["valid"] is True


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
    assert photo_checks[0]["passed"] is False


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
    assert data["valid"] is False


def test_verify_no_photo():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    resp = client.post("/verify", json={
        "manifest": m.model_dump(),
    })
    data = resp.json()
    assert data["valid"] is True
    sig_checks = [d for d in data["details"] if d["check"] == "ecdsa_signature"]
    assert sig_checks[0]["passed"] is True


def test_verify_unsupported_version():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    m2 = m.model_copy(update={"version": "9.9"})
    resp = client.post("/verify", json={
        "manifest": m2.model_dump(),
    })
    data = resp.json()
    assert data["valid"] is False
