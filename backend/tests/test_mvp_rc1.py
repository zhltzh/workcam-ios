"""WC-W010 RC1 integration-freeze verification (cross-module E2E).

DESKTOP_SIMULATION. One continuous chain through EVERY W005-W009
capability with the negative branches interleaved:

  project -> photo -> original upload -> evidence link -> server-side
  verify -> sync pull -> download -> integrity re-check

Negative coverage inline: unauthorized / revoked / replay / cross-device
isolation / disconnect-recovery / service restart.
"""

from __future__ import annotations

import base64
import hashlib
import sqlite3
import uuid

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import make_manifest, _encode_pub


@pytest.fixture()
def rc1_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "rc1-code")
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    monkeypatch.delenv("WORKCAM_TRUST_PROXY_HEADERS", raising=False)
    import app.db as dbm
    dbm.migrate(tmp_path / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    import app.services.device_identity as di

    devices = {}
    for name in ("device-AAA", "device-BBB"):
        key = ec.generate_private_key(ec.SECP256R1())
        pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
        token = di.enroll_device(pub_b64, name, "rc1-code")
        devices[name] = (key, pub_b64, token)
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "biz.db", "dev": devices,
        "H": {"X-Device-Token": devices["device-AAA"][2]},
        "B": {"X-Device-Token": devices["device-BBB"][2]},
        "client": TestClient(app),
    })()


def test_rc1_full_chain_with_inline_negatives(rc1_env):
    c = rc1_env.client
    key_a, pub_a, tok_a = rc1_env.dev["device-AAA"]
    log = []

    # ---- negative: unauthorized before anything else ---------------
    assert c.get("/api/v1/projects").status_code == 401
    log.append("unauthorized: 401 before any setup")

    # ---- 1. project (via sync batch, offline-queue style) ----------
    ops = [{"op": "project_create", "operation_id": "rc1-pc",
            "payload": {"operation_id": "rc1-pc",
                        "local_id": "loc-proj", "name": "RC1 Site"}}]
    r = c.post("/api/v1/sync/batch", json={"operations": ops},
               headers=rc1_env.H)
    assert r.status_code == 200
    assert r.json()["receipts"][0]["status"] == "applied"
    pid = r.json()["id_map"]["loc-proj"]
    log.append(f"project via sync: {pid[:8]}…")

    # ---- negative: cross-device isolation on the fresh project -----
    assert c.get(f"/api/v1/projects/{pid}",
                 headers=rc1_env.B).status_code == 404
    log.append("cross-device: 404 (no existence oracle)")

    # ---- 2. photo metadata + original bytes ------------------------
    data = b"rc1 photo bytes DESKTOP_SIMULATION"
    sha = hashlib.sha256(data).hexdigest()
    ops2 = [{"op": "photo_register", "operation_id": "rc1-pr",
             "payload": {"operation_id": "rc1-pr", "local_id": "loc-ph",
                         "server_project_id": pid,
                         "idempotency_key": "rc1-ik",
                         "original_sha256": sha,
                         "original_filename": "IMG.jpg",
                         "mime_type": "image/jpeg", "byte_size": len(data),
                         "captured_at": "2026-11-23T10:00:00Z",
                         "device_id": "iPhone15,3"}}]
    r2 = c.post("/api/v1/sync/batch", json={"operations": ops2},
                headers=rc1_env.H)
    photo_id = r2.json()["id_map"]["loc-ph"]
    assert r2.json()["receipts"][0]["status"] == "applied"

    up = c.put(f"/api/v1/photos/{photo_id}/original", content=data,
               headers={**rc1_env.H,
                        "Content-Type": "application/octet-stream"})
    assert up.status_code == 200 and up.json()["stored"] is True
    log.append(f"photo + original: stored {len(data)}B")

    # ---- negative: replay the whole sync queue (flap) --------------
    r1b = c.post("/api/v1/sync/batch", json={"operations": ops},
                 headers=rc1_env.H)
    r2b = c.post("/api/v1/sync/batch", json={"operations": ops2},
                 headers=rc1_env.H)
    assert r1b.json()["receipts"][0]["status"] == "duplicate"
    assert r2b.json()["receipts"][0]["status"] == "duplicate"
    log.append("replay: both batches duplicate")

    # ---- 3. evidence link + server-side verify ---------------------
    manifest = make_manifest(key_a, _encode_pub(key_a.public_key()),
                             photo_bytes=data)
    lk = c.post(f"/api/v1/photos/{photo_id}/evidence-links",
                json={"evidence_reference": manifest.record_id,
                      "evidence_type": "signature_manifest"},
                headers=rc1_env.H)
    assert lk.status_code == 201
    assert lk.json()["verification_status"] == "unverified"
    link_id = lk.json()["evidence_link_id"]

    from app.services.key_registry import get_registry
    get_registry().register(pub_a, "device-AAA")
    vr = c.post(
        f"/api/v1/photos/{photo_id}/evidence-links/{link_id}/verify",
        json={"manifest": manifest.model_dump(),
              "photo_base64": base64.b64encode(data).decode()},
        headers=rc1_env.H)
    assert vr.status_code == 200
    assert vr.json()["verification_status"] == "cryptographically_valid"
    log.append(f"evidence verified: cryptographically_valid")

    # ---- 4. sync pull (server state to the client) ------------------
    events = c.get("/api/v1/sync/events?limit=200",
                   headers=rc1_env.H).json()
    etypes = {e["entity_type"] for e in events["items"]}
    assert "project" in etypes and "photo" in etypes
    cursor = events["cursor"]
    log.append(f"sync pull: cursor={cursor}, types={sorted(etypes)}")

    # ---- 5. download + byte comparison ------------------------------
    dl = c.get(f"/api/v1/photos/{photo_id}/original",
               headers=rc1_env.H)
    assert dl.status_code == 200
    assert dl.content == data
    assert hashlib.sha256(dl.content).hexdigest() == sha
    log.append(f"download: {len(dl.content)}B byte-identical")

    # ---- 6. integrity re-check (storage + chain in one view) -------
    integ = c.get(f"/api/v1/photos/{photo_id}/original/integrity",
                  headers=rc1_env.H).json()
    assert integ["original"]["file_hash_valid"] is True
    assert integ["claims"]["file_integrity_verified"] is True
    assert integ["claims"]["photo_authenticity_proven"] is False
    assert integ["evidence"]["highest_status"] == "cryptographically_valid"
    log.append("integrity: bytes ok, authenticity NOT claimed (honest)")

    # ---- negative: device B sees NONE of this through sync ---------
    ev_b = c.get("/api/v1/sync/events?limit=200",
                 headers=rc1_env.B).json()
    assert ev_b["items"] == []
    log.append("cross-device sync pull: zero events")

    # ---- 7. service restart (fresh client, same DB) ------------------
    c2 = TestClient(app)
    assert c2.get("/api/v1/projects", headers=rc1_env.H).json()["total"] == 1
    integ2 = c2.get(
        f"/api/v1/photos/{photo_id}/original/integrity",
        headers=rc1_env.H).json()
    assert integ2["original"]["file_hash_valid"] is True
    ev2 = c2.get(f"/api/v1/sync/events?cursor={cursor}",
                 headers=rc1_env.H).json()
    assert all(e["seq"] > cursor for e in ev2["items"]) or ev2["items"] == []
    log.append("restart: identity/data/cursor consistent")

    # ---- negative: revocation kills everything for A ---------------
    c.post(f"/api/v1/devices/{tok_a}/revoke",
           json={"admin_code": "rc1-code"},
           headers={"X-Admin-Code": "rc1-code"})
    assert c2.get("/api/v1/projects",
                   headers=rc1_env.H).status_code == 401
    assert c2.get(f"/api/v1/photos/{photo_id}/original",
                  headers=rc1_env.H).status_code == 401
    log.append("revocation: A refused everywhere (401)")

    print("\n[RC1 CROSS-MODULE CHAIN - DESKTOP_SIMULATION]")
    for line in log:
        print(f"  {line}")


def test_rc2_v1_to_latest_sequential_migration(tmp_path, monkeypatch):
    """P2: v1 -> ... -> latest STEP BY STEP (migrate() applies ALL
    pending at once by design; stepwise verification executes one
    migration script per step to prove each level is self-contained).
    Updated in W011: 'latest' is now v6 (derivatives).
    Updated in B02-W025: migration NUMBERS may have GAPS - v9/v10 are
    reserved for B02-W014/B02-W017 branches (migrate() applies by
    sorted key, gaps are legal per the W017 delivery doc). The step
    walk skips reserved numbers; user_version still lands on every
    APPLIED level, matching migrate()'s own behaviour on a gapped
    dictionary."""
    import app.db as dbm
    import sqlite3
    db = tmp_path / "seq.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(dbm.MIGRATIONS[1])
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO mvp_projects (project_id, owner_token, name, "
        "description, site_address, status, created_at, updated_at) "
        "VALUES ('p1','tok','v1-data','','','active','t','t')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(db))
    latest = max(dbm.MIGRATIONS)
    for version in range(2, latest + 1):
        if version not in dbm.MIGRATIONS:
            continue        # reserved for a parallel branch; gap OK
        conn = sqlite3.connect(str(db))
        conn.execute("BEGIN IMMEDIATE")
        for stmt in dbm._split_statements(dbm.MIGRATIONS[version]):
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.execute("COMMIT")
        uv = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert uv == version, f"expected v{version}, got v{uv}"
    # v1 data survived the full walk
    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT name FROM mvp_projects WHERE project_id='p1'"
    ).fetchone() is not None
    conn.close()
    # replay is a no-op
    assert dbm.migrate(db) == 0


def test_rc3_backup_restore_full_stack(rc1_env, tmp_path, monkeypatch):
    """P2: DB (identity + business + sync) + originals joint backup,
    restore, and post-restore consistency through the API."""
    import app.db as dbm
    from app.services import original_store
    c = rc1_env.client
    ops = [{"op": "project_create", "operation_id": "rc3-pc",
            "payload": {"operation_id": "rc3-pc", "local_id": "lp",
                        "name": "Backup Site"}}]
    pid = c.post("/api/v1/sync/batch", json={"operations": ops},
                 headers=rc1_env.H).json()["id_map"]["lp"]
    data = b"rc3 bytes"
    sha = hashlib.sha256(data).hexdigest()
    photo_id = c.post(
        f"/api/v1/projects/{pid}/photos",
        json={"idempotency_key": "rc3-ik", "original_sha256": sha,
              "original_filename": "f.jpg", "mime_type": "image/jpeg",
              "byte_size": len(data), "captured_at": "t",
              "device_id": "d"}, headers=rc1_env.H).json()["photo_id"]
    c.put(f"/api/v1/photos/{photo_id}/original", content=data,
          headers={**rc1_env.H,
                   "Content-Type": "application/octet-stream"})

    # joint backup
    bdir = tmp_path / "rc3-backup"
    db_backup = tmp_path / "rc3.db"
    dbm.backup_to(db_backup, rc1_env.db)
    manifest = original_store.backup(bdir)
    assert len(manifest["objects"]) == 1

    # simulate catastrophic loss
    original_store.object_path(sha).unlink()

    # restore DB + originals
    restored_db = tmp_path / "rc3-restored.db"
    dbm.restore_from(db_backup, restored_db)
    res = original_store.restore(bdir, manifest)
    assert res["objects"] == 1

    # point the app at the restored DB and verify everything
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(restored_db))
    c2 = TestClient(app)
    body = c2.get("/api/v1/projects", headers=rc1_env.H).json()
    assert body["total"] == 1 and body["items"][0]["name"] == "Backup Site"
    from app.services.device_identity import resolve_device
    tok_a = rc1_env.dev["device-AAA"][2]
    assert resolve_device(tok_a) == "device-AAA"     # identity survived
    dl = c2.get(f"/api/v1/photos/{photo_id}/original",
                headers=rc1_env.H)
    assert dl.content == data                         # bytes restored
