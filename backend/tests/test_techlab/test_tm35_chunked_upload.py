"""TM35: resumable chunked upload - full-chain tests.

Covers: session open/re-open idempotence, out-of-order chunks,
duplicate chunks (idempotent with proof), corrupt-chunk rejection,
interruption + RESUME, server-restart survival (session state lives
in DB+files), full-file hash mismatch (nothing reaches the immutable
store), completion binding, cancel cleanup, cross-owner 404 isolation.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402

from app.main import app  # noqa: E402
from app.services import device_identity  # noqa: E402

CODE = "tm35-code"


def _photo(seed: int, size: int = 300_000) -> bytes:
    """Deterministic non-compressible-ish bytes (~300KB, 2+ chunks)."""
    import random
    rnd = random.Random(seed)
    return bytes(rnd.getrandbits(8) for _ in range(size))


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
        r = client.post("/api/v1/devices/enroll",
                        json={"public_key_b64":
                              base64.b64encode(pub).decode(),
                              "device_id": name,
                              "enrollment_code": CODE})
        assert r.status_code in (200, 201)
        return {"X-Device-Token": r.json()["device_token"]}

    A, B = enroll("tm35-A"), enroll("tm35-B")
    r = client.post("/api/v1/projects", headers=A,
                    json={"name": "Upload Site"})
    pid = r.json()["project_id"]

    def register(key):
        data = _photo(abs(hash(key)) % 9999)
        sha = hashlib.sha256(data).hexdigest()
        r = client.post(f"/api/v1/projects/{pid}/photos", headers=A,
                        json={"idempotency_key": f"ik-{key}",
                              "original_sha256": sha,
                              "original_filename": f"{key}.jpg",
                              "mime_type": "image/jpeg",
                              "byte_size": len(data),
                              "captured_at":
                                  "2026-11-26T08:00:00+00:00",
                              "device_id": "cam"})
        assert r.status_code == 201, r.text
        return r.json()["photo_id"], data, sha

    def open_session(photo_id, sha, chunk_size, data):
        n = (len(data) + chunk_size - 1) // chunk_size
        r = client.post(f"/api/v1/photos/{photo_id}/original-session",
                        headers=A, json={
            "claimed_sha256": sha, "chunk_size": chunk_size,
            "total_chunks": n, "total_bytes": len(data)})
        assert r.status_code == 200, r.text
        return r.json()

    def put_chunk(photo_id, uid, idx, chunk):
        r = client.put(
            f"/api/v1/photos/{photo_id}/original-session/{uid}"
            f"/chunk/{idx}",
            headers={**A, "X-Chunk-SHA256":
                     hashlib.sha256(chunk).hexdigest()},
            content=chunk)
        return r

    return type("E", (), {
        "client": client, "A": A, "B": B, "pid": pid,
        "register": staticmethod(register),
        "open_session": staticmethod(open_session),
        "put_chunk": staticmethod(put_chunk)})()


def test_full_chain_resume_after_interruption(env):
    photo, data, sha = env.register("resume-photo")
    CH = 128 * 1024
    s = env.open_session(photo, sha, CH, data)
    uid = s["upload_id"]
    chunks = [data[i:i + CH] for i in range(0, len(data), CH)]
    # interruption: only the first 2 chunks make it before the crash
    for i in (0, 1):
        r = env.put_chunk(photo, uid, i, chunks[i])
        assert r.status_code == 200 and r.json()["stored"] is True
    # RESUME: status says what is next
    r = env.client.get(
        f"/api/v1/photos/{photo}/original-session/{uid}", headers=env.A)
    st = r.json()
    assert st["received_count"] == 2 and st["next_chunk"] == 2
    # complete() refuses while incomplete and NAMES the gaps
    r = env.client.post(
        f"/api/v1/photos/{photo}/original-session/{uid}/complete",
        headers=env.A)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "upload_incomplete"
    # finish the remaining chunks OUT OF ORDER
    rest = list(range(2, len(chunks)))
    rest.reverse()
    for i in rest:
        r = env.put_chunk(photo, uid, i, chunks[i])
        assert r.status_code == 200
    # duplicate re-send is idempotent with proof
    r = env.put_chunk(photo, uid, 1, chunks[1])
    assert r.status_code == 200
    assert r.json()["duplicate"] is True and r.json()["stored"] is False
    # complete
    r = env.client.post(
        f"/api/v1/photos/{photo}/original-session/{uid}/complete",
        headers=env.A)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed" and body["bound"] is True
    # the object is in the immutable store and hash-verified
    r = env.client.get(f"/api/v1/photos/{photo}/original/integrity",
                      headers=env.A)
    body = r.json()
    assert body["bound_to_photo"] is True
    assert body["claims"]["file_integrity_verified"] is True


def test_corrupt_chunk_rejected_before_receiving(env):
    photo, data, sha = env.register("corrupt-photo")
    CH = 128 * 1024
    chunks = [data[i:i + CH] for i in range(0, len(data), CH)]
    s = env.open_session(photo, sha, CH, data)
    uid = s["upload_id"]
    r = env.client.put(
        f"/api/v1/photos/{photo}/original-session/{uid}/chunk/0",
        headers={**env.A,
                 "X-Chunk-SHA256": hashlib.sha256(b"WRONG").hexdigest()},
        content=chunks[0])
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "chunk_hash_mismatch"
    # the corrupt chunk never entered the received set
    r = env.client.get(
        f"/api/v1/photos/{photo}/original-session/{uid}", headers=env.A)
    assert r.json()["received_count"] == 0
    # the session is still fully usable afterwards
    r = env.put_chunk(photo, uid, 0, chunks[0])
    assert r.status_code == 200


def test_conflicting_duplicate_chunk_refused(env):
    photo, data, sha = env.register("dupchunk-photo")
    CH = 128 * 1024
    s = env.open_session(photo, sha, CH, data)
    uid = s["upload_id"]
    r = env.put_chunk(photo, uid, 0, data[:CH])
    assert r.status_code == 200
    # same index, DIFFERENT bytes, valid sha for the new bytes:
    # a takeover attempt must be a typed conflict
    evil = bytes([0x41] * CH)
    r = env.client.put(
        f"/api/v1/photos/{photo}/original-session/{uid}/chunk/0",
        headers={**env.A,
                 "X-Chunk-SHA256":
                     hashlib.sha256(evil).hexdigest()},
        content=evil)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "chunk_conflict"


def test_server_restart_survival(env, tmp_path):
    """Session state lives in the DB row + chunk files: a 'restart'
    (re-instantiated client over the same DB/files) resumes cleanly."""
    photo, data, sha = env.register("restart-photo")
    CH = 128 * 1024
    s = env.open_session(photo, sha, CH, data)
    uid = s["upload_id"]
    chunks = [data[i:i + CH] for i in range(0, len(data), CH)]
    env.put_chunk(photo, uid, 0, chunks[0])
    # simulate restart: a FRESH TestClient over the same database
    client2 = TestClient(app)
    r = client2.get(f"/api/v1/photos/{photo}/original-session/{uid}",
                    headers=env.A)
    assert r.status_code == 200
    assert r.json()["received_count"] == 1      # state survived
    for i in range(1, len(chunks)):
        r = client2.put(
            f"/api/v1/photos/{photo}/original-session/{uid}"
            f"/chunk/{i}",
            headers={**env.A, "X-Chunk-SHA256":
                     hashlib.sha256(chunks[i]).hexdigest()},
            content=chunks[i])
        assert r.status_code == 200
    r = client2.post(
        f"/api/v1/photos/{photo}/original-session/{uid}/complete",
        headers=env.A)
    assert r.status_code == 200
    assert r.json()["bound"] is True


def test_full_hash_mismatch_nothing_reaches_store(env):
    """All chunks arrive, but they assemble to the WRONG bytes: the
    complete() call refuses with a typed error, the assembled temp is
    discarded and NOTHING enters the immutable store."""
    photo, data, sha = env.register("mismatch-photo")
    CH = 128 * 1024
    # upload DIFFERENT bytes under the session for the CLAIMED sha
    evil = bytes([0x42] * len(data))
    s = env.open_session(photo, sha, CH, data)
    uid = s["upload_id"]
    chunks = [evil[i:i + CH] for i in range(0, len(evil), CH)]
    for i, c in enumerate(chunks):
        r = env.put_chunk(photo, uid, i, c)
        assert r.status_code == 200
    r = env.client.post(
        f"/api/v1/photos/{photo}/original-session/{uid}/complete",
        headers=env.A)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "full_hash_mismatch"
    # store untouched
    from app.services import original_store
    assert not original_store.object_path(sha).exists()
    # session stays open: re-send the CORRECT chunks and complete
    good = [data[i:i + CH] for i in range(0, len(data), CH)]
    for i, c in enumerate(good):
        r = env.client.put(
            f"/api/v1/photos/{photo}/original-session/{uid}"
            f"/chunk/{i}",
            headers={**env.A, "X-Chunk-SHA256":
                     hashlib.sha256(c).hexdigest()},
            content=c)
        # index i was already received with different bytes -> the
        # retry needs the takeover protection to fire here too
        if r.status_code == 409:
            # overwrite-with-different-bytes is refused: the client
            # must cancel + re-open (honest protocol)
            r = env.client.delete(
                f"/api/v1/photos/{photo}/original-session/{uid}",
                headers=env.A)
            assert r.status_code == 200
            s2 = env.open_session(photo, sha, CH, data)
            uid = s2["upload_id"]
            for i2, c2 in enumerate(good):
                r = env.put_chunk(photo, uid, i2, c2)
                assert r.status_code == 200
            break
    r = env.client.post(
        f"/api/v1/photos/{photo}/original-session/{uid}/complete",
        headers=env.A)
    assert r.status_code == 200, r.text
    assert r.json()["bound"] is True


def test_cancel_cleans_chunks_and_keeps_photo(env):
    photo, data, sha = env.register("cancel-photo")
    CH = 128 * 1024
    s = env.open_session(photo, sha, CH, data)
    uid = s["upload_id"]
    env.put_chunk(photo, uid, 0, data[:CH])
    from app.services import chunked_upload
    d = chunked_upload._session_dir(uid)
    assert d.exists()
    r = env.client.delete(
        f"/api/v1/photos/{photo}/original-session/{uid}", headers=env.A)
    assert r.status_code == 200
    assert not d.exists()                     # temp cleanup done
    # cancelled session refuses further chunks with a typed error
    r = env.put_chunk(photo, uid, 1, data[CH:2 * CH])
    assert r.status_code == 404 or r.status_code == 409
    # the photo record itself is untouched and re-openable
    s2 = env.open_session(photo, sha, CH, data)
    assert s2["upload_id"] != uid


def test_cross_owner_isolation(env):
    photo, data, sha = env.register("iso-photo")
    s = env.open_session(photo, sha, 128 * 1024, data)
    uid = s["upload_id"]
    env.put_chunk(photo, uid, 0, data[:128 * 1024])
    # the second device sees 404 on every session surface
    r = env.client.get(
        f"/api/v1/photos/{photo}/original-session/{uid}", headers=env.B)
    assert r.status_code == 404
    r = env.client.delete(
        f"/api/v1/photos/{photo}/original-session/{uid}", headers=env.B)
    assert r.status_code == 404
    r = env.client.post(
        f"/api/v1/photos/{photo}/original-session/{uid}/complete",
        headers=env.B)
    assert r.status_code == 404


def test_reopen_is_idempotent(env):
    photo, data, sha = env.register("reopen-photo")
    s1 = env.open_session(photo, sha, 128 * 1024, data)
    s2 = env.open_session(photo, sha, 128 * 1024, data)
    assert s1["upload_id"] == s2["upload_id"]     # resumed, not doubled
    # different params -> a NEW session (old one stays for cleanup)
    s3 = env.open_session(photo, sha, 256 * 1024, data)
    assert s3["upload_id"] != s1["upload_id"]
