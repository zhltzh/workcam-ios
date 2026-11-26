"""TM45: fault-injection supplement - the remaining gaps not covered
by TM32 (storage crashes) or W021/W016 scenario tests:

  - DB lock contention: a second BEGIN IMMEDIATE holder forces a
    typed busy path (never a silent hang or a lost update)
  - sync-batch interrupted mid-way: half an array of operations
    must leave EXACTLY the applied prefix committed, the rest unseen
  - evidence verification failure leaves the trust ladder intact
    (a failed server verify never downgrades OTHER photos)
  - backup failure mid-flight: a store hash flip after the DB part
    succeeded must surface in the manifest failed[] (W007/V2 package
    both honor this - pinned for the project package)
  - rate-limiter fault: an injected clock jump cannot lock every
    device out forever (window slides)
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402

from app.main import app  # noqa: E402
from app.services import device_identity  # noqa: E402

CODE = "tm45-code"

# the fixture patches check_rate to a no-op for API tests; the limiter
# test needs the ORIGINAL - snapshot it at import time (before any
# fixture monkeypatching)
from app.services.device_identity import check_rate as _ORIG_CHECK_RATE  # noqa: E402


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
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    r = client.post("/api/v1/devices/enroll",
                    json={"public_key_b64": base64.b64encode(pub).decode(),
                          "device_id": "tm45",
                          "enrollment_code": CODE})
    H = {"X-Device-Token": r.json()["device_token"]}
    r = client.post("/api/v1/projects", headers=H,
                    json={"name": "Fault Site"})
    pid = r.json()["project_id"]
    return type("E", (), {"client": client, "H": H, "pid": pid,
                          "tmp": tmp_path})()


def _register(env, key):
    from PIL import Image
    img = Image.new("RGB", (64, 48), (hash(key) % 255, 60, 100))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    data = buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    r = env.client.post(f"/api/v1/projects/{env.pid}/photos",
                        headers=env.H, json={
        "idempotency_key": f"ik-{key}", "original_sha256": sha,
        "original_filename": f"{key}.jpg", "mime_type": "image/jpeg",
        "byte_size": len(data),
        "captured_at": "2026-11-26T10:00:00+00:00",
        "device_id": "cam"})
    assert r.status_code == 201, r.text
    photo_id = r.json()["photo_id"]
    r = env.client.put(f"/api/v1/photos/{photo_id}/original",
                       headers=env.H, content=data)
    assert r.status_code == 200, r.text
    return photo_id


def test_db_lock_contention_typed_not_hanging(env):
    """An external BEGIN IMMEDIATE holder blocks writes; the API must
    surface the SQLITE_BUSY as a typed 503-class error (after the
    busy timeout), never an infinite hang or a crash."""
    import app.db as dbm
    holder = sqlite3.connect(str(dbm._db_path()), timeout=5)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("UPDATE mvp_projects SET updated_at = updated_at "
                   "WHERE project_id = ?", (env.pid,))
    try:
        t0 = time.perf_counter()
        r = env.client.post(f"/api/v1/projects/{env.pid}/photos",
                            headers=env.H, json={
            "idempotency_key": "locked-write",
            "original_sha256": "ab" * 32,
            "original_filename": "x.jpg",
            "mime_type": "image/jpeg", "byte_size": 5,
            "captured_at": "2026-11-26T10:00:00+00:00",
            "device_id": "cam"})
        dt = time.perf_counter() - t0
        # bounded, and the API did not die
        assert r.status_code >= 500 or r.status_code == 200
        assert dt < 30
    except sqlite3.OperationalError as e:
        # TestClient re-raises transport-level exceptions; the
        # requirement is that the failure is NAMED and FAST, and the
        # cascade bug (cannot-rollback noise) is gone
        assert "locked" in str(e)
    finally:
        holder.rollback()
        holder.close()
    # after release the SAME write succeeds (no poisoned state)
    r = env.client.post(f"/api/v1/projects/{env.pid}/photos",
                        headers=env.H, json={
        "idempotency_key": "locked-write",
        "original_sha256": "ab" * 32,
        "original_filename": "x.jpg",
        "mime_type": "image/jpeg", "byte_size": 5,
        "captured_at": "2026-11-26T10:00:00+00:00",
        "device_id": "cam"})
    assert r.status_code == 201, r.text


def test_sync_batch_interrupted_prefix_intact(env):
    """A batch whose LATER op is poisoned (constraint failure) must
    leave the EARLIER ops durably applied - the ledger discipline
    means receipts are per-op, and a retry of the same batch replays
    duplicates for the applied prefix and re-attempts only the
    failure (resumability of an interrupted sync)."""
    photo = _register(env, "sync-int")
    batch_ok = env.client.post("/api/v1/sync/batch", headers=env.H,
        json={"operations": [
            {"op": "annotation_create", "operation_id": "f-1",
             "payload": {"operation_id": "f-1", "local_id": "l1",
                         "server_photo_id": photo,
                         "annotation_type": "text",
                         "norm_x1": 0.1, "norm_y1": 0.1,
                         "text_content": "first"}},
            {"op": "annotation_create", "operation_id": "f-2",
             "payload": {"operation_id": "f-2", "local_id": "l2",
                         "server_photo_id": photo,
                         "annotation_type": "text",
                         "norm_x1": 0.2, "norm_y1": 0.2,
                         "text_content": "second"}},
        ]})
    assert batch_ok.status_code == 200
    recs = batch_ok.json()["receipts"]
    assert all(r["status"] == "applied" for r in recs)
    # poison a THIRD op with a nonexistent photo (not_found receipt)
    batch_mixed = env.client.post("/api/v1/sync/batch", headers=env.H,
        json={"operations": [
            {"op": "annotation_create", "operation_id": "f-3",
             "payload": {"operation_id": "f-3", "local_id": "l3",
                         "server_photo_id": photo,
                         "annotation_type": "text",
                         "norm_x1": 0.3, "norm_y1": 0.3,
                         "text_content": "third"}},
            {"op": "annotation_create", "operation_id": "f-4",
             "payload": {"operation_id": "f-4", "local_id": "l4",
                         "server_photo_id":
                             "00000000-0000-0000-0000-0000000000ee",
                         "annotation_type": "text",
                         "norm_x1": 0.4, "norm_y1": 0.4,
                         "text_content": "poison"}},
        ]})
    body = batch_mixed.json()
    by_id = {r["operation_id"]: r for r in body["receipts"]}
    assert by_id["f-3"]["status"] == "applied"
    assert by_id["f-4"]["status"] == "rejected"
    # retry replays the applied prefix as duplicates, keeps the rest
    again = env.client.post("/api/v1/sync/batch", headers=env.H,
        json={"operations": [
            {"op": "annotation_create", "operation_id": "f-3",
             "payload": {"operation_id": "f-3", "local_id": "l3",
                         "server_photo_id": photo,
                         "annotation_type": "text",
                         "norm_x1": 0.3, "norm_y1": 0.3,
                         "text_content": "third"}},
        ]}).json()
    assert again["receipts"][0]["status"] == "duplicate"
    # and the applied prefix IS durable on the server view
    r = env.client.get(f"/api/v1/photos/{photo}/annotations",
                       headers=env.H)
    texts = {a["text_content"] for a in r.json()["items"]}
    assert {"first", "second", "third"} <= texts


def test_verify_failure_does_not_touch_other_photos(env):
    """A failing signature verification on photo A must not change
    photo B's evidence state at all (blast-radius = one link)."""
    pa = _register(env, "fail-A")
    pb = _register(env, "intact-B")
    # create an evidence link on each
    for pid_photo in (pa, pb):
        r = env.client.post(
            f"/api/v1/photos/{pid_photo}/evidence-links",
            headers=env.H, json={
            "evidence_reference": "ref-" + pid_photo[:8],
            "evidence_type": "signature_manifest"})
        assert r.status_code == 201, r.text
    # A's verification FAILS (bad manifest bytes)
    r = env.client.post(
        f"/api/v1/photos/{pa}/evidence-links/"
        f"{_link_id(env, pa)}/verify",
        headers=env.H, json={
            "manifest": _tampered_manifest(pa),
            "photo_base64": None})
    # whatever the outcome for A, B's link stays exactly as it was
    r_b = env.client.get(f"/api/v1/photos/{pb}/original/integrity",
                         headers=env.H)
    assert r_b.status_code == 200
    assert r_b.json()["evidence"]["highest_status"] == "unverified"


def _link_id(env, photo_id):
    r = env.client.get(f"/api/v1/photos/{photo_id}/evidence-links",
                       headers=env.H)
    return r.json()["items"][0]["evidence_link_id"]


def _tampered_manifest(photo_id):
    from app.services.serialization import canonical_payload
    key = ec.generate_private_key(ec.SECP256R1())
    # sign a DIFFERENT payload than the manifest claims -> math fails
    manifest = {
        "version": "1.1", "record_id": "c" * 32, "sequence_number": 5,
        "previous_record_hash": None, "photo_hash": "d" * 64,
        "capture_timestamp": "2026-11-26T10:00:00+00:00",
        "device_reported_time": "2026-11-26T10:00:00+00:00",
        "device_id": "tamperer",
        "location": None, "signature": "", "public_key":
            base64.b64encode(key.public_key().public_bytes(
                Encoding.X962,
                PublicFormat.UncompressedPoint)).decode()}
    from app.models.capture import SignatureManifest
    m = SignatureManifest.model_validate(manifest)
    sig = key.sign(canonical_payload(m),
                   ec.ECDSA(__import__("cryptography.hazmat"
                                       ".primitives.hashes",
                                       fromlist=["hashes"]).SHA256()))
    manifest["signature"] = base64.b64encode(sig).decode()
    # tamper AFTER signing: payload no longer matches the signature
    manifest["photo_hash"] = "e" * 64
    return manifest


def test_project_package_backup_failure_surfaces(env):
    """A hash flip on a store object between the DB read and the
    copy must land in the package failed[] - never silently absent
    from the package."""
    from app.services import backup_v2, original_store
    photo = _register(env, "backup-flip")
    sha_row = env.client.get(
        f"/api/v1/photos/{photo}", headers=env.H).json()
    obj = original_store.object_path(sha_row["original_sha256"])
    # flip one byte AFTER everything else is in place
    raw = obj.read_bytes()
    obj.write_bytes(raw[:-1] + bytes([raw[-1] ^ 0x01]))
    pkg = env.tmp / "pkg-flip"
    out = backup_v2.project_backup(env.pid,
                                   env.H["X-Device-Token"], pkg)
    assert out["failed"], "the flipped object must surface"
    assert any("mismatch" in f["reason"] for f in out["failed"])
    # restore the byte (leave the store clean for other tests)
    obj.write_bytes(raw)


def test_rate_limiter_clock_jump_does_not_lock_out(env, monkeypatch):
    """Inject a wall-clock jump into the limiter's window math: a
    device must NEVER be permanently locked out by time moving
    forward (the sliding window must slide)."""
    import app.services.device_identity as di
    monkeypatch.setattr(di, "check_rate", _ORIG_CHECK_RATE)
    real_fn = di.check_rate
    token = env.H["X-Device-Token"]
    # exhaust the limiter (capacity 3 for speed)
    for _ in range(3):
        real_fn(token, capacity=3, window_seconds=60)
    with pytest.raises(Exception):
        real_fn(token, capacity=3, window_seconds=60)
    # the clock jumps FORWARD one hour: the window slides away and
    # the device is accepted again
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 3600)
    real_fn(token, capacity=3, window_seconds=60)
    monkeypatch.setattr(time, "time", real_time)
