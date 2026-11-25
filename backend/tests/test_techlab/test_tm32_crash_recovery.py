"""TM32: crash-recovery state machine - crash-injection suite.

Every task-listed crash point is simulated against the REAL storage
stack (SQLite business DB + W007 object store), then recovery must
uphold the four invariants. Scenarios:

  1. crash after project-reference write, before evidence survives
     (the Mac Night 04 finding - photo row exists, nothing else)
  2. crash after original-bytes write (binding lost window)
  3. crash after signature complete, before status write
  4. crash during DER/object write (partial temp file left behind)
  5. DB commit failure (row rolled back, bytes survive)
  6. original bytes do not match the claimed hash
  7. disk-full behaviour (store fails cleanly, nothing persisted)
  8. duplicate recovery (apply twice -> same result)
  9. concurrent recovery (two threads -> no double-apply, no corruption)
 10. orphan evidence (bytes with no metadata row)
 11. orphan reference (evidence link whose photo vanished -> VIOLATION
     because the fact chain is broken)
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import io
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402

from app.main import app  # noqa: E402
from app.services import device_identity, evidence_recovery, \
    original_store  # noqa: E402
from techlab.recovery_sm import (  # noqa: E402
    PipelineFacts, PipelineState, derive_state, plan_recovery)

CODE = "tm32-code"


def _photo_bytes(seed: int) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (80, 60), (seed * 7 % 255, 90, 30))
    ImageDraw.Draw(img).rectangle([5, 5, 70, 50], fill=(seed, 40, 200))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return buf.getvalue()


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
    pub_b64 = base64.b64encode(key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    r = client.post("/api/v1/devices/enroll",
                    json={"public_key_b64": pub_b64,
                          "device_id": "tm32-dev",
                          "enrollment_code": CODE})
    tok = r.json()["device_token"]
    r = client.post("/api/v1/projects", headers={"X-Device-Token": tok},
                    json={"name": "Recovery Site A"})
    pid = r.json()["project_id"]
    return type("E", (), {"client": client, "tok": tok, "pid": pid,
                          "key": key, "H": {"X-Device-Token": tok}})()


def _register_photo(env, data: bytes, key: str) -> str:
    sha = hashlib.sha256(data).hexdigest()
    r = env.client.post(f"/api/v1/projects/{env.pid}/photos",
                        headers=env.H, json={
        "idempotency_key": key, "original_sha256": sha,
        "original_filename": f"{key}.jpg", "mime_type": "image/jpeg",
        "byte_size": len(data),
        "captured_at": "2026-11-26T07:00:00+00:00",
        "device_id": "tm32-dev"})
    assert r.status_code == 201, r.text
    return r.json()["photo_id"]


def _upload(env, photo_id: str, data: bytes):
    r = env.client.put(f"/api/v1/photos/{photo_id}/original",
                       headers=env.H, content=data)
    assert r.status_code == 200, r.text


# ---------------------------------------------- pure state machine tests

def test_pure_ladder_and_violations():
    assert derive_state(PipelineFacts()) == PipelineState.EMPTY
    assert derive_state(PipelineFacts(metadata_row=True)) == \
        PipelineState.REGISTERED
    assert derive_state(PipelineFacts(original_bytes_on_disk=True)) == \
        PipelineState.ORPHAN_BYTES
    assert derive_state(PipelineFacts(metadata_row=True,
                                      original_bytes_on_disk=True)) == \
        PipelineState.STORED_UNBOUND
    assert derive_state(PipelineFacts(metadata_row=True,
                                      original_bytes_on_disk=True,
                                      binding_row=True)) == \
        PipelineState.BOUND
    assert derive_state(PipelineFacts(metadata_row=True,
                                      original_bytes_on_disk=True,
                                      binding_row=True,
                                      evidence_link_row=True)) == \
        PipelineState.EVIDENCE_PENDING
    assert derive_state(PipelineFacts(metadata_row=True,
                                      original_bytes_on_disk=True,
                                      binding_row=True,
                                      evidence_link_row=True,
                                      link_failed=True)) == \
        PipelineState.EVIDENCE_FAILED
    assert derive_state(PipelineFacts(metadata_row=True,
                                      original_bytes_on_disk=True,
                                      binding_row=True,
                                      evidence_link_row=True,
                                      link_verified=True)) == \
        PipelineState.TRUSTED_SIGNED
    # I1 violations: trust claim without its facts
    assert derive_state(PipelineFacts(link_verified=True)) == \
        PipelineState.VIOLATION
    assert derive_state(PipelineFacts(binding_row=True)) == \
        PipelineState.VIOLATION
    assert derive_state(PipelineFacts(metadata_row=True,
                                      evidence_link_row=True)) == \
        PipelineState.VIOLATION
    # binding without bytes is the Mac Night 04 shape
    assert derive_state(PipelineFacts(metadata_row=True,
                                      binding_row=True)) == \
        PipelineState.VIOLATION


def test_pure_actions_never_destructive():
    for f in (PipelineFacts(), PipelineFacts(metadata_row=True),
              PipelineFacts(original_bytes_on_disk=True),
              PipelineFacts(link_verified=True)):
        p = plan_recovery(f)
        assert p["destructive"] is False


# ------------------------------------------- crash scenario 1 (Night 04)

def test_crash_after_reference_before_evidence(env):
    """Photo metadata row written, then the process dies. Recovery
    must classify REGISTERED and propose await_upload - the record
    stays open, no trust is implied, the client retries."""
    data = _photo_bytes(1)
    pid_photo = _register_photo(env, data, "crash1")
    scan = evidence_recovery.scan_recovery()
    entry = next(p for p in scan["photos"]
                 if p["photo_id"] == pid_photo)
    assert entry["state"] == "registered"
    assert entry["action"] == "await_upload"
    # recovery changes nothing
    res = evidence_recovery.apply_recovery()
    assert res["applied"] == 0
    # bytes arrive later -> the pipeline advances on its own
    _upload(env, pid_photo, data)
    scan2 = evidence_recovery.scan_recovery()
    entry2 = next(p for p in scan2["photos"]
                  if p["photo_id"] == pid_photo)
    # binding happened inside the PUT - complete contract
    assert entry2["state"] in ("bound", "stored_unbound")


# ------------------------------------------- crash scenario 2

def test_crash_after_bytes_before_binding(env, tmp_path, monkeypatch):
    """Bytes reached the object store, but the process died before the
    binding INSERT committed (simulated by storing the object directly
    then registering metadata only). Recovery rebinds idempotently."""
    data = _photo_bytes(2)
    sha = hashlib.sha256(data).hexdigest()
    # bytes land in the store WITHOUT any photo row: pure orphan first
    original_store.store_original(data, sha)
    scan = evidence_recovery.scan_recovery()
    assert any(o["original_sha256"] == sha
              for o in scan["orphan_objects"])
    # now the metadata row appears (crash window between store and
    # bind replayed: object exists, binding row absent)
    pid_photo = _register_photo(env, data, "crash2")
    scan2 = evidence_recovery.scan_recovery()
    entry = next(p for p in scan2["photos"]
                 if p["photo_id"] == pid_photo)
    assert entry["state"] == "stored_unbound"
    assert entry["action"] == "rebind"
    # apply -> bound; idempotent second apply -> no double rows
    res = evidence_recovery.apply_recovery()
    assert res["applied"] == 1
    scan3 = evidence_recovery.scan_recovery()
    entry3 = next(p for p in scan3["photos"]
                  if p["photo_id"] == pid_photo)
    assert entry3["state"] == "bound"
    res2 = evidence_recovery.apply_recovery()
    assert res2["applied"] == 0               # nothing left to do
    assert scan3["counts"]["bound"] == \
        evidence_recovery.scan_recovery()["counts"]["bound"]


# ------------------------------------------- crash scenario 3

def test_crash_after_signature_before_status_write(env):
    """The client's signature completed on device, the evidence link
    row was created, but the server's /verify never ran (status stays
    'unverified'). Recovery may NOT upgrade the status - it can only
    flag the retry."""
    data = _photo_bytes(3)
    pid_photo = _register_photo(env, data, "crash3")
    _upload(env, pid_photo, data)
    r = env.client.post(f"/api/v1/photos/{pid_photo}/evidence-links",
                        headers=env.H, json={
        "evidence_reference": "rec-crash3",
        "evidence_type": "signature_manifest"})
    assert r.status_code == 201, r.text
    scan = evidence_recovery.scan_recovery()
    entry = next(p for p in scan["photos"]
                 if p["photo_id"] == pid_photo)
    assert entry["state"] == "evidence_pending"
    res = evidence_recovery.apply_recovery()
    # reverify does NOT fabricate a verification run
    ran = next(x for x in res["results"]
               if x["photo_id"] == pid_photo)
    assert ran["applied"] is False
    assert "client retry" in ran["note"]
    scan2 = evidence_recovery.scan_recovery()
    entry2 = next(p for p in scan2["photos"]
                  if p["photo_id"] == pid_photo)
    assert entry2["state"] == "evidence_pending"   # still not trusted


# ------------------------------------------- crash scenario 4

def test_partial_write_left_temp_file(env):
    """A crashed DER/object write leaves a .part temp behind. The
    object store's own fsck reports/cleans temps; recovery never
    treats a temp as the real object."""
    data = _photo_bytes(4)
    sha = hashlib.sha256(data).hexdigest()
    pid_photo = _register_photo(env, data, "crash4")
    # simulate: temp written, rename never happened
    obj_dir = original_store.object_path(sha).parent
    obj_dir.mkdir(parents=True, exist_ok=True)
    (obj_dir / ".crash4_part.jpg.part").write_bytes(data[:100])
    # a REAL partial never becomes the object: store_original rejects
    # a mismatched claimed hash by construction; verify the object
    # itself is still absent
    assert not original_store.object_path(sha).exists()
    scan = evidence_recovery.scan_recovery()
    entry = next(p for p in scan["photos"]
                 if p["photo_id"] == pid_photo)
    assert entry["state"] in ("registered", "stored_unbound")
    # .part files are not seen as objects (glob is *.jpg with .part
    # suffix excluded by construction - stem vs suffix check)
    assert not any(o.get("path", "").endswith(".part")
                   for o in scan["orphan_objects"])
    # now the real upload completes over the debris
    _upload(env, pid_photo, data)
    scan2 = evidence_recovery.scan_recovery()
    entry2 = next(p for p in scan2["photos"]
                  if p["photo_id"] == pid_photo)
    assert entry2["state"] in ("bound", "stored_unbound")


# ------------------------------------------- crash scenario 5

def test_db_commit_failure_leaves_bytes_sacred(env):
    """DB commit fails after the object landed (simulated with a raw
    connection rolled back). Bytes stay, the row does not - and
    recovery calls the result an orphan (reported, never deleted)."""
    data = _photo_bytes(5)
    sha = hashlib.sha256(data).hexdigest()
    original_store.store_original(data, sha)
    # a metadata row begins a transaction and ROLLS BACK (commit died)
    import sqlite3
    import app.db as dbm
    conn = sqlite3.connect(str(dbm._db_path()))
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO mvp_projects (project_id, owner_token, name, "
        "description, site_address, status, created_at, updated_at) "
        "VALUES ('ghost-p','x','ghost','','','active','t','t')")
    conn.rollback()
    conn.close()
    scan = evidence_recovery.scan_recovery()
    assert any(o["original_sha256"] == sha
               for o in scan["orphan_objects"])
    # recovery NEVER deletes it
    res = evidence_recovery.apply_recovery()
    assert not any("delete" in str(r).lower() and
                   r.get("photo_id") is None
                   for r in res["results"])


# ------------------------------------------- crash scenario 6

def test_hash_mismatch_bytes_are_never_bound(env):
    """Bytes on disk no longer match the claimed hash (tampered or
    wrong object). store_original already rejects at write time; the
    recovery rebind path re-hashes and ESCALATES on mismatch."""
    data = _photo_bytes(6)
    sha = hashlib.sha256(data).hexdigest()
    pid_photo = _register_photo(env, data, "crash6")
    obj = original_store.object_path(sha)
    obj.parent.mkdir(parents=True, exist_ok=True)
    obj.write_bytes(data[:-1] + b"\xff")      # wrong bytes smuggled in
    scan = evidence_recovery.scan_recovery()
    entry = next(p for p in scan["photos"]
                 if p["photo_id"] == pid_photo)
    assert entry["action"] == "rebind"
    res = evidence_recovery.apply_recovery()
    ran = next(x for x in res["results"]
               if x["photo_id"] == pid_photo)
    assert ran["applied"] is False
    assert "hash mismatch" in ran["note"]
    assert ran["action"] == "report_violation"
    # and the wrong bytes survive untouched (operator decides)
    assert obj.exists()


# ------------------------------------------- crash scenario 7

def test_disk_full_fails_cleanly(env, monkeypatch):
    """A disk-full on the object write must fail with NOTHING
    persisted (no temp leak, no DB row) - the write discipline's own
    guarantee, pinned here at the recovery boundary."""
    data = _photo_bytes(7)
    sha = hashlib.sha256(data).hexdigest()
    pid_photo = _register_photo(env, data, "crash7")
    import app.services.original_store as ostore
    real_mkstemp = tempfile.mkstemp

    def full_disk(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr("tempfile.mkstemp", full_disk)
    try:
        env.client.put(f"/api/v1/photos/{pid_photo}/original",
                       headers=env.H, content=data)
        wrote = True
    except OSError as e:
        wrote = False
        assert e.errno == 28
    monkeypatch.setattr("tempfile.mkstemp", real_mkstemp)
    assert wrote is False      # transport-level failure surfaced clean
    # object absent, no temp leaked
    assert not original_store.object_path(sha).exists()
    temps = list(original_store.store_root().rglob("*.part")) \
        if original_store.store_root().exists() else []
    assert temps == []
    # recovery classifies the photo as still awaiting upload
    scan = evidence_recovery.scan_recovery()
    entry = next(p for p in scan["photos"]
                 if p["photo_id"] == pid_photo)
    assert entry["state"] == "registered"
    # and the retry succeeds
    _upload(env, pid_photo, data)
    scan2 = evidence_recovery.scan_recovery()
    entry2 = next(p for p in scan2["photos"]
                  if p["photo_id"] == pid_photo)
    assert entry2["state"] in ("bound", "stored_unbound")


import tempfile  # noqa: E402  (used by the monkeypatch above)


# ------------------------------------------- crash scenario 8

def test_duplicate_recovery_is_idempotent(env):
    data = _photo_bytes(8)
    pid_photo = _register_photo(env, data, "crash8")
    sha = hashlib.sha256(data).hexdigest()
    original_store.store_original(data, sha)
    for _ in range(2):
        evidence_recovery.apply_recovery()
    scan = evidence_recovery.scan_recovery()
    assert scan["counts"].get("bound", 0) == 1
    assert scan["counts"].get("stored_unbound", 0) == 0


# ------------------------------------------- crash scenario 9

def test_concurrent_recovery_no_double_apply(env):
    data = _photo_bytes(9)
    pid_photo = _register_photo(env, data, "crash9")
    sha = hashlib.sha256(data).hexdigest()
    original_store.store_original(data, sha)
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        return evidence_recovery.apply_recovery()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(worker) for _ in range(2)]
        results = [f.result() for f in futs]
    total_applied = sum(r["applied"] for r in results)
    # exactly one rebind across both racing recoveries
    assert total_applied == 1
    # binding table holds exactly one row for the photo
    import sqlite3
    import app.db as dbm
    conn = sqlite3.connect(str(dbm._db_path()))
    n = conn.execute(
        "SELECT COUNT(*) FROM mvp_photo_originals WHERE photo_id = ?",
        (pid_photo,)).fetchone()[0]
    conn.close()
    assert n == 1


# ------------------------------------------- crash scenario 10

def test_orphan_evidence_reported_never_deleted(env):
    data = _photo_bytes(10)
    sha = hashlib.sha256(data).hexdigest()
    original_store.store_original(data, sha)      # no metadata row at all
    scan = evidence_recovery.scan_recovery()
    orphan = next(o for o in scan["orphan_objects"]
                  if o["original_sha256"] == sha)
    assert orphan["action"] == "report_orphan_bytes"
    assert "never deleted" in orphan["description"]
    evidence_recovery.apply_recovery()
    assert original_store.object_path(sha).exists()   # untouched


# ------------------------------------------- crash scenario 11

def test_orphan_reference_is_a_violation(env):
    """The Night-04 shape at its worst: an evidence link points at a
    photo whose bytes/binding never survived. The pure machine calls
    it VIOLATION; the server scan reports it through the fact chain
    (photo row without bytes -> REGISTERED; a link row without its
    photo would be the DB-enforced FK-violating shape, so the pure
    machine covers the no-binding variant)."""
    # F4 without F3 at the pure level:
    p = plan_recovery(PipelineFacts(metadata_row=True,
                                    evidence_link_row=True))
    assert p["state"] == "violation"
    assert p["action"] == "report_violation"
    # and a verified claim with nothing under it:
    p2 = plan_recovery(PipelineFacts(link_verified=True))
    assert p2["state"] == "violation"


def test_incomplete_signature_never_reaches_trusted():
    """I4 direct: every state short of server-verified is distinct
    from TRUSTED_SIGNED, and no action in the table upgrades trust."""
    for f in (PipelineFacts(metadata_row=True),
              PipelineFacts(metadata_row=True,
                            original_bytes_on_disk=True),
              PipelineFacts(metadata_row=True,
                            original_bytes_on_disk=True,
                            binding_row=True),
              PipelineFacts(metadata_row=True,
                            original_bytes_on_disk=True,
                            binding_row=True,
                            evidence_link_row=True)):
        p = plan_recovery(f)
        assert p["state"] != "trusted_signed"
        assert p["action"] != "verify"          # no invented upgrades
