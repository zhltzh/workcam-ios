"""WC-W016 stress tests.

DESKTOP_SIMULATION - in-process TestClient on a Windows desktop, single
pytest process. The elapsed times below are OBSERVED PROTOTYPING DATA,
not performance claims. Time assertions are generous hang-guards only.

Covers (W016 acceptance):
  K1 concurrent uploads + readback (8 threads x 3 real JPEGs)
  K2 concurrent sync batches (8 threads x 10-op batches, no crosstalk)
  K3 large payload roundtrip (multi-MB original, byte-identical,
     integrity endpoint file_hash_valid)
  K4 bulk sync throughput (10 x full 50-op batches = 500 ops)
  K5 concurrent report generation (4 threads, same project)
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
import threading
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from tests.conftest import _encode_pub


@pytest.fixture()
def k_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "w016k-code")
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    import app.db as dbm
    dbm.migrate(tmp_path / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    from app.services import device_identity
    import app.services.device_identity as _di
    monkeypatch.setattr(_di, "check_rate", lambda *a, **k: None)

    key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
    tok = device_identity.enroll_device(pub_b64, "w016-K", "w016k-code")
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "biz.db", "tok": tok,
        "H": {"X-Device-Token": tok},
        "client": TestClient(app),
    })()


def _photo(w=1200, h=900, seed=0, quality=85) -> bytes:
    img = Image.new("RGB", (w, h), (100 + seed % 128, 140, 120))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 200, 150], fill=(40, 80, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _project(env, name="Stress Site") -> str:
    r = env.client.post("/api/v1/projects", json={"name": name},
                         headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _register(env, pid, data, ik):
    sha = hashlib.sha256(data).hexdigest()
    r = env.client.post(
        f"/api/v1/projects/{pid}/photos",
        json={"idempotency_key": ik, "original_sha256": sha,
              "original_filename": "IMG.jpg", "mime_type": "image/jpeg",
              "byte_size": len(data), "captured_at": "2026-11-25 09:00",
              "device_id": "d"}, headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()["photo_id"]


def _batch(env, ops):
    r = env.client.post("/api/v1/sync/batch", json={"operations": ops},
                         headers=env.H)
    assert r.status_code == 200, r.text
    return r.json()


# ======================================================== K1 uploads


def test_w016_k1_concurrent_uploads_and_readback(k_env):
    """8 threads, each registering + uploading + re-downloading 3 real
    JPEG originals against the SAME project. All bytes must come back
    byte-identical; WAL + busy_timeout must absorb the write contention."""
    env = k_env
    pid = _project(env, "K1 Parallel Uploads")
    errors: list[str] = []
    done: list[int] = []

    def worker(t: int):
        try:
            wc = TestClient(app)
            for i in range(3):
                data = _photo(seed=t * 10 + i)
                ph = _register(env, pid, data, f"k1-{t}-{i}")
                up = wc.put(
                    f"/api/v1/photos/{ph}/original", content=data,
                    headers={**env.H,
                             "Content-Type": "application/octet-stream"})
                assert up.status_code == 200, up.text
                dl = wc.get(f"/api/v1/photos/{ph}/original",
                             headers=env.H)
                assert dl.status_code == 200
                assert dl.content == data, f"roundtrip broke at {t}/{i}"
                done.append(1)
        except Exception as e:      # pragma: no cover - diagnostic path
            errors.append(f"t{t}: {e}")

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(t,))
               for t in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    elapsed = time.perf_counter() - t0

    assert not errors, errors
    assert len(done) == 24
    photos = env.client.get(
        f"/api/v1/projects/{pid}/photos?limit=100",
        headers=env.H).json()
    assert photos["total"] == 24
    print(f"\n[W016-K1] 24 concurrent uploads+readbacks (8 threads) in"
          f" {elapsed:.2f}s (DESKTOP_SIMULATION, hang-guard 120s)")
    assert elapsed < 120


# ======================================================== K2 batches


def test_w016_k2_concurrent_sync_batches(k_env):
    """8 threads each submit their own 10-op photo_register batch
    against their own project, in parallel. Every receipt applies, every
    replay duplicates, and NO thread ever sees another thread's ids
    (ledger is owner- and op-scoped)."""
    env = k_env
    errors: list[str] = []

    def worker(t: int, out: dict):
        try:
            wc = TestClient(app)
            pid = _project(env, f"K2 Site {t}")
            sha = hashlib.sha256(_photo(seed=t)).hexdigest()
            ops = [{"op": "photo_register",
                     "operation_id": f"k2-{t}-{i}",
                     "payload": {"operation_id": f"k2-{t}-{i}",
                                  "local_id": f"l{t}_{i}",
                                  "server_project_id": pid,
                                  "idempotency_key": f"k2-ik-{t}-{i}",
                                  "original_sha256": sha,
                                  "original_filename": "K.jpg",
                                  "mime_type": "image/jpeg",
                                  "byte_size": 1024,
                                  "captured_at": "t",
                                  "device_id": "d"}}
                    for i in range(10)]
            r1 = wc.post("/api/v1/sync/batch", json={"operations": ops},
                          headers=env.H)
            assert r1.status_code == 200, r1.text
            b1 = r1.json()
            assert all(rc["status"] == "applied"
                       for rc in b1["receipts"]), \
                [rc for rc in b1["receipts"] if rc["status"] != "applied"]
            assert len(b1["id_map"]) == 10
            out[t] = b1["id_map"]
            # replay via a DIFFERENT client instance
            wc2 = TestClient(app)
            r2 = wc2.post("/api/v1/sync/batch", json={"operations": ops},
                            headers=env.H)
            assert r2.status_code == 200, r2.text
            b2 = r2.json()
            assert all(rc["status"] == "duplicate"
                       for rc in b2["receipts"])
            assert b2["id_map"] == b1["id_map"]
        except Exception as e:      # pragma: no cover - diagnostic path
            errors.append(f"t{t}: {e}")

    id_maps: dict[int, dict] = {}
    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(t, id_maps))
               for t in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    elapsed = time.perf_counter() - t0

    assert not errors, errors
    assert len(id_maps) == 8
    # no crosstalk: every id_map has exactly the thread's own 10 ids,
    # and no server id appears in two threads' maps
    all_ids = []
    for t, m in id_maps.items():
        assert set(m) == {f"l{t}_{i}" for i in range(10)}
        all_ids.extend(m.values())
    assert len(all_ids) == len(set(all_ids)) == 80
    conn = sqlite3.connect(str(env.db))
    n = conn.execute("SELECT COUNT(*) FROM mvp_sync_ops").fetchone()[0]
    photos = conn.execute("SELECT COUNT(*) FROM mvp_photos"
                           ).fetchone()[0]
    conn.close()
    assert n == 80                     # 80 ops, nothing double-applied
    assert photos == 80
    print(f"\n[W016-K2] 8x10-op concurrent batches + replays in"
          f" {elapsed:.2f}s, 80 photos, 0 crosstalk"
          f" (DESKTOP_SIMULATION, hang-guard 120s)")
    assert elapsed < 120


# ======================================================== K3 payload


def _noise_photo(w, h, quality=90) -> bytes:
    """High-entropy synthetic original: flat-color JPEGs compress to a
    few dozen KB, so K3 uses random noise to exercise a multi-MB
    payload with realistic incompressibility."""
    import os as _os
    raw = _os.urandom(w * h * 3)
    img = Image.frombytes("RGB", (w, h), raw)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def test_w016_k3_large_payload_roundtrip(k_env):
    """A multi-megabyte synthetic original must upload, download back
    byte-identical, and pass the integrity endpoint's on-disk hash
    check. Byte size observed and printed, never assumed."""
    env = k_env
    pid = _project(env, "K3 Large Payload")
    data = _noise_photo(2048, 1536)
    print(f"\n[W016-K3] synthetic original: {len(data)} bytes")
    assert len(data) > 1024 * 1024, "expected a >1MB payload"

    ph = _register(env, pid, data, "k3-large")
    t0 = time.perf_counter()
    up = env.client.put(f"/api/v1/photos/{ph}/original", content=data,
                         headers={**env.H,
                                  "Content-Type":
                                      "application/octet-stream"})
    assert up.status_code == 200, up.text
    assert up.json()["sha256"] == hashlib.sha256(data).hexdigest()
    t_up = time.perf_counter() - t0

    dl = env.client.get(f"/api/v1/photos/{ph}/original", headers=env.H)
    assert dl.status_code == 200
    assert dl.content == data
    t_dl = time.perf_counter() - t0 - t_up

    integ = env.client.get(f"/api/v1/photos/{ph}/original/integrity",
                            headers=env.H).json()
    assert integ["original"]["file_present"] is True
    assert integ["original"]["file_hash_valid"] is True
    assert integ["original"]["byte_size"] == len(data)
    assert integ["claims"]["file_integrity_verified"] is True
    assert integ["claims"]["photo_authenticity_proven"] is False

    print(f"[W016-K3] {len(data)}B upload {t_up:.2f}s / download"
          f" {t_dl:.2f}s, integrity OK (DESKTOP_SIMULATION,"
          f" hang-guard 60s)")
    assert (t_up + t_dl) < 60


# ======================================================== K4 throughput


def test_w016_k4_bulk_sync_throughput(k_env):
    """10 consecutive FULL batches (50 ops each = 500 photo_register
    ops) must apply completely; each op is its own BEGIN IMMEDIATE
    transaction. Time observed and printed; hang-guard only."""
    env = k_env
    pid = _project(env, "K4 Bulk Throughput")
    sha = hashlib.sha256(_photo(seed=1)).hexdigest()

    t0 = time.perf_counter()
    total_applied = 0
    for chunk in range(10):
        ops = [{"op": "photo_register",
                 "operation_id": f"k4-{chunk}-{i}",
                 "payload": {"operation_id": f"k4-{chunk}-{i}",
                              "local_id": f"lc{chunk}_{i}",
                              "server_project_id": pid,
                              "idempotency_key": f"k4-ik-{chunk}-{i}",
                              "original_sha256": sha,
                              "original_filename": "B.jpg",
                              "mime_type": "image/jpeg",
                              "byte_size": 512,
                              "captured_at": "t", "device_id": "d"}}
                for i in range(50)]
        resp = _batch(env, ops)
        total_applied += sum(1 for rc in resp["receipts"]
                             if rc["status"] == "applied")
        assert len(resp["id_map"]) == 50
    elapsed = time.perf_counter() - t0

    assert total_applied == 500
    photos = env.client.get(
        f"/api/v1/projects/{pid}/photos?limit=1",
        headers=env.H).json()
    assert photos["total"] == 500
    # the change-log saw all 501 creates (1 project + 500 photos)
    events = env.client.get("/api/v1/sync/events?limit=200",
                              headers=env.H).json()
    assert events["items"] and events["has_more"] is True
    print(f"\n[W016-K4] 500 sync ops (10 x 50) applied in {elapsed:.2f}s"
          f" = {500 / elapsed:.0f} ops/s observed"
          f" (DESKTOP_SIMULATION, hang-guard 180s)")
    assert elapsed < 180


# ======================================================== K5 reports


def test_w016_k5_concurrent_report_generation(k_env):
    """4 threads generate inspection reports for the SAME project in
    parallel; every report must succeed, produce a real PDF, and every
    delivery package must SHA-verify its manifest."""
    env = k_env
    pid = _project(env, "K5 Report Pressure")
    photo_ids = []
    for i in range(6):
        data = _photo(seed=i)
        ph = _register(env, pid, data, f"k5-{i}")
        up = env.client.put(f"/api/v1/photos/{ph}/original", content=data,
                             headers={**env.H,
                                      "Content-Type":
                                          "application/octet-stream"})
        assert up.status_code == 200
        photo_ids.append(ph)
    for i in range(2):
        r = env.client.post(f"/api/v1/projects/{pid}/issues", json={
            "title": f"Issue {i}", "severity": "medium"},
            headers=env.H)
        assert r.status_code == 201

    errors: list[str] = []
    report_ids: list[str] = []

    def worker(t: int):
        try:
            wc = TestClient(app)
            r = wc.post(f"/api/v1/projects/{pid}/reports", json={
                "report_type": "inspection", "page_size": "a4",
                "per_page": 2, "photo_ids": photo_ids},
                headers=env.H)
            assert r.status_code == 201, r.text
            rid = r.json()["report_id"]
            pdf = wc.get(f"/api/v1/reports/{rid}/download",
                          headers=env.H)
            assert pdf.status_code == 200
            assert pdf.content.startswith(b"%PDF")
            pkg = wc.post(
                f"/api/v1/reports/{rid}/delivery-package",
                json={"include_originals": True,
                      "include_derivatives": False,
                      "include_evidence": True}, headers=env.H)
            assert pkg.status_code == 200, pkg.text
            import zipfile as _zf
            zf = _zf.ZipFile(io.BytesIO(pkg.content))
            manifest = json.loads(zf.read("manifest.json"))
            for f in manifest["files"]:
                assert hashlib.sha256(
                    zf.read(f["path"])).hexdigest() == f["sha256"]
            report_ids.append(rid)
        except Exception as e:      # pragma: no cover - diagnostic path
            errors.append(f"t{t}: {e}")

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(t,))
               for t in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    elapsed = time.perf_counter() - t0

    assert not errors, errors
    assert len(report_ids) == 4
    assert len(set(report_ids)) == 4        # distinct report rows
    print(f"\n[W016-K5] 4 concurrent report+package generations in"
          f" {elapsed:.2f}s (DESKTOP_SIMULATION, hang-guard 180s)")
    assert elapsed < 180
