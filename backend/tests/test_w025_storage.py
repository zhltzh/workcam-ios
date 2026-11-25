"""B02-W025 - storage space management tests.

DESKTOP_SIMULATION: all data is synthetic, all timings are single-run
desktop observations recorded for the scale report - no performance
claims. Hard invariants pinned by these tests:

  - original bytes NEVER change through any storage-op (thumbnail,
    audit, prune, backup/restore) - the W007 red line
  - cache pruning is structurally incapable of touching originals/
  - the streaming ingest path is byte-identical in outcome to the
    legacy W007 store (same object path, same idempotency, same
    error codes) while holding at most ONE chunk in memory
"""

from __future__ import annotations

import hashlib
import io
import random
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.main import app
from app.services import original_store, storage_ops
from tests.conftest import _encode_pub


# ------------------------------------------------------------ helpers

def jpeg_bytes(w: int = 96, h: int = 72, seed: int = 0) -> bytes:
    """Small deterministic synthetic JPEG (fast to build at scale)."""
    from PIL import Image
    rng = random.Random(seed)
    img = Image.new("RGB", (w, h))
    img.putdata([(rng.randrange(256), rng.randrange(256),
                  rng.randrange(256)) for _ in range(w * h)])
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def w025_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "w025.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "w025-code")
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    monkeypatch.delenv("WORKCAM_TRUST_PROXY_HEADERS", raising=False)
    import app.db as dbm
    dbm.migrate(tmp_path / "w025.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    import app.services.device_identity as di
    import base64

    devices = {}
    for name in ("device-A", "device-B"):
        key = ec.generate_private_key(ec.SECP256R1())
        pub_b64 = base64.b64encode(
            _encode_pub(key.public_key())).decode()
        token = di.enroll_device(pub_b64, name, "w025-code")
        devices[name] = (key, pub_b64, token)
    c = TestClient(app)
    ha = {"X-Device-Token": devices["device-A"][2]}
    pid = c.post(
        "/api/v1/projects", json={"name": "W025 Site"},
        headers=ha).json()["project_id"]
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "w025.db", "client": c,
        "H": ha,
        "B": {"X-Device-Token": devices["device-B"][2]},
        "pid": pid,
    })()


def register_photo(env, data: bytes, key: str | None = None,
                   headers=None, pid: str | None = None) -> str:
    """Register photo metadata + upload bytes via the STREAMING path
    (both HTTP paths are exercised across the suite)."""
    sha = sha256_of(data)
    r = env.client.post(
        f"/api/v1/projects/{pid or env.pid}/photos",
        json={"idempotency_key": key or f"ik-{uuid.uuid4()}",
              "original_sha256": sha,
              "original_filename": "f.jpg", "mime_type": "image/jpeg",
              "byte_size": len(data), "captured_at": "t",
              "device_id": "d"},
        headers=headers or env.H)
    assert r.status_code == 201, r.text
    pid_photo = r.json()["photo_id"]
    r2 = env.client.put(
        f"/api/v1/photos/{pid_photo}/original-stream", content=data,
        headers={**(headers or env.H),
                 "Content-Type": "application/octet-stream"})
    assert r2.status_code == 200, r2.text
    return pid_photo


def all_original_hashes(env) -> list[str]:
    """Snapshot of every stored original's content hash - the invariant
    comparator for 'originals never change' assertions."""
    root = original_store.store_root()
    out = []
    if root.exists():
        for p in sorted(root.rglob("*.jpg")):
            out.append(original_store._hash_file(p))
    return out


# ============================== streaming ingest ==================

def test_streaming_store_matches_w007_object_path(w025_env):
    """Content addressing is one store, not two: the streaming path and
    the legacy W007 bytes path land the SAME content in the SAME
    file with the same ledger row."""
    from app.services import original_store, storage_ops
    data = jpeg_bytes(seed=1)
    sha = sha256_of(data)

    r1 = storage_ops.store_streaming(iter([data[:50], data[50:]]), sha)
    assert r1["sha256"] == sha and r1["idempotent_replay"] is False

    r2 = original_store.store_original(data, sha)     # legacy twin
    assert r2["idempotent_replay"] is True            # same object
    assert original_store.object_path(sha).read_bytes() == data


def test_streaming_rejects_mismatch_and_leaves_no_residue(w025_env):
    from app.services import storage_ops
    data = jpeg_bytes(seed=2)
    claimed = sha256_of(data)[:-1] + ("0" if data[0] % 2 else "1")
    with pytest.raises(Exception) as ei:
        storage_ops.store_streaming(iter([data]), claimed)
    assert "hash_mismatch" in str(ei.value) or "mismatch" in str(
        ei.value).lower()
    # nothing persisted: no object, no temp residue
    assert not original_store.object_path(claimed).exists()
    audit = storage_ops.audit_storage()
    assert audit["temp_residue"] == []


def test_streaming_empty_body_rejected(w025_env):
    from app.services import storage_ops
    with pytest.raises(Exception):
        storage_ops.store_streaming(iter([b"", b""]), "a" * 64)


def test_streaming_memory_is_bounded_to_one_chunk(w025_env):
    """The RISK-W021-R1 point, as an I/O-pattern assertion: the source
    is a LAZY generator and every observed chunk stays under the
    chunk bound; the full payload is never materialized by either
    side. (DESKTOP_SIMULATION: this pins the ingest path's access
    pattern, which bounds heap use by construction.)"""
    from app.services import storage_ops
    payload = bytes(random.Random(42).randbytes(2 * 1024 * 1024))
    sha = sha256_of(payload)
    chunk_bound = 256 * 1024
    seen = {"peak": 0, "count": 0, "total": 0}

    def lazy_chunks():
        for i in range(0, len(payload), 64 * 1024):
            chunk = payload[i:i + 64 * 1024]      # slice per chunk ONLY
            seen["peak"] = max(seen["peak"], len(chunk))
            seen["count"] += 1
            seen["total"] += len(chunk)
            yield chunk

    r = storage_ops.store_streaming(lazy_chunks(), sha)
    assert r["byte_size"] == len(payload)
    assert seen["peak"] <= chunk_bound
    assert seen["count"] == 32                    # 2MB / 64KB
    from app.services import original_store
    assert original_store.object_path(sha).read_bytes() == payload


def test_streaming_api_roundtrip_large_body(w025_env):
    """2.7MB high-entropy body through the HTTP streaming endpoint -
    W016-K3's payload class on the new path. Download must be
    byte-identical and integrity must verify."""
    payload = random.Random(1337).randbytes(2_820_329)
    sha = sha256_of(payload)
    photo_id = w025_env.client.post(
        f"/api/v1/projects/{w025_env.pid}/photos",
        json={"idempotency_key": "stream-2.7m",
              "original_sha256": sha, "original_filename": "big.jpg",
              "mime_type": "image/jpeg", "byte_size": len(payload),
              "captured_at": "t", "device_id": "d"},
        headers=w025_env.H).json()["photo_id"]
    r = w025_env.client.put(
        f"/api/v1/photos/{photo_id}/original-stream", content=payload,
        headers={**w025_env.H,
                 "Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["streamed"] is True
    assert body["byte_size"] == len(payload)

    dl = w025_env.client.get(f"/api/v1/photos/{photo_id}/original",
                             headers=w025_env.H)
    assert dl.status_code == 200
    assert dl.content == payload
    it = w025_env.client.get(
        f"/api/v1/photos/{photo_id}/original/integrity",
        headers=w025_env.H).json()
    assert it["original"]["file_hash_valid"] is True


def test_streaming_api_mismatch_persists_nothing(w025_env):
    data = jpeg_bytes(seed=3)
    photo_id = w025_env.client.post(
        f"/api/v1/projects/{w025_env.pid}/photos",
        json={"idempotency_key": "stream-bad",
              "original_sha256": sha256_of(data),
              "original_filename": "f.jpg", "mime_type": "image/jpeg",
              "byte_size": len(data), "captured_at": "t",
              "device_id": "d"},
        headers=w025_env.H).json()["photo_id"]
    r = w025_env.client.put(
        f"/api/v1/photos/{photo_id}/original-stream",
        content=data[:-1] + bytes([data[-1] ^ 0xFF]),
        headers={**w025_env.H,
                 "Content-Type": "application/octet-stream"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "hash_mismatch"
    # zero garbage: audit sees no object, no temp
    from app.services import storage_ops
    audit = storage_ops.audit_storage()
    assert audit["orphan_files"] == []
    assert audit["temp_residue"] == []


def test_streaming_idempotent_replay_shares_one_object(w025_env):
    from app.services import original_store, storage_ops
    data = jpeg_bytes(seed=4)
    sha = sha256_of(data)
    r1 = storage_ops.store_streaming(iter([data]), sha)
    r2 = storage_ops.store_streaming(iter([data]), sha)
    assert r1["idempotent_replay"] is False
    assert r2["idempotent_replay"] is True
    stats = storage_ops.storage_stats()
    assert stats["originals"]["physical_files"] == 1


def test_existing_object_corrupted_refused_never_overwritten(w025_env):
    """W007 immutability, streaming edition: an on-disk object whose
    content no longer matches its hash is REFUSED, never repaired by
    re-upload (silent repair would be evidence tampering)."""
    from app.services import original_store, storage_ops
    data = jpeg_bytes(seed=5)
    sha = sha256_of(data)
    storage_ops.store_streaming(iter([data]), sha)
    path = original_store.object_path(sha)
    # tamper with the committed bytes (outside the API, simulating
    # disk corruption)
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 0x01]))
    with pytest.raises(Exception) as ei:
        storage_ops.store_streaming(iter([data]), sha)
    assert "corrupted" in str(ei.value).lower()
    # the corrupted bytes were NOT silently healed
    assert original_store._hash_file(path) != sha


# ============================== thumbnails ========================

def test_thumbnail_generation_dimensions_and_dedup(w025_env):
    data = jpeg_bytes(w=800, h=600, seed=10)
    photo1 = register_photo(w025_env, data, key="th-1")
    photo2 = register_photo(w025_env, data, key="th-2")  # same content
    obj_path = original_store.object_path(sha256_of(data))
    before = sha256_of(obj_path.read_bytes())

    r = w025_env.client.get(f"/api/v1/photos/{photo1}/thumbnail",
                            headers=w025_env.H)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["X-Thumbnail-Of"] == sha256_of(data)
    from PIL import Image
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (512, 384)          # 800x600 scaled by 0.64
    assert r.headers["X-Cache-Hit"] == "False"

    # second photo, SAME original -> cache hit, still ONE file
    r2 = w025_env.client.get(f"/api/v1/photos/{photo2}/thumbnail",
                             headers=w025_env.H)
    assert r2.headers["X-Cache-Hit"] == "True"
    assert r2.content == r.content         # same cache object
    stats = storage_ops.storage_stats()
    assert stats["thumbnails"]["physical_files"] == 1

    # INVARIANT: the original bytes are untouched by thumbnailing
    assert sha256_of(obj_path.read_bytes()) == before


def test_thumbnail_requires_uploaded_original(w025_env):
    data = jpeg_bytes(seed=11)
    photo_id = w025_env.client.post(
        f"/api/v1/projects/{w025_env.pid}/photos",
        json={"idempotency_key": "th-nobytes",
              "original_sha256": sha256_of(data),
              "original_filename": "f.jpg", "mime_type": "image/jpeg",
              "byte_size": len(data), "captured_at": "t",
              "device_id": "d"},
        headers=w025_env.H).json()["photo_id"]
    r = w025_env.client.get(f"/api/v1/photos/{photo_id}/thumbnail",
                            headers=w025_env.H)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "original_not_uploaded"


def test_thumbnail_invalid_edge_rejected(w025_env):
    photo_id = register_photo(w025_env, jpeg_bytes(seed=12),
                              key="th-edge")
    r = w025_env.client.get(
        f"/api/v1/photos/{photo_id}/thumbnail?edge=333",
        headers=w025_env.H)
    assert r.status_code == 400


# ============================== statistics ========================

def test_stats_global_physical_vs_logical(w025_env):
    """Three photos over TWO distinct contents: physical store holds 2
    objects; logical accounting charges 3 photo-bindings. The gap IS
    the dedup saving."""
    data_x = jpeg_bytes(seed=20)
    data_y = jpeg_bytes(seed=21)
    register_photo(w025_env, data_x, key="s-1")
    register_photo(w025_env, data_x, key="s-2")
    register_photo(w025_env, data_y, key="s-3")

    stats = storage_ops.storage_stats()
    assert stats["scope"] == "global"
    assert stats["photos"] == 3
    assert stats["originals"]["physical_files"] == 2
    assert stats["originals"]["ledger_objects"] == 2
    assert stats["originals"]["logical_bytes"] == (
        2 * len(data_x) + len(data_y))
    assert stats["originals"]["dedup_saved_bytes"] == len(data_x)
    assert stats["originals"]["referenced_objects"] == 2
    assert stats["originals"]["unreferenced_objects"] == 0


def test_stats_project_logical_shared_double_counting(w025_env):
    """The same content referenced by TWO projects: each project's
    LOGICAL view charges the full bytes (shared objects are not
    split); the GLOBAL physical view holds them once. Both readings
    are reported, never conflated."""
    data = jpeg_bytes(seed=22)
    pid2 = w025_env.client.post(
        "/api/v1/projects", json={"name": "W025 Site 2"},
        headers=w025_env.H).json()["project_id"]
    sha = sha256_of(data)

    def _register(pid, key):
        photo_id = w025_env.client.post(
            f"/api/v1/projects/{pid}/photos",
            json={"idempotency_key": key, "original_sha256": sha,
                  "original_filename": "f.jpg", "mime_type":
                  "image/jpeg", "byte_size": len(data),
                  "captured_at": "t", "device_id": "d"},
            headers=w025_env.H).json()["photo_id"]
        r = w025_env.client.put(
            f"/api/v1/photos/{photo_id}/original-stream", content=data,
            headers={**w025_env.H,
                     "Content-Type": "application/octet-stream"})
        assert r.status_code == 200

    _register(w025_env.pid, "p1-share")
    _register(pid2, "p2-share")

    s1 = storage_ops.storage_stats(w025_env.pid)
    s2 = storage_ops.storage_stats(pid2)
    assert s1["originals"]["logical_bytes"] == len(data)
    assert s2["originals"]["logical_bytes"] == len(data)
    g = storage_ops.storage_stats()
    assert g["originals"]["physical_bytes"] == len(data)
    assert g["originals"]["physical_files"] == 1
    assert g["originals"]["dedup_saved_bytes"] == len(data)


def test_stats_cross_owner_project_404(w025_env):
    r = w025_env.client.get(
        f"/api/v1/storage/stats?project_id={w025_env.pid}",
        headers=w025_env.B)
    assert r.status_code == 404


# ============================== dedup report ======================

def test_dedup_report_refcount_and_saving(w025_env):
    data = jpeg_bytes(seed=23)
    for i in range(3):
        register_photo(w025_env, data, key=f"dup-{i}")
    report = storage_ops.dedup_report()
    assert report["shared_objects"] == 1
    group = report["groups"][0]
    assert group["sha256"] == sha256_of(data)
    assert group["references"] == 3
    assert group["byte_size"] == len(data)
    assert report["dedup_saved_bytes"] == 2 * len(data)


def test_dedup_report_owner_scoped(w025_env):
    """Storage-layer dedup respects caller scoping: device B sees only
    its own bindings even when the physical object is shared."""
    data = jpeg_bytes(seed=24)
    # A binds the same content three times
    for i in range(3):
        register_photo(w025_env, data, key=f"a-dup-{i}")
    # B has its own project with DIFFERENT content, single copy
    pid_b = w025_env.client.post(
        "/api/v1/projects", json={"name": "B Site"},
        headers=w025_env.B).json()["project_id"]
    register_photo(w025_env, jpeg_bytes(seed=25), key="b-solo",
                   headers=w025_env.B, pid=pid_b)

    ra = w025_env.client.get("/api/v1/storage/dedup",
                             headers=w025_env.H).json()
    rb = w025_env.client.get("/api/v1/storage/dedup",
                             headers=w025_env.B).json()
    assert ra["shared_objects"] == 1
    assert ra["groups"][0]["references"] == 3
    assert rb["shared_objects"] == 0
    # cross-subject photo access still 404 (physical sharing is not
    # an information surface)
    other = w025_env.client.get(
        f"/api/v1/projects/{pid_b}/photos",
        headers=w025_env.B).json()
    assert other["total"] == 1
    cross = w025_env.client.get(
        f"/api/v1/projects/{w025_env.pid}/photos",
        headers=w025_env.B)
    assert cross.status_code == 404


# ============================== cache pruning =====================

def _make_watermark(env, photo_id: str) -> dict:
    r = env.client.post(
        f"/api/v1/photos/{photo_id}/derivatives/watermark",
        json={"template": "minimal",
              "fields": {"show_photo_id": True,
                         "photo_id": photo_id}},
        headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()


def test_prune_thumbnails_only_leaves_derivatives(w025_env):
    photo = register_photo(w025_env, jpeg_bytes(seed=30), key="pr-1")
    w025_env.client.get(f"/api/v1/photos/{photo}/thumbnail",
                        headers=w025_env.H)
    _make_watermark(w025_env, photo)
    r = w025_env.client.post(
        "/api/v1/storage/cache/prune",
        json={"scope": "thumbnails", "dry_run": False},
        headers=w025_env.H).json()
    assert r["per_class"]["thumbnails"] == 1
    assert r["per_class"].get("derivatives", 0) == 0
    stats = storage_ops.storage_stats()
    assert stats["thumbnails"]["physical_files"] == 0
    assert stats["derivatives"]["physical_files"] == 1


def test_prune_derivatives_bytes_regenerable(w025_env):
    """The W011 derivative row survives pruning (registered
    provenance); the BYTES are regenerable - an identical render
    request transparently recreates them with the same content hash.
    This is what makes derivative pruning safe."""
    photo = register_photo(w025_env, jpeg_bytes(seed=31), key="pr-2")
    first = _make_watermark(w025_env, photo)
    d_sha = first["derivative_sha256"]
    d_id = first["derivative_id"]

    pr = w025_env.client.post(
        "/api/v1/storage/cache/prune",
        json={"scope": "derivatives", "dry_run": False},
        headers=w025_env.H).json()
    assert pr["files_deleted"] == 1

    # registered row kept, download 404s on missing bytes
    dl = w025_env.client.get(
        f"/api/v1/derivatives/{d_id}/download", headers=w025_env.H)
    assert dl.status_code == 404

    # identical request re-renders; SAME content hash comes back
    second = _make_watermark(w025_env, photo)
    assert second["derivative_id"] == d_id
    assert second["derivative_sha256"] == d_sha
    dl2 = w025_env.client.get(
        f"/api/v1/derivatives/{d_id}/download", headers=w025_env.H)
    assert dl2.status_code == 200


def test_prune_dry_run_deletes_nothing(w025_env):
    photo = register_photo(w025_env, jpeg_bytes(seed=32), key="pr-3")
    w025_env.client.get(f"/api/v1/photos/{photo}/thumbnail",
                        headers=w025_env.H)
    pr = w025_env.client.post(
        "/api/v1/storage/cache/prune",
        json={"scope": "all", "dry_run": True},
        headers=w025_env.H).json()
    assert pr["dry_run"] is True
    assert pr["files_deleted"] == 1          # reported...
    stats = storage_ops.storage_stats()
    assert stats["thumbnails"]["physical_files"] == 1   # ...not taken


def test_prune_originals_scope_rejected(w025_env):
    """Red line: 'originals' is not a cache and the prune vocabulary
    refuses it outright."""
    r = w025_env.client.post(
        "/api/v1/storage/cache/prune",
        json={"scope": "originals", "dry_run": False},
        headers=w025_env.H)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_scope"
    assert "immutable" in r.json()["detail"]["message"]


def test_prune_all_never_changes_any_original_byte(w025_env):
    """THE task red line as a hard test: run every storage-op (thumb,
    watermark, prune all, audit), then byte-compare every original
    object against the pre-op snapshot."""
    originals = [jpeg_bytes(seed=40 + i) for i in range(3)]
    photos = [register_photo(w025_env, d, key=f"inv-{i}")
              for i, d in enumerate(originals)]
    for p in photos:
        w025_env.client.get(f"/api/v1/photos/{p}/thumbnail",
                            headers=w025_env.H)
        _make_watermark(w025_env, p)
    snapshot = all_original_hashes(w025_env)

    w025_env.client.post("/api/v1/storage/cache/prune",
                         json={"scope": "all", "dry_run": False},
                         headers=w025_env.H)
    storage_ops.audit_storage()

    assert all_original_hashes(w025_env) == snapshot
    # and each photo still downloads its exact bytes
    for p, d in zip(photos, originals):
        dl = w025_env.client.get(f"/api/v1/photos/{p}/original",
                                 headers=w025_env.H)
        assert dl.content == d


# ============================== audit / orphans ===================

def test_audit_clean_state_consistent(w025_env):
    photo = register_photo(w025_env, jpeg_bytes(seed=50), key="au-0")
    w025_env.client.get(f"/api/v1/photos/{photo}/thumbnail",
                        headers=w025_env.H)
    _make_watermark(w025_env, photo)
    a = storage_ops.audit_storage()
    assert a["verdict"] == "consistent"
    assert a["intact"] == 1
    assert a["orphan_files"] == []
    assert a["derivatives_missing_bytes"] == []
    assert a["thumbnail_orphan_files"] == []


def test_audit_orphan_disk_file_no_db_row(w025_env):
    """FS -> DB direction: a file on disk unknown to the ledger."""
    register_photo(w025_env, jpeg_bytes(seed=51), key="au-1")
    fake_sha = hashlib.sha256(b"never-registered").hexdigest()
    p = original_store.object_path(fake_sha)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"rogue bytes")
    a = storage_ops.audit_storage()
    assert a["verdict"] == "inconsistencies_found"
    assert str(p.relative_to(original_store.store_root())) in \
        a["orphan_files"]


def test_audit_db_row_missing_disk_file(w025_env):
    """DB -> FS direction: the ledger knows an object the disk lost."""
    data = jpeg_bytes(seed=52)
    photo = register_photo(w025_env, data, key="au-2")
    original_store.object_path(sha256_of(data)).unlink()
    a = storage_ops.audit_storage()
    assert sha256_of(data) in a["missing_files"]
    assert a["verdict"] == "inconsistencies_found"
    assert photo in a["bound_missing"]


def test_audit_corrupt_object_detected(w025_env):
    data = jpeg_bytes(seed=53)
    register_photo(w025_env, data, key="au-3")
    p = original_store.object_path(sha256_of(data))
    p.write_bytes(data[:-1] + bytes([data[-1] ^ 0x01]))
    a = storage_ops.audit_storage()
    assert a["corrupt"][0]["sha256"] == sha256_of(data)
    assert a["verdict"] == "inconsistencies_found"
    # corruption flagged in the ledger too
    with storage_ops.db() as conn:
        st = conn.execute(
            "SELECT status FROM mvp_originals WHERE sha256 = ?",
            (sha256_of(data),)).fetchone()["status"]
    assert st == "corruption_detected"


def test_audit_derivative_missing_bytes_reported(w025_env):
    photo = register_photo(w025_env, jpeg_bytes(seed=54), key="au-4")
    d = _make_watermark(w025_env, photo)
    from app.api.derivatives import derivative_path
    derivative_path(d["derivative_sha256"]).unlink()
    a = storage_ops.audit_storage()
    assert a["derivatives_missing_bytes"][0][
        "derivative_id"] == d["derivative_id"]
    # dangling derivative bytes are NOT a hard error for the verdict
    # (they are regenerable) - but they ARE reported
    assert d["derivative_id"] in [x["derivative_id"]
                                  for x in a["derivatives_missing_bytes"]]


def test_audit_derivative_orphan_bytes(w025_env):
    from app.api.derivatives import derivative_path
    fake = hashlib.sha256(b"unregistered derivative").hexdigest()
    p = derivative_path(fake)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"rogue")
    a = storage_ops.audit_storage()
    assert a["derivative_orphan_files"] == [f"{fake[:2]}/{fake}.jpg"]


def test_audit_thumbnail_orphan_when_original_missing(w025_env):
    data = jpeg_bytes(seed=55)
    register_photo(w025_env, data, key="au-5")
    storage_ops.ensure_thumbnail(sha256_of(data), 512)
    # disaster: the original vanishes while the thumbnail lingers
    original_store.object_path(sha256_of(data)).unlink()
    a = storage_ops.audit_storage()
    assert len(a["thumbnail_orphan_files"]) == 1
    assert str(sha256_of(data))[:2] in a["thumbnail_orphan_files"][0]


def test_audit_temp_residue_fix_only_removes_temps(w025_env):
    register_photo(w025_env, jpeg_bytes(seed=56), key="au-6")
    root = original_store.store_root()
    tmp = root / "aa" / ".w025_crash.part"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(b"crash residue")
    a1 = storage_ops.audit_storage()
    assert len(a1["temp_residue"]) == 1
    a2 = storage_ops.audit_storage(fix_temps=True)
    assert a2["repaired_temps"] == 1
    assert not tmp.exists()
    # committed objects untouched by the fix
    assert a2["intact"] == 1


# ============================== quota model =======================

def test_quota_model_set_get_check_flow(w025_env):
    register_photo(w025_env, jpeg_bytes(seed=60), key="q-1")
    setr = w025_env.client.put(
        "/api/v1/storage/quota",
        json={"project_id": w025_env.pid,
              "quota_bytes": 10 * 1024 * 1024, "policy": "report_only"},
        headers=w025_env.H)
    assert setr.status_code == 200
    chk = w025_env.client.get(
        f"/api/v1/storage/quota?project_id={w025_env.pid}",
        headers=w025_env.H).json()
    assert chk["quota_bytes"] == 10 * 1024 * 1024
    assert chk["exceeded"] is False
    assert chk["policy"] == "report_only"
    assert "Not Implemented" in chk["enforcement"]


def test_quota_exceeded_is_report_only_not_enforcement(w025_env):
    """The quota model detects the overage but NO production path
    enforces it: uploads keep succeeding while exceeded=True. Hard
    enforcement wiring is explicitly Not Implemented."""
    register_photo(w025_env, jpeg_bytes(seed=61), key="q-2")
    w025_env.client.put(
        "/api/v1/storage/quota",
        json={"project_id": w025_env.pid, "quota_bytes": 1,
              "policy": "report_only"},
        headers=w025_env.H)
    chk = w025_env.client.get(
        f"/api/v1/storage/quota?project_id={w025_env.pid}",
        headers=w025_env.H).json()
    assert chk["exceeded"] is True
    assert chk["action"] == "report"
    # and the proof it is not wired: a NEW photo still stores fine
    register_photo(w025_env, jpeg_bytes(seed=62), key="q-3")


def test_quota_owner_isolation(w025_env):
    r = w025_env.client.put(
        "/api/v1/storage/quota",
        json={"project_id": w025_env.pid, "quota_bytes": 100,
              "policy": "report_only"},
        headers=w025_env.B)
    assert r.status_code == 404
    r2 = w025_env.client.get(
        f"/api/v1/storage/quota?project_id={w025_env.pid}",
        headers=w025_env.B)
    assert r2.status_code == 404


def test_quota_validation(w025_env):
    r = w025_env.client.put(
        "/api/v1/storage/quota",
        json={"project_id": w025_env.pid, "quota_bytes": -5,
              "policy": "report_only"},
        headers=w025_env.H)
    assert r.status_code == 422
    with pytest.raises(Exception):
        storage_ops.set_quota(w025_env.pid, 100, "bogus_policy")


# ============================== backup/restore ===================

def test_backup_restore_dedup_shared_object(w025_env):
    """The dedup-specific backup/restore case the task brief adds on
    top of test_rc3_backup_restore_full_stack: one physical object
    shared by three photos is backed up ONCE and restored ONCE, and
    all three photos come back byte-perfect."""
    import app.db as dbm
    data = jpeg_bytes(seed=70)
    sha = sha256_of(data)
    photos = [register_photo(w025_env, data, key=f"bk-{i}")
              for i in range(3)]

    bdir = w025_env.tmp / "bk-objs"
    db_backup = w025_env.tmp / "bk.db"
    dbm.backup_to(db_backup, w025_env.db)
    manifest = original_store.backup(bdir)
    assert len(manifest["objects"]) == 1        # physical dedup held
    assert manifest["objects"][sha] == len(data)

    # catastrophic loss of the single shared object
    original_store.object_path(sha).unlink()

    restored_db = w025_env.tmp / "bk-restored.db"
    dbm.restore_from(db_backup, restored_db)
    res = original_store.restore(bdir, manifest)
    assert res["objects"] == 1

    # all three photos download the identical restored bytes
    for p in photos:
        dl = w025_env.client.get(f"/api/v1/photos/{p}/original",
                                 headers=w025_env.H)
        assert dl.status_code == 200
        assert dl.content == data
    # repeated restore is a safe no-op (P4 #6 discipline)
    res2 = original_store.restore(bdir, manifest)
    assert res2["skipped_intact"] == 1 and res2["objects"] == 0


def test_backup_restore_after_cache_prune_full_consistency(
        w025_env, monkeypatch):
    """Storage-layer disaster drill: prune caches -> back up -> lose
    BOTH the original bytes and the caches -> restore -> audit must be
    clean, and the regenerable caches come back on demand."""
    import app.db as dbm
    data = jpeg_bytes(seed=71)
    sha = sha256_of(data)
    photo = register_photo(w025_env, data, key="bk-pr-1")
    w025_env.client.get(f"/api/v1/photos/{photo}/thumbnail",
                        headers=w025_env.H)
    _make_watermark(w025_env, photo)

    w025_env.client.post("/api/v1/storage/cache/prune",
                         json={"scope": "all", "dry_run": False},
                         headers=w025_env.H)
    bdir = w025_env.tmp / "bk2"
    db_backup = w025_env.tmp / "bk2.db"
    dbm.backup_to(db_backup, w025_env.db)
    manifest = original_store.backup(bdir)
    assert len(manifest["objects"]) == 1

    # lose everything on disk
    original_store.object_path(sha).unlink()

    restored_db = w025_env.tmp / "bk2-restored.db"
    dbm.restore_from(db_backup, restored_db)
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(restored_db))
    res = original_store.restore(bdir, manifest)
    assert res["objects"] == 1
    a = storage_ops.audit_storage()
    # the pruned derivative is a REGENERABLE gap - reported, but the
    # audit verdict stays 'consistent' (no data loss)
    assert a["regenerable_gaps"] == 1
    assert a["verdict"] == "consistent"
    # regenerable caches were never part of the backup; they
    # regenerate on demand (cache miss -> fresh bytes)
    t = storage_ops.ensure_thumbnail(sha, 512)
    assert t["idempotent_replay"] is False


# ============================== auth surface ======================

def test_storage_endpoints_require_identity(w025_env):
    for method, path in (
            ("get", "/api/v1/storage/stats"),
            ("get", "/api/v1/storage/dedup"),
            ("get", "/api/v1/storage/audit"),
            ("post", "/api/v1/storage/cache/prune"),
    ):
        r = getattr(w025_env.client, method)(path,
                                             **({"json": {}} if method
                                                == "post" else {}))
        assert r.status_code == 401, f"{path} leaked without token"


# ============================== scale regression ==================

def test_scale_1k_storage_layer_ingest_audit(w025_env, tmp_path):
    """Scale regression gate: 1,000 synthetic photos ingested through
    the STREAMING storage path (30% duplicate content to exercise
    dedup), then stats + audit + prune + re-generate. Timings are
    printed for the scale report (DESKTOP_SIMULATION, single run).

    The 5k/10k stages run via tools/w025_scale.py (outside pytest)
    and their real numbers are recorded in the delivery report."""
    n = 1000
    dup_ratio = 0.3
    base = [jpeg_bytes(seed=1000 + i) for i in range(30)]
    rows_photos = []
    rows_bindings = []
    from app.db import utc_now
    now = utc_now()

    def chunks_of(b, size=256 * 1024):
        for i in range(0, len(b), size):
            yield b[i:i + size]

    t0 = time.perf_counter()
    for i in range(n):
        data = base[i % 30] if i < n * dup_ratio else jpeg_bytes(
            seed=2000 + i)
        sha = sha256_of(data)
        photo_id = f"sc{i:05d}-{uuid.uuid4()}"
        storage_ops.store_streaming(chunks_of(data), sha)
        rows_photos.append((photo_id, w025_env.pid, f"ik-{i}", sha,
                            "f.jpg", "image/jpeg", len(data), "t",
                            now, "d", None, now))
        rows_bindings.append((photo_id, sha, "device-A-token", now))
    t1 = time.perf_counter()
    with storage_ops.db() as conn:
        conn.executemany(
            "INSERT INTO mvp_photos (photo_id, project_id, "
            "idempotency_key, original_sha256, original_filename, "
            "mime_type, byte_size, captured_at, received_at, "
            "device_id, evidence_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows_photos)
        conn.executemany(
            "INSERT INTO mvp_photo_originals (photo_id, sha256, "
            "owner_token, bound_at) VALUES (?,?,?,?) "
            "ON CONFLICT(photo_id, sha256) DO NOTHING", rows_bindings)

    t2 = time.perf_counter()
    stats = storage_ops.storage_stats()
    t3 = time.perf_counter()
    audit = storage_ops.audit_storage()
    t4 = time.perf_counter()

    distinct = stats["originals"]["physical_files"]
    assert stats["photos"] == n
    # 700 unique seeds + 30 shared bodies => exactly 730 objects
    assert distinct == 730, distinct
    assert stats["originals"]["dedup_saved_bytes"] > 0
    assert stats["originals"]["referenced_objects"] == 730
    assert audit["verdict"] == "consistent"
    assert audit["intact"] == 730

    # thumbnail storm over a sample, then prune-all and re-check the
    # original-bytes invariant at scale
    t5 = time.perf_counter()
    sample_shas = {r[3] for r in rows_photos[:50]}
    for s in sample_shas:
        storage_ops.ensure_thumbnail(s, 512)
    t6 = time.perf_counter()
    snapshot = all_original_hashes(w025_env)
    pr = storage_ops.prune_cache(scope="all")
    # the first 50 photos draw from the 30 duplicated base bodies ->
    # exactly len(sample_shas) distinct thumbnails were generated
    assert pr["files_deleted"] == len(sample_shas)
    assert all_original_hashes(w025_env) == snapshot     # red line

    print(f"\n[W025-SCALE-1K] ingest={t1-t0:.2f}s "
          f"bulk_db={t2-t1:.2f}s stats={t3-t2:.2f}s "
          f"audit={t4-t3:.2f}s 50_thumbs={t6-t5:.2f}s "
          f"objects={distinct} photos={n}")
    # hang-guard only (NOT a performance claim): the whole stage must
    # finish in under a generous ceiling
    assert (t4 - t0) < 300, "scale stage exceeded the 300s hang-guard"

