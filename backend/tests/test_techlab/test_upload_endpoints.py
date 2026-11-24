"""W003 - T12 REAL upload endpoint tests (FastAPI TestClient).

Every endpoint is exercised over HTTP semantics: status codes, stream
body handling, restart recovery (new storage access after 'reboot'),
hostile inputs and resource limits.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_upload_dir(tmp_path, monkeypatch):
    """Each test gets its own storage root + the opt-in flag; 'restart'
    semantics = state read from disk again."""
    monkeypatch.setenv("WORKCAM_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "test-code-123")
    # import-time binding workaround: the module reads the env lazily via
    # helper, but _STORAGE_ROOT was captured at import. Re-import trick:
    import app.api.upload as up
    monkeypatch.setattr(up, "_STORAGE_ROOT", tmp_path / "uploads")
    yield tmp_path / "uploads"


def _register(total_bytes: int, chunk_size: int, sha: str | None = None):
    body = {"total_bytes": total_bytes, "chunk_size": chunk_size,
            "total_sha256": sha or ("ab" * 32)}
    r = client.post("/upload/experimental/batches", json=body)
    return r


def _chunk_put(batch_id: str, index: int, payload: bytes):
    return client.put(f"/upload/experimental/batches/{batch_id}/chunks/{index}",
                      content=payload,
                      headers={"Content-Length": str(len(payload))})


def _status(batch_id: str):
    return client.get(f"/upload/experimental/batches/{batch_id}")


def _complete(batch_id: str):
    return client.post(f"/upload/experimental/batches/{batch_id}/complete")


def _register_with_plan(data: bytes, chunk_size: int):
    return _register(len(data), chunk_size, hashlib.sha256(data).hexdigest())


# ------------------------------------------------------------ happy path


def test_register_chunk_complete_roundtrip():
    data = b"X" * 1000
    r = _register_with_plan(data, 400)
    assert r.status_code == 200
    bid = r.json()["batch_id"]
    assert r.json()["n_chunks"] == 3      # 400+400+200

    chunks = [data[:400], data[400:800], data[800:]]
    for i, c in enumerate(chunks):
        rr = _chunk_put(bid, i, c)
        assert rr.status_code == 200
        assert rr.json()["sha256"] == hashlib.sha256(c).hexdigest()

    st = _status(bid).json()
    assert st["complete"] is True

    comp = _complete(bid)
    assert comp.status_code == 200
    assert comp.json()["total_sha256"] == hashlib.sha256(data).hexdigest()


def test_out_of_order_and_resume():
    data = b"Y" * 900
    bid = _register_with_plan(data, 300).json()["batch_id"]
    chunks = [data[i:i + 300] for i in range(0, 900, 300)]

    # send chunks 2 and 0 only (network drops mid-batch)
    assert _chunk_put(bid, 2, chunks[2]).status_code == 200
    assert _chunk_put(bid, 0, chunks[0]).status_code == 200

    st = _status(bid).json()
    assert st["complete"] is False
    assert st["missing"] == [1]

    # 'cannot complete' is a 409 with the missing count
    r = _complete(bid)
    assert r.status_code == 409
    assert "missing" in r.json()["detail"]

    # resume: send the missing one
    assert _chunk_put(bid, 1, chunks[1]).status_code == 200
    assert _complete(bid).status_code == 200


def test_duplicate_chunk_idempotent_ack():
    data = b"Z" * 300
    bid = _register_with_plan(data, 300).json()["batch_id"]
    first = _chunk_put(bid, 0, data)
    assert first.json()["status"] == "acked"
    second = _chunk_put(bid, 0, data)
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate_acked"


# ------------------------------------------------------------ restart


def test_server_restart_recovery(isolated_upload_dir):
    """Batches live on DISK: a 'reboot' (fresh process semantics via the
    same storage) keeps every chunk and completion works."""
    data = b"R" * 700
    bid = _register_with_plan(data, 300).json()["batch_id"]
    chunks = [data[:300], data[300:600], data[600:]]
    _chunk_put(bid, 0, chunks[0])
    _chunk_put(bid, 1, chunks[1])

    # simulate restart: the module-level storage path stays, but a NEW
    # TestClient/process sees only what is on disk - here we simply
    # re-check status via a second client instance (same app, same disk)
    client2 = TestClient(app)
    st = client2.get(f"/upload/experimental/batches/{bid}").json()
    assert st["missing"] == [2]
    assert client2.put(f"/upload/experimental/batches/{bid}/chunks/2",
                       content=chunks[2],
                       headers={"Content-Length": str(len(chunks[2]))}).status_code == 200
    r = client2.post(f"/upload/experimental/batches/{bid}/complete")
    assert r.status_code == 200
    assert r.json()["total_sha256"] == hashlib.sha256(data).hexdigest()


# --------------------------------------------------------- hostile input


def test_unknown_batch_404():
    assert client.get("/upload/experimental/batches/deadbeef").status_code == 404
    assert _chunk_put("deadbeef", 0, b"x").status_code == 404


def test_batch_id_traversal_rejected(isolated_upload_dir):
    r = client.get("/upload/experimental/batches/..%2fetc")
    # route/charset guard: non-hex ids never touch disk (404/400 both
    # acceptable - the assertion that MATTERS is the storage check below)
    assert r.status_code in (400, 404)
    assert not isolated_upload_dir.exists() or \
        not any(isolated_upload_dir.iterdir())


def test_index_out_of_plan_rejected():
    bid = _register_with_plan(b"a" * 100, 50).json()["batch_id"]
    r = _chunk_put(bid, 99, b"q")          # plan has 0..1
    assert r.status_code == 400


def test_oversize_chunk_rejected():
    bid = _register_with_plan(b"b" * 100, 50).json()["batch_id"]
    r = client.put(f"/upload/experimental/batches/{bid}/chunks/0",
                   content=b"q" * 80,       # plan says exactly 50
                   headers={"Content-Length": "80"})
    assert r.status_code == 400


def test_short_chunk_rejected():
    bid = _register_with_plan(b"c" * 100, 50).json()["batch_id"]
    r = client.put(f"/upload/experimental/batches/{bid}/chunks/0",
                   content=b"q" * 20,
                   headers={"Content-Length": "20"})
    assert r.status_code == 400
    # slot reusable: correct retry works
    assert _chunk_put(bid, 0, b"c" * 50).status_code == 200


def test_whole_batch_mismatch_rejected():
    data = b"D" * 120
    r = _register(120, 60, sha="ff" * 32)   # forged whole-batch hash
    bid = r.json()["batch_id"]
    _chunk_put(bid, 0, data[:60])
    _chunk_put(bid, 1, data[60:])
    r = _complete(bid)
    assert r.status_code == 409
    assert "whole-batch" in r.json()["detail"]
    # chunks remain for a corrected retry (nothing was destroyed)
    st = _status(bid).json()
    assert st["complete"] is True


def test_production_env_disables_endpoints(monkeypatch):
    monkeypatch.setenv("WORKCAM_ENV", "production")
    r = client.post("/upload/experimental/batches",
                    json={"total_bytes": 100, "chunk_size": 50,
                          "total_sha256": "ab" * 32})
    assert r.status_code == 503
    monkeypatch.delenv("WORKCAM_ENV")


def test_limits_enforced():
    # chunk count cap (checked by the endpoint logic -> 400)
    r = _register(10_000, 10)             # 1000 chunks > 512 cap
    assert r.status_code == 400
    # per-batch total cap (body-model validation -> 422, same refusal)
    r = _register(3 * 1024 * 1024 * 1024, 1024 * 1024)
    assert r.status_code == 422
    # chunk size cap (body-model validation -> 422)
    r = _register(10 * 1024 * 1024, 5 * 1024 * 1024)
    assert r.status_code == 422


# ------------------------------------------------- P1 security review tests


def test_opt_in_disabled_by_default(monkeypatch):
    """P1: the endpoints must NOT be reachable unless explicitly opted
    in. A production deployment that forgot WORKCAM_ENV must not
    expose them by accident."""
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_ENV", raising=False)
    r = client.post("/upload/experimental/batches",
                    json={"total_bytes": 100, "chunk_size": 50,
                          "total_sha256": "ab" * 32})
    assert r.status_code == 503
    assert "opt-in" in r.json()["detail"]


def test_production_veto_beats_opt_in(monkeypatch):
    """Even WITH the opt-in flag, production hard-disables."""
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    monkeypatch.setenv("WORKCAM_ENV", "production")
    r = client.post("/upload/experimental/batches",
                    json={"total_bytes": 100, "chunk_size": 50,
                          "total_sha256": "ab" * 32})
    assert r.status_code == 503
    assert "production" in r.json()["detail"]
    monkeypatch.delenv("WORKCAM_ENV")


def test_orphan_part_swept(isolated_upload_dir):
    """A killed PUT leaves a .part behind; the next register() sweep
    removes expired ones (resource cleanup, P1)."""
    isolated_upload_dir.mkdir(parents=True, exist_ok=True)
    orphan = isolated_upload_dir / ".chunk_0.deadbeefcafe1234.part"
    orphan.write_bytes(b"INTERRUPTED")
    # force it to look expired for the sweeper
    import os as _os
    old = time.time() - 7200
    _os.utime(orphan, (old, old))

    _register(100, 50)                     # register triggers _sweep
    assert not orphan.exists()


def test_stale_part_does_not_block_correct_upload(isolated_upload_dir):
    """A leftover .part from a crashed PUT must never interfere with a
    subsequent correct upload of the same chunk (unique temp names)."""
    data = b"S" * 200
    bid = _register_with_plan(data, 100).json()["batch_id"]
    # simulate a killed upload of chunk 0 (temp file left mid-write)
    import secrets as _secrets
    stale = isolated_upload_dir / bid / f".chunk_0.{_secrets.token_hex(8)}.part"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"PARTIAL GARBAGE FROM KILLED PUT")

    r = _chunk_put(bid, 0, data[:100])
    assert r.status_code == 200            # correct upload unaffected
    _chunk_put(bid, 1, data[100:])
    assert _complete(bid).status_code == 200
    # the server-assembled bytes are exactly the payload
    dl = client.get(f"/upload/experimental/batches/{bid}/content")
    assert dl.status_code == 200
    assert b"".join(dl.iter_bytes()) == data
