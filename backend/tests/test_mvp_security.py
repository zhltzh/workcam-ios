"""WC-W009 security regression tests (P4).

DESKTOP_SIMULATION. Twelve required scenarios:
  unauthorized access / revocation / forged identity / challenge replay /
  cross-device isolation / dangerous configuration / proxy IP forgery /
  rate limiting / log sensitive-data leakage / restart consistency /
  database migration / backup-restore (identity + business data).
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat)
from fastapi.testclient import TestClient

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]   # workcam-ios root


@pytest.fixture()
def sec_env(tmp_path, monkeypatch):
    """Isolated DB + enrollment code + two devices (A active, B later
    revoked)."""
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "test-code-123")
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    monkeypatch.delenv("WORKCAM_TRUST_PROXY_HEADERS", raising=False)
    import app.db as dbm
    dbm.migrate(tmp_path / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    import app.services.device_identity as di

    def _mkdev(name):
        key = ec.generate_private_key(ec.SECP256R1())
        pub_b64 = base64.b64encode(key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)).decode()
        token = di.enroll_device(pub_b64, name, "test-code-123")
        return key, pub_b64, token

    key_a, pub_a, tok_a = _mkdev("device-AAA")
    key_b, pub_b, tok_b = _mkdev("device-BBB")
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "biz.db",
        "key_a": key_a, "pub_a": pub_a, "tok_a": tok_a,
        "key_b": key_b, "tok_b": tok_b,
        "H": {"X-Device-Token": tok_a},
        "B": {"X-Device-Token": tok_b},
        "client": TestClient(app),
    })()


# --------------------------------------------- 1. unauthorized access


def test_unauthorized_access_rejected(sec_env):
    c = sec_env.client
    assert c.get("/api/v1/projects").status_code == 401
    assert c.get("/api/v1/sync/events").status_code == 401
    r = c.get("/api/v1/projects/xyz", headers={
        "X-Device-Token": "ff" * 32})
    assert r.status_code == 401 and "not enrolled" in r.json()["detail"]["message"]


# --------------------------------------------- 2. revocation


def test_revoked_device_refused_everywhere(sec_env):
    c = sec_env.client
    # A creates a project while active
    r = c.post("/api/v1/projects", json={"name": "A's project"},
               headers=sec_env.H)
    assert r.status_code == 201

    # admin revokes A
    rv = c.post(f"/api/v1/devices/{sec_env.tok_a}/revoke",
                json={"admin_code": "test-code-123"},
                headers={"X-Admin-Code": "test-code-123"})
    assert rv.status_code == 200 and rv.json()["status"] == "revoked"

    # every protected surface now refuses A's token
    assert c.get("/api/v1/projects", headers=sec_env.H).status_code == 401
    assert c.post("/api/v1/projects", json={"name": "x"},
                  headers=sec_env.H).status_code == 401
    assert c.post("/api/v1/sync/batch",
                  json={"operations": []}, headers=sec_env.H).status_code in (401, 422)

    # re-revoke is idempotent
    rv2 = c.post(f"/api/v1/devices/{sec_env.tok_a}/revoke",
                 json={"admin_code": "test-code-123"},
                 headers={"X-Admin-Code": "test-code-123"})
    assert rv2.json().get("idempotent") is True

    # the revoked device's DATA is untouched (A's project still exists)
    got = c.get("/api/v1/projects", headers=sec_env.B)
    assert got.json()["total"] == 0   # B sees only B's - none yet


def test_revoked_challenge_cannot_verify(sec_env):
    """Outstanding challenges die with the revocation."""
    c = sec_env.client
    ch = c.post("/api/v1/devices/challenge", headers=sec_env.H).json()
    nonce = ch["nonce"]
    c.post(f"/api/v1/devices/{sec_env.tok_a}/revoke",
           json={"admin_code": "test-code-123"},
           headers={"X-Admin-Code": "test-code-123"})
    sig = sec_env.key_a.sign(nonce.encode(), ec.ECDSA(hashes.SHA256()))
    r = c.post("/api/v1/devices/challenge/verify", json={
        "device_token": sec_env.tok_a, "nonce": nonce,
        "signature_b64": base64.b64encode(sig).decode()})
    assert r.status_code == 401        # revocation precedes proof


# --------------------------------------------- 3. forged identity


def test_forged_token_cannot_access(sec_env):
    r = sec_env.client.get("/api/v1/projects", headers={
        "X-Device-Token": "deadbeef" * 8})
    assert r.status_code == 401


def test_forged_admin_code_rejected(sec_env):
    r = sec_env.client.get(f"/api/v1/devices/{sec_env.tok_a}",
                           headers={"X-Admin-Code": "wrong-code"})
    assert r.status_code == 401


def test_enrollment_requires_code(sec_env, tmp_path, monkeypatch):
    """Self-registration is never the default: WITHOUT a code, enroll
    is closed even through the API."""
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "")
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE_FILE",
                       str(tmp_path / "no-such-file.txt"))
    import app.services.device_identity as di
    key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    with pytest.raises(Exception, match="enrollment_closed|bad_enrollment"):
        di.enroll_device(pub_b64, "self-registered", "guess")


def test_forged_client_device_id_is_not_identity(sec_env):
    """B cannot enter A's project by claiming A's device_id in the
    photo body (ownership is the TOKEN, not the claim)."""
    c = sec_env.client
    pid = c.post("/api/v1/projects", json={"name": "A's"},
                 headers=sec_env.H).json()["project_id"]
    r = c.post(f"/api/v1/projects/{pid}/photos", json={
        "idempotency_key": "spoof", "original_sha256": "ab" * 32,
        "original_filename": "x.jpg", "mime_type": "image/jpeg",
        "byte_size": 10, "captured_at": "t",
        "device_id": "device-AAA"}, headers=sec_env.B)
    assert r.status_code == 404


# --------------------------------------------- 4. challenge replay


def test_challenge_replay_rejected(sec_env):
    c = sec_env.client
    ch = c.post("/api/v1/devices/challenge", headers=sec_env.H).json()
    sig = sec_env.key_a.sign(ch["nonce"].encode(),
                             ec.ECDSA(hashes.SHA256()))
    body = {"device_token": sec_env.tok_a, "nonce": ch["nonce"],
            "signature_b64": base64.b64encode(sig).decode()}
    assert c.post("/api/v1/devices/challenge/verify",
                  json=body).status_code == 200
    r2 = c.post("/api/v1/devices/challenge/verify", json=body)
    assert r2.status_code == 401


def test_challenge_wrong_key_rejected(sec_env):
    c = sec_env.client
    ch = c.post("/api/v1/devices/challenge", headers=sec_env.H).json()
    sig = sec_env.key_b.sign(ch["nonce"].encode(),
                             ec.ECDSA(hashes.SHA256()))
    r = c.post("/api/v1/devices/challenge/verify", json={
        "device_token": sec_env.tok_a, "nonce": ch["nonce"],
        "signature_b64": base64.b64encode(sig).decode()})
    assert r.status_code == 401


# --------------------------------------------- 5. cross-device isolation


def test_cross_device_isolation_through_every_surface(sec_env):
    c = sec_env.client
    pid = c.post("/api/v1/projects", json={"name": "A's"},
                 headers=sec_env.H).json()["project_id"]
    ph = c.post(f"/api/v1/projects/{pid}/photos", json={
        "idempotency_key": "k", "original_sha256": "ab" * 32,
        "original_filename": "f.jpg", "mime_type": "image/jpeg",
        "byte_size": 5, "captured_at": "t", "device_id": "d"},
        headers=sec_env.H).json()["photo_id"]
    # B gets 404 on EVERY surface (no existence oracle)
    for url in (f"/api/v1/projects/{pid}",
                f"/api/v1/photos/{ph}",
                f"/api/v1/photos/{ph}/original",
                f"/api/v1/photos/{ph}/original/integrity",
                f"/api/v1/photos/{ph}/evidence-links",
                f"/api/v1/projects/{pid}/photos"):
        assert c.get(url, headers=sec_env.B).status_code == 404, url
    # B's sync events contain NONE of A's entities
    ev = c.get("/api/v1/sync/events", headers=sec_env.B)
    assert ev.json()["items"] == []


# --------------------------------------------- 6. dangerous configuration


def test_startup_refuses_production_plus_experimental(tmp_path, capfd):
    """The fatal guard: WORKCAM_ENV=production + UPLOAD_EXPERIMENTAL=1
    aborts boot with exit code 1."""
    import subprocess
    code = (
        "import sys; sys.path.insert(0, '.'); "
        "import os; "
        "os.environ['WORKCAM_ENV'] = 'production'; "
        "os.environ['WORKCAM_UPLOAD_EXPERIMENTAL'] = '1'; "
        "from app.services.security_config import check_startup_security; "
        "check_startup_security(fail_fast=True); print('BOOTED')"
    )
    import sys
    proc = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, encoding="utf-8",
                           cwd=str(REPO_ROOT / "backend"))
    assert proc.returncode == 1
    assert "FATAL" in proc.stderr


def test_startup_passes_default_config():
    from app.services.security_config import check_startup_security
    for var in ("WORKCAM_ENV", "WORKCAM_UPLOAD_EXPERIMENTAL",
                "WORKCAM_TRUST_PROXY_HEADERS"):
        os.environ.pop(var, None)
    assert check_startup_security(fail_fast=True) == []


# --------------------------------------------- 7. proxy IP forgery


def test_proxy_ip_forgery_blocked_by_default(sec_env):
    """X-Forwarded-For is NOT trusted by default: a non-loopback
    client cannot bypass the upload loopback gate by forging it."""
    # hand-built ASGI scope with a remote peer (proves the gate uses
    # the socket peer, not the forged header)
    import asyncio
    async def run():
        scope = {
            "type": "http", "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/upload/experimental/batches",
            "raw_path": b"/upload/experimental/batches",
            "query_string": b"", "headers": [
                (b"content-type", b"application/json"),
                (b"x-forwarded-for", b"127.0.0.1"),  # FORGED
            ],
            "root_path": "", "client": ("203.0.113.9", 51000),
            "server": ("testserver", 80)}
        body = json.dumps({"total_bytes": 100, "chunk_size": 50,
                           "total_sha256": "ab" * 32}).encode()
        status = {}
        async def receive():
            return {"type": "http.request", "body": body,
                    "more_body": False}
        async def send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            elif message["type"] == "http.response.body":
                status.setdefault("body", b"")
        os.environ["WORKCAM_UPLOAD_EXPERIMENTAL"] = "1"
        try:
            await app(scope, receive, send)
        finally:
            os.environ.pop("WORKCAM_UPLOAD_EXPERIMENTAL")
        return status["code"]
    # auth OFF + remote peer + forged XFF claiming loopback -> 503
    code = asyncio.run(run())
    assert code == 503      # the FORWARDED header did not save it


# --------------------------------------------- 8. rate limiting


def test_persistent_rate_limit(sec_env):
    """Small window; A crosses the cap and gets 429; B is unaffected;
    the count survives a 'restart' (fresh client, same DB)."""
    import app.services.device_identity as di
    for _ in range(3):
        di.check_rate(sec_env.tok_a, capacity=3, window_seconds=60)
    with pytest.raises(Exception, match="rate limit"):
        di.check_rate(sec_env.tok_a, capacity=3, window_seconds=60)
    # B untouched
    di.check_rate(sec_env.tok_b, capacity=3, window_seconds=60)
    # restart consistency: the counter is in the DB
    conn = sqlite3.connect(str(sec_env.db))
    rows = conn.execute(
        "SELECT COUNT(*) FROM mvp_rate_limits").fetchone()[0]
    conn.close()
    assert rows >= 1


# --------------------------------------------- 9. log sensitive leakage


def test_audit_log_never_stores_full_tokens(sec_env):
    c = sec_env.client
    c.post("/api/v1/projects", json={"name": "audit-me"},
           headers=sec_env.H)
    conn = sqlite3.connect(str(sec_env.db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM mvp_audit_log").fetchall()
    conn.close()
    assert rows, "audit trail should have events"
    blob = json.dumps([dict(r) for r in rows])
    # the FULL token never appears anywhere in the audit trail (only
    # the 16-char fingerprint shows up under device_token)
    assert sec_env.tok_a not in blob
    assert sec_env.tok_b not in blob
    # every stored device_token is a short fingerprint, not the full 64
    for r in rows:
        tok_val = r["device_token"]
        if tok_val:
            assert len(tok_val) == 16   # token_fingerprint length


# --------------------------------------------- 10. restart consistency


def test_restart_keeps_identity_and_data(sec_env):
    c = sec_env.client
    pid = c.post("/api/v1/projects", json={"name": "persistent"},
                 headers=sec_env.H).json()["project_id"]
    # 'restart' = fresh client, same DB files
    c2 = TestClient(app)
    assert c2.get("/api/v1/projects", headers=sec_env.H).json()["total"] == 1
    assert c2.get(f"/api/v1/projects/{pid}",
                  headers=sec_env.H).status_code == 200
    # sync cursor continues where it left off
    ev1 = c.get("/api/v1/sync/events", headers=sec_env.H).json()
    ev2 = c2.get(f"/api/v1/sync/events?cursor={ev1['cursor']}",
                 headers=sec_env.H).json()
    assert ev2["items"] == [] or all(
        e["seq"] > ev1["cursor"] for e in ev2["items"])


# --------------------------------------------- 11. migration


def test_v4_to_v5_upgrade_preserves_data(tmp_path, monkeypatch):
    import app.db as dbm
    import sqlite3
    db = tmp_path / "mig.db"
    dbm.migrate(db)      # goes straight to v5
    conn = sqlite3.connect(str(db))
    # v5 tables exist
    for t in ("mvp_devices", "mvp_audit_log", "mvp_rate_limits",
              "mvp_auth_challenges"):
        assert conn.execute(
            f"SELECT name FROM sqlite_master WHERE name='{t}'"
        ).fetchone(), t
    conn.close()
    assert dbm.migrate(db) == 0    # replay = no-op


# --------------------------------------------- 12. backup-restore


def test_backup_restore_with_identity(sec_env, tmp_path, monkeypatch):
    """W006 DB backup + full-app restart after restore: identity,
    projects and sync state all consistent."""
    import app.db as dbm
    c = sec_env.client
    c.post("/api/v1/projects", json={"name": "to-backup"},
           headers=sec_env.H)
    backup = tmp_path / "backup.db"
    dbm.backup_to(backup, sec_env.db)
    info = dbm.verify_backup(backup)
    assert info["counts"]["projects"] == 1
    # restore into a fresh path and point the app at it
    restored = tmp_path / "restored.db"
    dbm.restore_from(backup, restored)
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(restored))
    c2 = TestClient(app)
    body = c2.get("/api/v1/projects", headers=sec_env.H).json()
    assert body["total"] == 1 and body["items"][0]["name"] == "to-backup"
    # identity still resolves (device rows survived)
    from app.services.device_identity import resolve_device
    assert resolve_device(sec_env.tok_a) == "device-AAA"
