"""TM03: device key lifecycle - rotation, suspension, history.

Pinned behaviours:
  1. rotate: old-key possession proof (single-use nonce), token stable,
     key swapped, history rows written
  2. after rotation the OLD key cannot authorize (challenge-verify with
     old key fails) and cannot enroll anywhere (rollback guard)
  3. suspended device is refused by every protected API and by rotation;
     resume restores access; revocation stays final
  4. key_status_for_public_key separates registration state from
     signature math: rotated_out keys stay resolvable forever
  5. full audit trail via /keys (admin-gated)
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding, PublicFormat)
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import device_identity  # noqa: E402

import pytest  # noqa: E402


CODE = "tm03-code"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", CODE)
    import app.db as dbm
    dbm.migrate(tmp_path / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    monkeypatch.setattr(device_identity, "check_rate",
                        lambda *a, **k: None)
    client = TestClient(app)

    def enroll(name):
        key = ec.generate_private_key(ec.SECP256R1())
        pub = key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)
        pub_b64 = base64.b64encode(pub).decode()
        r = client.post("/api/v1/devices/enroll",
                        json={"public_key_b64": pub_b64,
                              "device_id": name,
                              "enrollment_code": CODE})
        assert r.status_code in (200, 201), r.text
        return key, pub_b64, r.json()["device_token"]

    k1, pub1, tok = enroll("tm03-device")
    return type("E", (), {
        "client": client,
        "k1": k1, "pub1": pub1, "tok": tok,
        "admin": {"X-Admin-Code": CODE},
        "H": {"X-Device-Token": tok},
        "enroll": enroll,
    })()


def _sign(key, text: str) -> str:
    sig = key.sign(text.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(sig).decode()


def _new_key_b64():
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    return k, base64.b64encode(pub).decode()


def test_rotation_full_chain(env):
    k2, pub2 = _new_key_b64()
    # 1. prepare: nonce bound to the device token
    r = env.client.post("/api/v1/devices/rotate/prepare",
                        headers=env.H)
    assert r.status_code == 200, r.text
    nonce = r.json()["nonce"]
    # 2. commit with the OLD key's signature over the nonce
    r = env.client.post("/api/v1/devices/rotate/commit",
                        headers=env.H,
                        json={"nonce": nonce,
                              "signature_b64": _sign(env.k1, nonce),
                              "new_public_key_b64": pub2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_key_fingerprint"] != body["old_key_fingerprint"]
    # 3. token is UNCHANGED - rotation must not break ownership keys
    r = env.client.get("/api/v1/projects?limit=1", headers=env.H)
    assert r.status_code == 200, r.text
    # 4. the OLD key no longer proves possession
    r = env.client.post("/api/v1/devices/challenge", headers=env.H)
    nonce2 = r.json()["nonce"]
    r = env.client.post("/api/v1/devices/challenge/verify",
                        json={"device_token": env.tok, "nonce": nonce2,
                              "signature_b64": _sign(env.k1, nonce2)})
    assert r.status_code == 401, "old key must no longer authorize"
    # the NEW key does
    r = env.client.post("/api/v1/devices/challenge", headers=env.H)
    nonce3 = r.json()["nonce"]
    r = env.client.post("/api/v1/devices/challenge/verify",
                        json={"device_token": env.tok, "nonce": nonce3,
                              "signature_b64": _sign(k2, nonce3)})
    assert r.status_code == 200, r.text


def test_rotation_requires_old_key_proof(env):
    _, pub2 = _new_key_b64()
    attacker = ec.generate_private_key(ec.SECP256R1())
    r = env.client.post("/api/v1/devices/rotate/prepare", headers=env.H)
    nonce = r.json()["nonce"]
    # sign with a DIFFERENT key - the attack: enroll attacker's key by
    # presenting a fresh nonce but no possession proof of the old one
    r = env.client.post("/api/v1/devices/rotate/commit",
                        headers=env.H,
                        json={"nonce": nonce,
                              "signature_b64": _sign(attacker, nonce),
                              "new_public_key_b64": pub2})
    assert r.status_code == 401, r.text
    # nonce consumed; replay with the correct key also fails now
    r = env.client.post("/api/v1/devices/rotate/commit",
                        headers=env.H,
                        json={"nonce": nonce,
                              "signature_b64": _sign(env.k1, nonce),
                              "new_public_key_b64": pub2})
    assert r.status_code == 401, "single-use nonce must be consumed"


def test_rotated_out_key_cannot_reenroll_rollback_guard(env):
    k2, pub2 = _new_key_b64()
    r = env.client.post("/api/v1/devices/rotate/prepare", headers=env.H)
    nonce = r.json()["nonce"]
    r = env.client.post("/api/v1/devices/rotate/commit",
                        headers=env.H,
                        json={"nonce": nonce,
                              "signature_b64": _sign(env.k1, nonce),
                              "new_public_key_b64": pub2})
    assert r.status_code == 200
    # the RETIRED old key tries to enroll as a NEW device
    r = env.client.post("/api/v1/devices/enroll",
                        json={"public_key_b64": env.pub1,
                              "device_id": "clone-device",
                              "enrollment_code": CODE})
    assert r.status_code == 409 and "rollback" in r.text.lower()
    # rotating BACK to the old key is refused too
    r = env.client.post("/api/v1/devices/rotate/prepare", headers=env.H)
    nonce2 = r.json()["nonce"]
    r = env.client.post("/api/v1/devices/rotate/commit",
                        headers=env.H,
                        json={"nonce": nonce2,
                              "signature_b64": _sign(k2, nonce2),
                              "new_public_key_b64": env.pub1})
    assert r.status_code == 409 and "retired" in r.text.lower()


def test_suspend_resume_lifecycle(env):
    # business access before suspend
    r = env.client.get("/api/v1/projects?limit=1", headers=env.H)
    assert r.status_code == 200
    # suspend (admin)
    r = env.client.post(f"/api/v1/devices/{env.tok}/suspend",
                        headers=env.admin)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "suspended"
    # every protected API refuses a suspended device
    r = env.client.get("/api/v1/projects?limit=1", headers=env.H)
    assert r.status_code == 401 and "suspended" in r.text
    # rotation also refused
    r = env.client.post("/api/v1/devices/rotate/prepare", headers=env.H)
    assert r.status_code == 401
    # resume (admin) restores access
    r = env.client.post(f"/api/v1/devices/{env.tok}/resume",
                        headers=env.admin)
    assert r.status_code == 200 and r.json()["status"] == "active"
    r = env.client.get("/api/v1/projects?limit=1", headers=env.H)
    assert r.status_code == 200
    # suspend requires the admin code
    r = env.client.post(f"/api/v1/devices/{env.tok}/suspend",
                        headers={"X-Admin-Code": "wrong"})
    assert r.status_code == 401


def test_revocation_stays_final_after_suspend(env):
    env.client.post(f"/api/v1/devices/{env.tok}/suspend",
                    headers=env.admin)
    # resume of a revoked device is refused; revoke first (the revoke
    # endpoint still requires its historical body admin_code field)
    r = env.client.post(f"/api/v1/devices/{env.tok}/revoke",
                        headers=env.admin, json={"admin_code": CODE})
    assert r.status_code == 200
    r = env.client.post(f"/api/v1/devices/{env.tok}/resume",
                        headers=env.admin)
    assert r.status_code == 409 and "revoked" in r.text.lower()


def test_key_status_separates_registration_from_math(env):
    # BEFORE rotation: current key -> active, is_current_enrolled_key
    st = device_identity.key_status_for_public_key(env.pub1)
    assert st["enrollment_status"] == "active"
    assert st["is_current_enrolled_key"] is True
    # rotate away
    k2, pub2 = _new_key_b64()
    r = env.client.post("/api/v1/devices/rotate/prepare", headers=env.H)
    nonce = r.json()["nonce"]
    env.client.post("/api/v1/devices/rotate/commit",
                    headers=env.H,
                    json={"nonce": nonce,
                          "signature_b64": _sign(env.k1, nonce),
                          "new_public_key_b64": pub2})
    # OLD key: still resolvable (historical evidence!), but not current
    st_old = device_identity.key_status_for_public_key(env.pub1)
    assert st_old["enrollment_status"] == "rotated_out"
    assert st_old["is_current_enrolled_key"] is False
    # NEW key is current
    st_new = device_identity.key_status_for_public_key(pub2)
    assert st_new["enrollment_status"] == "active"
    assert st_new["is_current_enrolled_key"] is True
    # unknown key
    _, pub_unknown = _new_key_b64()
    st_u = device_identity.key_status_for_public_key(pub_unknown)
    assert st_u["enrollment_status"] == "unknown"


def test_key_history_audit_trail(env):
    k2, pub2 = _new_key_b64()
    r = env.client.post("/api/v1/devices/rotate/prepare", headers=env.H)
    nonce = r.json()["nonce"]
    env.client.post("/api/v1/devices/rotate/commit",
                    headers=env.H,
                    json={"nonce": nonce,
                          "signature_b64": _sign(env.k1, nonce),
                          "new_public_key_b64": pub2})
    env.client.post(f"/api/v1/devices/{env.tok}/suspend",
                    headers=env.admin)
    env.client.post(f"/api/v1/devices/{env.tok}/resume",
                    headers=env.admin)
    r = env.client.get(f"/api/v1/devices/{env.tok}/keys",
                       headers=env.admin)
    assert r.status_code == 200, r.text
    events = [k["event"] for k in r.json()["keys"]]
    assert "rotated_out" in events and "rotated_in" in events
    assert "suspended" in events and "resumed" in events
    # admin gate enforced
    r = env.client.get(f"/api/v1/devices/{env.tok}/keys")
    assert r.status_code == 401
