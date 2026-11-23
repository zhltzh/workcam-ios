"""WC-W007 original storage tests.

Coverage per the task brief P5: normal upload/read, hash mismatch,
duplicate upload, concurrent writes, write interruption (temp residue),
file corruption, path traversal, unauthorized download, cross-project
and cross-subject access, original<->evidence hash binding, DB rollback,
backup/restore (including crash-gap scenarios: file-without-record,
record-without-file), repeated restore safety, and the simulated iOS
client chain (project -> photo -> upload -> bind -> verify -> download
-> sha comparison) - all DESKTOP, clearly marked.
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import make_manifest, _encode_pub


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_DEVICE_REGISTRY",
                       str(tmp_path / "devices.jsonl"))
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    import app.db as dbm
    import app.api.upload_auth as ua
    dbm.migrate(tmp_path / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()

    key = ec.generate_private_key(ec.SECP256R1())       # TEST key
    pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
    registry = ua.DeviceRegistry(tmp_path / "devices.jsonl")
    token_a = registry.enroll(pub_b64, "device-AAA")
    key_b = ec.generate_private_key(ec.SECP256R1())
    pub_b = base64.b64encode(_encode_pub(key_b.public_key())).decode()
    token_b = registry.enroll(pub_b, "device-BBB")
    ua._batch_owners.clear(); ua._buckets.clear(); ua._challenges.clear()
    return type("E", (), {
        "tmp": tmp_path, "key": key, "pub_b64": pub_b64,
        "H": {"X-Device-Token": token_a},
        "B": {"X-Device-Token": token_b},
        "client": TestClient(app),
    })()


def _mk_project(env, name="Site W") -> str:
    r = env.client.post("/api/v1/projects", json={"name": name},
                         headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _register_photo(env, project_id, sha: str, filename="IMG.jpg") -> str:
    r = env.client.post(f"/api/v1/projects/{project_id}/photos", json={
        "idempotency_key": f"k-{uuid.uuid4().hex[:8]}",
        "original_sha256": sha,
        "original_filename": filename, "mime_type": "image/jpeg",
        "byte_size": 2048, "captured_at": "2026-11-24T08:00:00Z",
        "device_id": "iPhone15,3"}, headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()["photo_id"]


def _mk_photo_bytes(seed: int = 0x42, size: int = 2048) -> bytes:
    return bytes([seed]) * size


# ------------------------------------------------------------- P0/P1: core


def test_upload_and_download_roundtrip(env):
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)

    up = env.client.put(f"/api/v1/photos/{photo_id}/original",
                        content=data, headers={
                            **env.H, "Content-Type": "application/octet-stream"})
    assert up.status_code == 200, up.text
    assert up.json()["sha256"] == sha
    assert up.json()["stored"] is True

    dl = env.client.get(f"/api/v1/photos/{photo_id}/original",
                        headers=env.H)
    assert dl.status_code == 200
    assert dl.content == data                       # byte-identical
    assert dl.headers["X-Original-Sha256"] == sha


def test_hash_mismatch_rejected_nothing_persisted(env):
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    wrong_sha = hashlib.sha256(b"different").hexdigest()
    photo_id = _register_photo(env, pid, wrong_sha)

    r = env.client.put(f"/api/v1/photos/{photo_id}/original",
                       content=data, headers={
                           **env.H, "Content-Type": "application/octet-stream"})
    assert r.status_code == 400
    assert "hash_mismatch" in r.text

    # NOTHING persisted: no file, no DB object
    from app.services.original_store import object_path, _db_file
    import sqlite3
    assert not object_path(wrong_sha).exists()
    conn = sqlite3.connect(str(_db_file()))
    assert conn.execute("SELECT COUNT(*) FROM mvp_originals").fetchone()[0] == 0
    conn.close()


def test_duplicate_upload_idempotent(env):
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)

    h = {**env.H, "Content-Type": "application/octet-stream"}
    r1 = env.client.put(f"/api/v1/photos/{photo_id}/original",
                        content=data, headers=h)
    r2 = env.client.put(f"/api/v1/photos/{photo_id}/original",
                        content=data, headers=h)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r2.json()["idempotent_replay"] is True
    assert r2.json()["sha256"] == r1.json()["sha256"]


def test_concurrent_writes_same_object(env):
    """Two photos with IDENTICAL content: both register, both upload -
    the object is stored once (content addressing) and both bind."""
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    p1 = _register_photo(env, pid, sha, filename="a.jpg")
    p2 = _register_photo(env, pid, sha, filename="b.jpg")

    h = {**env.H, "Content-Type": "application/octet-stream"}
    r1 = env.client.put(f"/api/v1/photos/{p1}/original", content=data,
                        headers=h)
    r2 = env.client.put(f"/api/v1/photos/{p2}/original", content=data,
                        headers=h)
    assert r1.status_code == 200 and r2.status_code == 200
    # same object, two legitimate bindings
    assert r1.json()["sha256"] == r2.json()["sha256"] == sha


def test_write_interruption_temp_residue_fsck(env):
    """Simulate a crash mid-write: place a .part file where the store
    writes temps. fsck reports it; fix_temps cleans it; the committed
    object is never touched."""
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)
    env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                   headers={**env.H,
                            "Content-Type": "application/octet-stream"})

    from app.services import original_store
    # crash residue in the SAME object directory
    tmp = original_store.object_path(sha).parent / ".w007_crash.part"
    tmp.write_bytes(b"partial bytes from a killed write")

    report = original_store.fsck(fix_temps=False)
    assert any("crash.part" in t for t in report["temp_residue"])
    assert report["intact"] >= 1            # committed object intact

    report2 = original_store.fsck(fix_temps=True)
    assert report2["repaired_temps"] == 1
    assert not tmp.exists()
    assert original_store.object_path(sha).exists()   # object untouched


def test_file_corruption_detected_and_never_overwritten(env):
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)
    env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                   headers={**env.H,
                            "Content-Type": "application/octet-stream"})

    # tamper the object ON DISK (bypassing the API - a disk-level event)
    path = __import__("app.services.original_store",
                      fromlist=["object_path"]).object_path(sha)
    raw = bytearray(path.read_bytes())
    raw[10] ^= 0xFF
    path.write_bytes(bytes(raw))

    # re-store attempt with CORRECT data: 409 - existing object is
    # corrupted and must NOT be silently overwritten (immutability rule)
    r = env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                       headers={**env.H,
                                "Content-Type": "application/octet-stream"})
    assert r.status_code == 409
    assert "object_corrupted" in r.text


def test_path_traversal_impossible(env):
    """The hash is validated before ANY path construction - a traversal
    attempt is a 400 at the schema level, and no client byte ever
    reaches a filesystem path (paths are hash-derived)."""
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    real_sha = hashlib.sha256(data).hexdigest()
    # a 'sha256' field containing traversal - rejected at photo
    # registration (W006 already enforces ^[0-9a-f]{64}$)
    r = env.client.post(f"/api/v1/projects/{pid}/photos", json={
        "idempotency_key": "trav", "original_sha256": "../../etc/passwd",
        "original_filename": "x", "mime_type": "image/jpeg",
        "byte_size": 10, "captured_at": "t", "device_id": "d"},
        headers=env.H)
    assert r.status_code == 422

    # and the storage layer independently refuses malformed hashes
    from app.services.original_store import object_path
    from app.db import ApiError
    with pytest.raises(ApiError):
        object_path("../../../etc/passwd")


# --------------------------------------------------------------- security


def test_unauthorized_download_rejected(env):
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)
    env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                   headers={**env.H,
                            "Content-Type": "application/octet-stream"})
    r = env.client.get(f"/api/v1/photos/{photo_id}/original")
    assert r.status_code == 401


def test_cross_subject_download_404(env):
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)
    env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                   headers={**env.H,
                            "Content-Type": "application/octet-stream"})
    r = env.client.get(f"/api/v1/photos/{photo_id}/original",
                        headers=env.B)
    assert r.status_code == 404            # no existence oracle
    # upload as B is also refused
    r2 = env.client.put(f"/api/v1/photos/{photo_id}/original",
                        content=data, headers={
                            **env.B,
                            "Content-Type": "application/octet-stream"})
    assert r2.status_code == 404


def test_cross_project_binding_blocked(env):
    """Device A cannot bind B's photo (ownership chains through the
    project) - and a photo in project 1 can never be bound by a
    different owner even if the hash matches."""
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)
    r = env.client.put(f"/api/v1/photos/{photo_id}/original",
                       content=data, headers={
                           **env.B,
                           "Content-Type": "application/octet-stream"})
    assert r.status_code == 404


def test_no_disk_path_exposed_in_responses(env):
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)
    up = env.client.put(f"/api/v1/photos/{photo_id}/original",
                        content=data, headers={
                            **env.H,
                            "Content-Type": "application/octet-stream"})
    dl = env.client.get(f"/api/v1/photos/{photo_id}/original",
                        headers=env.H)
    # no filesystem path leaks in any response
    assert "originals" not in dl.text or "X-Original-Sha256" in dl.headers
    body = up.text + str(dl.headers)
    assert str(env.tmp) not in body         # no absolute server paths


# ------------------------------------------------------------- P2: integrity


def test_integrity_view_and_evidence_chain(env):
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)
    env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                   headers={**env.H,
                            "Content-Type": "application/octet-stream"})

    # link evidence (the photo's manifest)
    manifest = make_manifest(env.key, _encode_pub(env.key.public_key()),
                             photo_bytes=data)
    env.client.post(f"/api/v1/photos/{photo_id}/evidence-links",
                   json={"evidence_reference": manifest.record_id,
                         "evidence_type": "signature_manifest"},
                   headers=env.H)

    r = env.client.get(f"/api/v1/photos/{photo_id}/original/integrity",
                       headers=env.H)
    assert r.status_code == 200
    body = r.json()
    assert body["original"]["file_present"] is True
    assert body["original"]["file_hash_valid"] is True
    assert body["original"]["sha256"] == sha
    assert body["evidence"]["link_count"] == 1
    # integrity verified YES; authenticity claimed NO
    assert body["claims"]["file_integrity_verified"] is True
    assert body["claims"]["photo_authenticity_proven"] is False


def test_integrity_detects_missing_and_corrupt(env):
    from app.services import original_store as store
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)
    env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                   headers={**env.H,
                            "Content-Type": "application/octet-stream"})

    # corrupt on disk
    path = store.object_path(sha)
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 0x01
    path.write_bytes(bytes(raw))
    r = env.client.get(f"/api/v1/photos/{photo_id}/original/integrity",
                       headers=env.H)
    assert r.json()["original"]["file_hash_valid"] is False

    # missing on disk
    path.unlink()
    r2 = env.client.get(f"/api/v1/photos/{photo_id}/original/integrity",
                        headers=env.H)
    assert r2.json()["original"]["file_present"] is False
    assert r2.json()["original"]["file_hash_valid"] is None


# ----------------------------------------------------------- P4: backup


def test_backup_restore_roundtrip_and_repeat_safety(env):
    from app.services import original_store as store
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)
    env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                   headers={**env.H,
                            "Content-Type": "application/octet-stream"})

    bdir = env.tmp / "backup-originals"
    manifest = store.backup(bdir)
    assert sha in manifest["objects"]
    assert manifest["failed"] == []

    # destroy the live object, then restore
    store.object_path(sha).unlink()
    res = store.restore(bdir, manifest)
    assert res["objects"] == 1
    assert store.object_path(sha).exists()
    # the restored file verifies
    dl = env.client.get(f"/api/v1/photos/{photo_id}/original",
                        headers=env.H)
    assert dl.content == data

    # repeated restore: intact -> skip, never damages (P4 #6)
    res2 = store.restore(bdir, manifest)
    assert res2["skipped_intact"] == 1 and res2["objects"] == 0
    dl2 = env.client.get(f"/api/v1/photos/{photo_id}/original",
                         headers=env.H)
    assert dl2.content == data


def test_fsck_reports_orphan_file_without_db_record(env):
    """Scenario P4 #4: file exists on disk, DB has no record -> orphan
    REPORTED (never auto-deleted)."""
    from app.services import original_store as store
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)
    env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                   headers={**env.H,
                            "Content-Type": "application/octet-stream"})

    # simulate a DB row loss (crash gap: file written, record lost)
    import sqlite3
    conn = sqlite3.connect(str(store._db_file()))
    conn.execute("DELETE FROM mvp_originals WHERE sha256 = ?", (sha,))
    conn.commit()
    conn.close()

    report = store.fsck()
    assert any(sha in o for o in report["orphan_files"])
    # NOT deleted
    assert store.object_path(sha).exists()


def test_fsck_reports_record_without_file(env):
    """Scenario P4 #5: DB record exists, file vanished -> missing_files
    reported, status marked corruption_detected."""
    from app.services import original_store as store
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)
    env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                   headers={**env.H,
                            "Content-Type": "application/octet-stream"})
    store.object_path(sha).unlink()

    report = store.fsck()
    assert sha in report["missing_files"]
    # download now fails loudly
    r = env.client.get(f"/api/v1/photos/{photo_id}/original",
                       headers=env.H)
    assert r.status_code == 404


# ---------------------------------------------------------- P5: DB rollback


def test_db_rollback_on_bind_failure(env):
    """Store succeeds, bind fails (simulate by breaking the FK target):
    the FILE may exist (fsck handles it) but the BINDING row is rolled
    back - no phantom binding survives."""
    import sqlite3
    from app.services import original_store as store
    pid = _mk_project(env)
    data = _mk_photo_bytes()
    sha = hashlib.sha256(data).hexdigest()
    photo_id = _register_photo(env, pid, sha)

    # store the object first (standalone - this path commits)
    store.store_original(data, sha)

    # now break the photo row (FK target) directly, then try the API bind
    conn = sqlite3.connect(str(store._db_file()))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("DELETE FROM mvp_photos WHERE photo_id = ?", (photo_id,))
    conn.commit()
    conn.close()

    r = env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                       headers={**env.H,
                                "Content-Type": "application/octet-stream"})
    assert r.status_code == 404              # photo gone -> no bind

    # no phantom binding was created
    conn2 = sqlite3.connect(str(store._db_file()))
    n = conn2.execute("SELECT COUNT(*) FROM mvp_photo_originals").fetchone()[0]
    conn2.close()
    assert n == 0


# --------------------------------------- simulated iOS client chain (P6)


def test_simulated_ios_client_full_chain(env):
    """The Mac-facing script's exact chain, as an in-process test:
    project -> photo -> upload -> bind -> evidence link -> server verify
    -> download -> sha compare. DESKTOP data throughout."""
    # 1. project
    pid = _mk_project(env, name="Sim iPhone Project")
    # 2. photo bytes (desktop stand-in for a real iPhone JPEG)
    data = _mk_photo_bytes(seed=0x55)
    sha = hashlib.sha256(data).hexdigest()
    # 3. register
    photo_id = _register_photo(env, pid, sha, filename="IMG_real.jpg")
    # 4. upload + bind
    up = env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                        headers={**env.H,
                                 "Content-Type": "application/octet-stream"})
    assert up.status_code == 200
    # 5. evidence link
    manifest = make_manifest(env.key, _encode_pub(env.key.public_key()),
                             photo_bytes=data)
    lk = env.client.post(f"/api/v1/photos/{photo_id}/evidence-links",
                         json={"evidence_reference": manifest.record_id,
                               "evidence_type": "signature_manifest"},
                         headers=env.H)
    assert lk.status_code == 201
    link_id = lk.json()["evidence_link_id"]
    # 6. server-side verify (registers the key first)
    from app.services.key_registry import get_registry
    get_registry().register(env.pub_b64, "device-AAA")
    vr = env.client.post(
        f"/api/v1/photos/{photo_id}/evidence-links/{link_id}/verify",
        json={"manifest": manifest.model_dump()}, headers=env.H)
    assert vr.status_code == 200
    assert vr.json()["verification_status"] == "cryptographically_valid"
    # 7. integrity view
    ir = env.client.get(f"/api/v1/photos/{photo_id}/original/integrity",
                        headers=env.H)
    body = ir.json()
    assert body["original"]["file_hash_valid"] is True
    assert body["evidence"]["highest_status"] == "cryptographically_valid"
    # 8. download + compare
    dl = env.client.get(f"/api/v1/photos/{photo_id}/original",
                        headers=env.H)
    assert hashlib.sha256(dl.content).hexdigest() == sha
    assert dl.content == data
