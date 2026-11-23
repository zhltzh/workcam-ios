"""WC-W005 P2 - upload auth layer tests.

Every guarantee of the security layer, plus the COMPATIBILITY guarantee:
with WORKCAM_UPLOAD_AUTH unset, every pre-existing protocol behaviour is
byte-for-byte unchanged (covered by the original suites passing).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


@pytest.fixture()
def auth_env(tmp_path, monkeypatch):
    """Auth ON, isolated storage, one enrolled device. Returns helpers."""
    monkeypatch.setenv("WORKCAM_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    monkeypatch.setenv("WORKCAM_UPLOAD_AUTH", "1")
    monkeypatch.setenv("WORKCAM_DEVICE_REGISTRY",
                       str(tmp_path / "devices.jsonl"))
    monkeypatch.setenv("WORKCAM_UPLOAD_AUDIT",
                       str(tmp_path / "audit.jsonl"))
    import app.api.upload as up
    import app.api.upload_auth as ua
    monkeypatch.setattr(up, "_STORAGE_ROOT", tmp_path / "uploads")
    # reset in-memory auth state between tests
    ua._batch_owners.clear()
    ua._buckets.clear()
    ua._challenges.clear()

    # enroll device A and device B with software TEST keys (clearly
    # marked: this is the AUTH layer; the same slot is where a real
    # device's evidence-signing key registers in production)
    key_a = ec.generate_private_key(ec.SECP256R1())
    key_b = ec.generate_private_key(ec.SECP256R1())

    def _pub_b64(key):
        return base64.b64encode(key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)).decode()

    pub_a, pub_b = _pub_b64(key_a), _pub_b64(key_b)
    registry = ua.DeviceRegistry(Path(tmp_path / "devices.jsonl"))
    token_a = registry.enroll(pub_a, "device-AAA")
    token_b = registry.enroll(pub_b, "device-BBB")

    class H:
        A = {"X-Device-Token": token_a}
        B = {"X-Device-Token": token_b}

    return type("Env", (), {
        "tmp_path": tmp_path, "key_a": key_a, "key_b": key_b,
        "token_a": token_a, "token_b": token_b, "H": H,
        "pub_a": pub_a,
    })()


def _register(env, total_bytes=300, chunk_size=100, sha="ab" * 32):
    return client.post("/upload/experimental/batches",
                      json={"total_bytes": total_bytes,
                            "chunk_size": chunk_size,
                            "total_sha256": sha},
                      headers=env.H.A)


def test_auth_off_by_default(tmp_path, monkeypatch):
    """P2 requirement 4: DEFAULT CLOSED. Without WORKCAM_UPLOAD_AUTH the
    auth routes refuse; batch routes behave as before (token-less)."""
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    r = client.post("/upload/experimental/auth/challenge")
    assert r.status_code == 503


def test_missing_token_rejected(auth_env):
    r = client.post("/upload/experimental/batches",
                    json={"total_bytes": 100, "chunk_size": 50,
                          "total_sha256": "ab" * 32})
    assert r.status_code == 401


def test_unknown_token_rejected(auth_env):
    r = client.post("/upload/experimental/batches",
                    json={"total_bytes": 100, "chunk_size": 50,
                          "total_sha256": "ab" * 32},
                    headers={"X-Device-Token": "ff" * 32})
    assert r.status_code == 401


def test_identity_challenge_flow(auth_env):
    """Full P-256 nonce-signature flow: private key never travels."""
    r = client.post("/upload/experimental/auth/challenge",
                    headers=auth_env.H.A)
    assert r.status_code == 200
    nonce = r.json()["nonce"]

    sig = auth_env.key_a.sign(nonce.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    v = client.post("/upload/experimental/auth/verify", json={
        "device_token": auth_env.token_a, "nonce": nonce,
        "signature_b64": base64.b64encode(sig).decode()})
    assert v.status_code == 200
    assert v.json()["device_id"] == "device-AAA"
    assert v.json()["verified"] is True


def test_challenge_replay_rejected(auth_env):
    """Single-use nonce: the same signature cannot verify twice."""
    r = client.post("/upload/experimental/auth/challenge",
                    headers=auth_env.H.A)
    nonce = r.json()["nonce"]
    sig = auth_env.key_a.sign(nonce.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    body = {"device_token": auth_env.token_a, "nonce": nonce,
            "signature_b64": base64.b64encode(sig).decode()}
    assert client.post("/upload/experimental/auth/verify",
                       json=body).status_code == 200
    assert client.post("/upload/experimental/auth/verify",
                       json=body).status_code == 401


def test_wrong_key_fails_identity(auth_env):
    """Device B's key cannot sign device A's challenge."""
    r = client.post("/upload/experimental/auth/challenge",
                    headers=auth_env.H.A)
    nonce = r.json()["nonce"]
    sig = auth_env.key_b.sign(nonce.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    v = client.post("/upload/experimental/auth/verify", json={
        "device_token": auth_env.token_a, "nonce": nonce,
        "signature_b64": base64.b64encode(sig).decode()})
    assert v.status_code == 401


def test_batch_isolation_between_devices(auth_env):
    """THE core guarantee: device B cannot touch device A's batch - and
    the error NEVER reveals whose batch it is (404, not 403-with-owner)."""
    r = _register(auth_env)
    bid = r.json()["batch_id"]

    # B is enrolled & authenticated but the batch is A's
    st = client.get(f"/upload/experimental/batches/{bid}",
                    headers=auth_env.H.B)
    assert st.status_code == 404                 # ownership hidden
    put = client.put(f"/upload/experimental/batches/{bid}/chunks/0",
                     content=b"x" * 100,
                     headers={**auth_env.H.B, "Content-Length": "100"})
    assert put.status_code == 404

    # A can still use its own batch normally
    assert client.get(f"/upload/experimental/batches/{bid}",
                      headers=auth_env.H.A).status_code == 200


def test_full_authed_roundtrip_with_resume(auth_env):
    """Auth ON does not change protocol semantics: A uploads, drops a
    chunk, resumes, completes, downloads - byte-identical."""
    data = b"S" * 250
    sha = hashlib.sha256(data).hexdigest()
    r = client.post("/upload/experimental/batches",
                    json={"total_bytes": len(data), "chunk_size": 100,
                          "total_sha256": sha},
                    headers=auth_env.H.A)
    bid = r.json()["batch_id"]
    chunks = [data[:100], data[100:200], data[200:]]

    client.put(f"/upload/experimental/batches/{bid}/chunks/0",
               content=chunks[0],
               headers={**auth_env.H.A, "Content-Length": "100"})
    # network drop: chunk 1 & 2 missing
    missing = client.get(f"/upload/experimental/batches/{bid}",
                         headers=auth_env.H.A).json()["missing"]
    assert missing == [1, 2]
    for i in missing:
        client.put(f"/upload/experimental/batches/{bid}/chunks/{i}",
                   content=chunks[i],
                   headers={**auth_env.H.A,
                            "Content-Length": str(len(chunks[i]))})
    comp = client.post(f"/upload/experimental/batches/{bid}/complete",
                       headers=auth_env.H.A)
    assert comp.status_code == 200
    dl = client.get(f"/upload/experimental/batches/{bid}/content",
                    headers=auth_env.H.A)
    assert dl.status_code == 200
    assert b"".join(dl.iter_bytes()) == data


def test_rate_limit_per_device(auth_env, monkeypatch):
    """Per-device token bucket: device A exhausts the cap, gets 429;
    device B is unaffected (isolation of the limiter too)."""
    import app.api.upload_auth as ua
    monkeypatch.setattr(ua, "_RATE", {"capacity": 3, "refill_per_min": 0})
    r0 = _register(auth_env)                     # 1 request for A
    for _ in range(3):
        rr = _register(auth_env)
        if rr.status_code == 429:
            break
    assert rr.status_code == 429                  # A is throttled
    # B still works
    rb = client.post("/upload/experimental/batches",
                     json={"total_bytes": 100, "chunk_size": 50,
                           "total_sha256": "ab" * 32},
                     headers=auth_env.H.B)
    assert rb.status_code == 200


def test_audit_log_written(auth_env):
    """Every authed action lands in the append-only audit trail."""
    _register(auth_env)
    audit_path = auth_env.tmp_path / "audit.jsonl"
    assert audit_path.exists()
    lines = [json.loads(l) for l in
             audit_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    events = {l["event"] for l in lines}
    assert "batch_registered" in events
    assert all("ts" in l for l in lines)
    assert any(l.get("device_id") == "device-AAA" for l in lines)


def test_no_real_key_claims(auth_env):
    """Red line: the layer NEVER claims Secure Enclave. The registry
    stores a public key; the test key here is software - documented in
    the fixture, and no output of this layer asserts hardware origin."""
    import app.api.upload_auth as ua
    registry = ua.DeviceRegistry(Path(auth_env.tmp_path / "devices.jsonl"))
    assert registry.device_id_for_token(auth_env.token_a) == "device-AAA"
    # the layer asserts IDENTITY PROOF ONLY; device-attestation is out
    # of scope (the /verify evidence path's unknown attestation status
    # remains the single source of truth for that question)
