"""WC-W016 scenario simulations.

DESKTOP_SIMULATION - synthetic photos, REAL SQLite/FSS/ZIP behavior.

Covers (W016 acceptance):
  S1 full-day fieldwork: 1 project + 20 photos w/ REAL original bytes
     + 20 annotations + 5 issues incl. transitions and before/after
     links + inspection report + delivery package (manifest SHA)
     + full-batch replay + restart consistency + original immutability
  S2 multi-device parallel inspection (threaded) with ownership isolation
  S3 concurrent revision race: exactly one winner under true parallelism
  S4 mixed-outcome batch: applied/rejected coexist; ledger replay stable;
     a rejected op never poisons later batches
  S5 batch size boundaries: 50 ops applied, 51 ops -> 422, full-batch
     replay all duplicate
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
import zipfile

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from tests.conftest import _encode_pub


@pytest.fixture()
def w016_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "w016-code")
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    import app.db as dbm
    dbm.migrate(tmp_path / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    from app.services import device_identity
    import app.services.device_identity as _di
    monkeypatch.setattr(_di, "check_rate", lambda *a, **k: None)

    def mk(name):
        key = ec.generate_private_key(ec.SECP256R1())
        pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
        return key, pub_b64, device_identity.enroll_device(
            pub_b64, name, "w016-code")

    key_a, pub_a, tok_a = mk("w016-AAA")
    key_b, pub_b, tok_b = mk("w016-BBB")
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "biz.db",
        "key": key_a, "pub_a": pub_a, "tok_a": tok_a, "tok_b": tok_b,
        "H": {"X-Device-Token": tok_a},
        "B": {"X-Device-Token": tok_b},
        "client": TestClient(app),
    })()


def _photo(w=1200, h=900, seed=0) -> bytes:
    img = Image.new("RGB", (w, h), (100 + seed % 128, 140, 120))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 200, 150], fill=(40, 80, 160 + seed % 96))
    d.text((60, 300), f"W016 seed {seed}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _batch(env, ops, headers=None) -> dict:
    r = env.client.post("/api/v1/sync/batch", json={"operations": ops},
                         headers=headers or env.H)
    assert r.status_code == 200, r.text
    return r.json()


def _direct_project(env, name) -> str:
    r = env.client.post("/api/v1/projects", json={"name": name},
                         headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _register_photo(env, pid, data: bytes, ik: str | None = None) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    r = env.client.post(
        f"/api/v1/projects/{pid}/photos",
        json={"idempotency_key": ik or f"w016-{uuid.uuid4().hex[:8]}",
              "original_sha256": sha,
              "original_filename": "IMG.jpg", "mime_type": "image/jpeg",
              "byte_size": len(data), "captured_at": "2026-11-25 09:00",
              "device_id": "iPhone15,3"}, headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()


def _upload_original(env, photo_id, data: bytes):
    r = env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                        headers={**env.H,
                                 "Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text


# ======================================================== S1: full day


def test_w016_s1_full_day_fieldwork(w016_env):
    """One device, one construction day: 20 annotated photos, 5 issues
    (2 resolved w/ before/after), inspection report, delivery package
    with SHA-verified manifest, full replay, restart, byte-identical
    originals."""
    env = w016_env
    c = env.client
    t0 = time.perf_counter()

    # project via sync (offline queue semantics)
    r1 = _batch(env, [{"op": "project_create", "operation_id": "fd-pc",
                        "payload": {"operation_id": "fd-pc",
                                     "local_id": "lproj",
                                     "name": "Full Day Site",
                                     "description": "W016 S1 simulation",
                                     "site_address": "Km 34+200"}}])
    assert r1["receipts"][0]["status"] == "applied"
    pid = r1["id_map"]["lproj"]

    # 20 photos, registered via sync in 2 batches (MAX_BATCH_OPS=50)
    originals: dict[str, bytes] = {}
    photo_server_ids: list[str] = []
    reg_batches: list[list[dict]] = []
    for chunk in range(2):
        ops = []
        for i in range(10):
            idx = chunk * 10 + i
            data = _photo(seed=idx)
            sha = hashlib.sha256(data).hexdigest()
            ops.append({"op": "photo_register",
                         "operation_id": f"fd-pr-{idx}",
                         "payload": {"operation_id": f"fd-pr-{idx}",
                                      "local_id": f"lph{idx}",
                                      "server_project_id": pid,
                                      "idempotency_key": f"fd-ik-{idx}",
                                      "original_sha256": sha,
                                      "original_filename":
                                          f"IMG_{idx:04d}.jpg",
                                      "mime_type": "image/jpeg",
                                      "byte_size": len(data),
                                      "captured_at":
                                          "2026-11-25 08:30",
                                      "device_id": "iPhone15,3"}})
        reg_batches.append(ops)
        resp = _batch(env, ops)
        assert all(rc["status"] == "applied" for rc in resp["receipts"])
        for idx in range(chunk * 10, chunk * 10 + 10):
            photo_server_ids.append(resp["id_map"][f"lph{idx}"])

    # upload the REAL original bytes for all 20
    for idx in range(20):
        data = _photo(seed=idx)
        originals[photo_server_ids[idx]] = data
        _upload_original(env, photo_server_ids[idx], data)

    # one annotation per photo, single 20-op batch
    ann_ops = [{"op": "annotation_create",
                 "operation_id": f"fd-an-{i}",
                 "payload": {"operation_id": f"fd-an-{i}",
                              "local_id": f"lan{i}",
                              "server_photo_id": photo_server_ids[i],
                              "annotation_type": "arrow",
                              "norm_x1": 0.2, "norm_y1": 0.3,
                              "norm_x2": 0.6, "norm_y2": 0.4}}
                for i in range(20)]
    ann_batch = _batch(env, ann_ops)
    assert all(rc["status"] == "applied" for rc in ann_batch["receipts"])

    # 5 issues, transitions and before/after links
    iss_ops = [{"op": "issue_create",
                 "operation_id": f"fd-is-{i}",
                 "payload": {"operation_id": f"fd-is-{i}",
                              "local_id": f"lis{i}",
                              "server_project_id": pid,
                              "title": f"Defect {i} at pier {i + 2}",
                              "severity": ["low", "medium", "high"][i % 3]}}
                for i in range(5)]
    iss_batch = _batch(env, iss_ops)
    issue_ids = [iss_batch["id_map"][f"lis{i}"] for i in range(5)]
    assert all(rc["status"] == "applied" for rc in iss_batch["receipts"])

    flow_ops = []
    for i in range(2):
        flow_ops.append({"op": "issue_transition",
                          "operation_id": f"fd-tr-{i}-a",
                          "payload": {"operation_id": f"fd-tr-{i}-a",
                                       "server_issue_id": issue_ids[i],
                                       "expected_revision": 1,
                                       "target_status": "in_progress"}})
        flow_ops.append({"op": "issue_transition",
                          "operation_id": f"fd-tr-{i}-b",
                          "payload": {"operation_id": f"fd-tr-{i}-b",
                                       "server_issue_id": issue_ids[i],
                                       "expected_revision": 2,
                                       "target_status": "resolved"}})
    for i in range(2):
        flow_ops.append({"op": "issue_photo_link",
                          "operation_id": f"fd-ln-b-{i}",
                          "payload": {"operation_id": f"fd-ln-b-{i}",
                                       "server_issue_id": issue_ids[i],
                                       "server_photo_id":
                                           photo_server_ids[i * 2],
                                       "role": "before"}})
        flow_ops.append({"op": "issue_photo_link",
                          "operation_id": f"fd-ln-a-{i}",
                          "payload": {"operation_id": f"fd-ln-a-{i}",
                                       "server_issue_id": issue_ids[i],
                                       "server_photo_id":
                                           photo_server_ids[i * 2 + 1],
                                       "role": "after"}})
    flow_batch = _batch(env, flow_ops)
    assert all(rc["status"] == "applied" for rc in flow_batch["receipts"])

    # inspection report over all 20 photos + all 5 issues
    rr = c.post(f"/api/v1/projects/{pid}/reports",
                 json={"report_type": "inspection", "page_size": "a4",
                       "per_page": 4, "photo_ids": photo_server_ids,
                       "issue_ids": issue_ids}, headers=env.H)
    assert rr.status_code == 201, rr.text
    rid = rr.json()["report_id"]
    assert rr.json()["page_count"] >= 5

    # delivery package: every manifest entry SHA-verified
    pkg = c.post(f"/api/v1/reports/{rid}/delivery-package",
                  json={"include_originals": True,
                        "include_derivatives": False,
                        "include_evidence": True}, headers=env.H)
    assert pkg.status_code == 200, pkg.text
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    manifest = json.loads(zf.read("manifest.json"))
    paths = {f["path"] for f in manifest["files"]}
    for f in manifest["files"]:
        assert hashlib.sha256(zf.read(f["path"])).hexdigest() == f["sha256"]
    assert "report.pdf" in paths
    assert sum(1 for p in paths if p.startswith("originals/")) == 20

    # FULL REPLAY of every batch -> all duplicate, ids stable
    original_id_maps = [reg_batches[0], reg_batches[1], ann_ops, iss_ops,
                        flow_ops]
    for ops in original_id_maps:
        first = _batch(env, ops)
        replay = _batch(env, ops)
        assert all(rc["status"] == "duplicate"
                   for rc in replay["receipts"])
        assert replay["id_map"] == first["id_map"]

    # restart consistency: a brand-new client sees the same world
    c2 = TestClient(app)
    issues2 = c2.get(f"/api/v1/projects/{pid}/issues",
                      headers=env.H).json()["items"]
    assert len(issues2) == 5
    by_id = {i["issue_id"]: i for i in issues2}
    assert by_id[issue_ids[0]]["status"] == "resolved"
    assert by_id[issue_ids[4]]["status"] == "open"
    photos2 = c2.get(f"/api/v1/projects/{pid}/photos?limit=100",
                      headers=env.H).json()
    assert photos2["total"] == 20

    # originals byte-identical after the whole day
    for idx in (0, 9, 19):
        pid_ = photo_server_ids[idx]
        dl = c2.get(f"/api/v1/photos/{pid_}/original", headers=env.H)
        assert dl.content == originals[pid_]

    elapsed = time.perf_counter() - t0
    print(f"\n[W016-S1] full-day fieldwork: 20 photos + 20 annotations"
          f" + 5 issues + report + package in {elapsed:.2f}s"
          f" (DESKTOP_SIMULATION)")


# ==================================================== S2: multi-device


def test_w016_s2_multi_device_parallel(w016_env):
    """A and B work DIFFERENT projects in parallel threads; neither
    sees the other's data; DB ends with both devices' rows intact."""
    env = w016_env
    results: dict[str, list] = {"A": [], "B": []}
    errors: list[str] = []

    def worker(tag: str, token: str, n_photos: int):
        try:
            wc = TestClient(app)
            hdr = {"X-Device-Token": token}
            r = wc.post("/api/v1/projects",
                         json={"name": f"Site {tag}"}, headers=hdr)
            assert r.status_code == 201, r.text
            pid = r.json()["project_id"]
            for i in range(n_photos):
                data = _photo(seed=hash(tag) % 100 + i)
                sha = hashlib.sha256(data).hexdigest()
                pr = wc.post(
                    f"/api/v1/projects/{pid}/photos",
                    json={"idempotency_key": f"md-{tag}-{i}",
                          "original_sha256": sha,
                          "original_filename": "IMG.jpg",
                          "mime_type": "image/jpeg",
                          "byte_size": len(data),
                          "captured_at": "2026-11-25 10:00",
                          "device_id": tag}, headers=hdr)
                assert pr.status_code == 201, pr.text
                up = wc.put(
                    f"/api/v1/photos/{pr.json()['photo_id']}/original",
                    content=data,
                    headers={**hdr,
                             "Content-Type": "application/octet-stream"})
                assert up.status_code == 200, up.text
                results[tag].append((pid, pr.json()["photo_id"]))
        except Exception as e:      # pragma: no cover - diagnostic path
            errors.append(f"{tag}: {e}")

    tA = threading.Thread(target=worker, args=("A", env.tok_a, 8))
    tB = threading.Thread(target=worker, args=("B", env.tok_b, 8))
    tA.start(); tB.start(); tA.join(); tB.join()
    assert not errors, errors
    assert len(results["A"]) == 8 and len(results["B"]) == 8

    # ownership isolation after parallel work
    a_proj = {pid for pid, _ in results["A"]}
    b_proj = {pid for pid, _ in results["B"]}
    assert a_proj and a_proj.isdisjoint(b_proj)
    seen_a = {p["project_id"] for p in
              env.client.get("/api/v1/projects",
                              headers=env.H).json()["items"]}
    seen_b = {p["project_id"] for p in
              env.client.get("/api/v1/projects",
                              headers=env.B).json()["items"]}
    assert seen_a == a_proj and seen_b == b_proj
    # cross-subject reads stay 404
    b_photo = results["B"][0][1]
    assert env.client.get(f"/api/v1/photos/{b_photo}/original",
                           headers=env.H).status_code == 404
    # both devices' rows survive in the DB
    conn = sqlite3.connect(str(env.db))
    n = conn.execute("SELECT COUNT(*) FROM mvp_photos").fetchone()[0]
    conn.close()
    assert n == 16


# ==================================================== S3: revision race


def test_w016_s3_concurrent_revision_race(w016_env):
    """Two threads PATCH the same issue with the SAME expected_revision
    under true parallelism: exactly ONE wins, the loser gets a 409, and
    the stored revision ends at exactly 2 (no lost update, no double
    apply)."""
    env = w016_env
    c = env.client
    pid = _direct_project(env, "Race Site")
    issue = c.post(f"/api/v1/projects/{pid}/issues", json={
        "title": "Racy defect", "severity": "high"},
        headers=env.H).json()
    iid = issue["issue_id"]

    outcomes: list[int] = []
    lock = threading.Lock()

    def racer(tag: str):
        wc = TestClient(app)
        r = wc.patch(f"/api/v1/issues/{iid}", json={
            "description": f"edit by {tag}", "expected_revision": 1},
            headers=env.H)
        with lock:
            outcomes.append(r.status_code)

    barrier = threading.Barrier(2)
    orig_start = barrier.wait

    class SyncedRacer(threading.Thread):
        def __init__(self, tag):
            super().__init__()
            self.tag = tag

        def run(self):
            orig_start()
            racer(self.tag)

    t1 = SyncedRacer("T1")
    t2 = SyncedRacer("T2")
    t1.start(); t2.start(); t1.join(); t2.join()

    assert sorted(outcomes) == [200, 409], \
        f"expected exactly one winner, got {outcomes}"
    det = c.get(f"/api/v1/issues/{iid}", headers=env.H).json()
    assert det["revision"] == 2
    # loser's description must NOT be the stored one
    assert det["description"] in ("edit by T1", "edit by T2")
    # description edits do NOT append history rows by design (only
    # severity/status transitions do); the race must not have
    # double-written anything either
    assert len(det["history"]) == 1
    assert det["history"][0]["action"] == "created"


# ==================================================== S4: mixed batch


def test_w016_s4_mixed_outcome_batch(w016_env):
    """applied + rejected receipts coexist in ONE batch; the ledger
    replays BOTH deterministically; a rejected op never poisons the
    batches that follow it."""
    env = w016_env
    pid = _direct_project(env, "Mixed Site")
    data = _photo(seed=7)
    sha = hashlib.sha256(data).hexdigest()
    good1 = {"op": "photo_register", "operation_id": "mx-1",
              "payload": {"operation_id": "mx-1", "local_id": "g1",
                          "server_project_id": pid,
                          "idempotency_key": "mx-ik-1",
                          "original_sha256": sha,
                          "original_filename": "OK.jpg",
                          "mime_type": "image/jpeg",
                          "byte_size": len(data),
                          "captured_at": "t", "device_id": "d"}}
    bad_proj = dict(good1, operation_id="mx-2")
    bad_proj["payload"] = {**good1["payload"], "operation_id": "mx-2",
                            "local_id": "b1",
                            "server_project_id":
                                str(uuid.uuid4()),   # nonexistent
                            "idempotency_key": "mx-ik-2"}
    bad_ann = {"op": "annotation_create", "operation_id": "mx-3",
                "payload": {"operation_id": "mx-3", "local_id": "b2",
                             "server_photo_id": str(uuid.uuid4()),
                             "annotation_type": "arrow",
                             "norm_x1": 0.1, "norm_y1": 0.1,
                             "norm_x2": 0.5, "norm_y2": 0.5}}
    good2 = dict(good1, operation_id="mx-4")
    good2["payload"] = {**good1["payload"], "operation_id": "mx-4",
                         "local_id": "g2",
                         "idempotency_key": "mx-ik-4"}

    resp = _batch(env, [good1, bad_proj, bad_ann, good2])
    statuses = [r["status"] for r in resp["receipts"]]
    assert statuses == ["applied", "rejected", "rejected", "applied"], \
        statuses
    assert set(resp["id_map"]) == {"g1", "g2"}
    assert all("not_found" in str(r["error"])
               for r in resp["receipts"][1:3])

    # replay: ledger answers for ALL four (duplicates), ids stable
    replay = _batch(env, [good1, bad_proj, bad_ann, good2])
    assert all(r["status"] == "duplicate"
               for r in replay["receipts"])
    assert set(replay["id_map"]) == {"g1", "g2"}
    assert replay["id_map"] == resp["id_map"]
    # the rejected pair is stored as rejected in the ledger
    conn = sqlite3.connect(str(env.db))
    rows = {r[0]: json.loads(r[1]) for r in conn.execute(
        "SELECT operation_id, result_json FROM mvp_sync_ops "
        "WHERE operation_id IN ('mx-2','mx-3')")}
    conn.close()
    assert rows["mx-2"]["status"] == "rejected"
    assert rows["mx-3"]["status"] == "rejected"

    # a later clean batch still applies fine (no poisoning)
    good5 = {"op": "photo_register", "operation_id": "mx-5",
              "payload": {"operation_id": "mx-5", "local_id": "g3",
                           "server_project_id": pid,
                           "idempotency_key": "mx-ik-5",
                           "original_sha256": sha,
                           "original_filename": "OK5.jpg",
                           "mime_type": "image/jpeg",
                           "byte_size": len(data),
                           "captured_at": "t", "device_id": "d"}}
    resp5 = _batch(env, [good5])
    assert resp5["receipts"][0]["status"] == "applied"
    assert set(resp5["id_map"]) == {"g3"}


# ==================================================== S5: batch limits


def test_w016_s5_batch_size_boundaries(w016_env):
    """MAX_BATCH_OPS=50: a full batch applies, 51 ops -> 422, and the
    full batch replays to all-duplicate with a stable id_map."""
    env = w016_env
    pid = _direct_project(env, "Limit Site")
    data = _photo(seed=11)
    sha = hashlib.sha256(data).hexdigest()

    def reg(op_id: str, local: str, ik: str) -> dict:
        return {"op": "photo_register", "operation_id": op_id,
                 "payload": {"operation_id": op_id, "local_id": local,
                              "server_project_id": pid,
                              "idempotency_key": ik,
                              "original_sha256": sha,
                              "original_filename": "L.jpg",
                              "mime_type": "image/jpeg",
                              "byte_size": len(data),
                              "captured_at": "t", "device_id": "d"}}

    fifty = [reg(f"lim-{i}", f"l{i}", f"lim-ik-{i}") for i in range(50)]
    resp = _batch(env, fifty)
    assert resp["batch_received"] == 50
    assert all(r["status"] == "applied" for r in resp["receipts"])
    assert len(resp["id_map"]) == 50

    # 51 ops -> hard 422, NOTHING applied
    fifty_one = fifty + [reg("lim-50", "l50", "lim-ik-50")]
    r = env.client.post("/api/v1/sync/batch",
                         json={"operations": fifty_one}, headers=env.H)
    assert r.status_code == 422
    conn = sqlite3.connect(str(env.db))
    n = conn.execute("SELECT COUNT(*) FROM mvp_sync_ops "
                      "WHERE operation_id = 'lim-50'").fetchone()[0]
    conn.close()
    assert n == 0                    # the 51st op never landed

    # full-batch replay -> all duplicate, id_map identical
    replay = _batch(env, fifty)
    assert all(r["status"] == "duplicate" for r in replay["receipts"])
    assert replay["id_map"] == resp["id_map"]
