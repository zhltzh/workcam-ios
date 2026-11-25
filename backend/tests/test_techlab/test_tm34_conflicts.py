"""TM34: complex conflict handling - 10 conflict shapes, zero silent
overwrites. All batches run through the production sync executors.

MODEL NOTE (honest scope): the MVP is single-owner per project - a
second device token CANNOT see another owner's project (cross-subject
404, the W021 isolation discipline - pinned as test #10).
"Multi-device" conflicts therefore run as two PARALLEL OPERATION
STREAMS from the same owner (device swap / restored backup / two app
instances with one account); the machinery under test is identical:
optimistic revisions + typed conflicts, never silent last-write-wins.
"""

from __future__ import annotations

import base64
import hashlib
import io
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

CODE = "tm34-code"


def _photo(seed: int) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (64, 48), (seed % 255, 80, 120))
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

    def enroll(name):
        key = ec.generate_private_key(ec.SECP256R1())
        pub = key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)
        r = client.post("/api/v1/devices/enroll",
                        json={"public_key_b64":
                              base64.b64encode(pub).decode(),
                              "device_id": name,
                              "enrollment_code": CODE})
        assert r.status_code in (200, 201), r.text
        return {"X-Device-Token": r.json()["device_token"]}

    A, B = enroll("tm34-A"), enroll("tm34-B")
    r = client.post("/api/v1/projects", headers=A,
                    json={"name": "Conflict Site"})
    pid = r.json()["project_id"]

    def batch(ops):
        r = client.post("/api/v1/sync/batch", headers=A,
                        json={"operations": ops})
        assert r.status_code == 200, r.text
        return r.json()

    def photo_for(key):
        data = _photo(abs(hash(key)) % 200)
        sha = hashlib.sha256(data).hexdigest()
        out = batch([{"op": "photo_register",
                      "operation_id": f"reg-{key}",
                      "payload": {"operation_id": f"reg-{key}",
                                  "local_id": f"l-{key}",
                                  "server_project_id": pid,
                                  "idempotency_key": f"ik-{key}",
                                  "original_sha256": sha,
                                  "original_filename": f"{key}.jpg",
                                  "mime_type": "image/jpeg",
                                  "byte_size": len(data),
                                  "captured_at":
                                      "2026-11-26T07:00:00+00:00",
                                  "device_id": "cam"}}])
        rec = out["receipts"][0]
        assert rec["status"] == "applied", rec
        sp = rec["result"]["server_photo_id"]
        r2 = client.put(f"/api/v1/photos/{sp}/original", headers=A,
                        content=data)
        assert r2.status_code == 200, r2.text
        return sp

    def annotate(photo, oid, text):
        out = batch([{"op": "annotation_create",
                      "operation_id": oid,
                      "payload": {"operation_id": oid,
                                  "local_id": f"l-{oid}",
                                  "server_photo_id": photo,
                                  "annotation_type": "text",
                                  "norm_x1": 0.1, "norm_y1": 0.1,
                                  "text_content": text}}])
        rec = out["receipts"][0]
        assert rec["status"] == "applied", rec
        ann = rec["result"]["server_annotation_id"]
        # the authoritative revision comes from the server view
        r = client.get(f"/api/v1/photos/{photo}/annotations",
                       headers=A)
        row = next(a for a in r.json()["items"]
                   if a["annotation_id"] == ann)
        return ann, row["revision"]

    def make_issue(oid, title):
        out = batch([{"op": "issue_create",
                      "operation_id": oid,
                      "payload": {"operation_id": oid,
                                  "local_id": f"l-{oid}",
                                  "server_project_id": pid,
                                  "title": title,
                                  "severity": "high"}}])
        rec = out["receipts"][0]
        assert rec["status"] == "applied", rec
        return rec["result"]["server_issue_id"]

    return type("E", (), {"client": client, "A": A, "B": B, "pid": pid,
                          "batch": staticmethod(batch),
                          "photo_for": staticmethod(photo_for),
                          "annotate": staticmethod(annotate),
                          "make_issue": staticmethod(make_issue)})()


# ------------------------------------------------------------- 1

def test_parallel_edit_streams_annotation_race(env):
    photo = env.photo_for("race-photo")
    ann, rev0 = env.annotate(photo, "an-1", "A")

    first = env.batch([{"op": "annotation_update",
                        "operation_id": "s1-edit",
                        "payload": {"operation_id": "s1-edit",
                                    "server_annotation_id": ann,
                                    "expected_revision": rev0,
                                    "text_content": "first write"}}])
    assert first["receipts"][0]["status"] == "applied"
    # stream 2 (restored backup / stale outbox) replays revision N
    second = env.batch([{"op": "annotation_update",
                         "operation_id": "s2-edit",
                         "payload": {"operation_id": "s2-edit",
                                     "server_annotation_id": ann,
                                     "expected_revision": rev0,
                                     "text_content": "second write"}}])
    rec = second["receipts"][0]
    assert rec["status"] == "rejected"
    assert rec["error"]["code"] == "revision_conflict"
    r = env.client.get(f"/api/v1/photos/{photo}/annotations",
                       headers=env.A)
    row = next(a for a in r.json()["items"]
               if a["annotation_id"] == ann)
    assert row["text_content"] == "first write"      # never flipped


# ------------------------------------------------------------- 2

def test_same_photo_double_classification_conflict(env):
    base = {"operation_id": "dup-ok", "local_id": "l-dup",
            "server_project_id": env.pid, "idempotency_key": "ik-dup",
            "original_sha256": "aa" * 32,
            "original_filename": "x.jpg", "mime_type": "image/jpeg",
            "byte_size": 10, "captured_at": "2026-11-26T07:00:00+00:00",
            "device_id": "cam"}

    def reg(op_id, sha="aa" * 32, name="x.jpg", size=10):
        out = env.batch([{"op": "photo_register",
                          "operation_id": op_id,
                          "payload": {**base, "operation_id": op_id,
                                      "original_sha256": sha,
                                      "original_filename": name,
                                      "byte_size": size}}])
        return out["receipts"][0]

    assert reg("dup-ok")["status"] == "applied"
    clash = reg("dup-clash", sha="bb" * 32, name="y.jpg", size=20)
    assert clash["status"] == "rejected"
    assert clash["error"]["code"] == "idempotency_conflict"
    # replay of the SAME operation id = duplicate receipt (ledger hit),
    # never a second row
    assert reg("dup-ok")["status"] == "duplicate"


# ------------------------------------------------------------- 3

def test_project_name_stale_revision_loses(env):
    made = env.batch([{"op": "project_update",
                       "operation_id": "pu-1",
                       "payload": {"operation_id": "pu-1",
                                   "server_project_id": env.pid,
                                   "expected_revision": 1,
                                   "name": "Site v2"}}])
    assert made["receipts"][0]["status"] == "applied"
    stale = env.batch([{"op": "project_update",
                        "operation_id": "pu-2",
                        "payload": {"operation_id": "pu-2",
                                    "server_project_id": env.pid,
                                    "expected_revision": 1,
                                    "name": "Site v3"}}])
    rec = stale["receipts"][0]
    assert rec["status"] == "rejected"
    assert rec["error"]["code"] == "revision_conflict"
    # the current revision rides inside the conflict message
    assert "current_revision" in rec["error"]["message"]
    r = env.client.get(f"/api/v1/projects/{env.pid}", headers=env.A)
    assert r.json()["name"] == "Site v2"


# ------------------------------------------------------------- 4

def test_tag_batch_ledger_rejects_hash_drift(env):
    photo = env.photo_for("tag-photo")
    r = env.client.post("/api/v1/photos/batch/tags", headers=env.A,
                        json={"batch_id": "tag-b1",
                              "photo_ids": [photo],
                              "action": "add", "tags": ["roof"]})
    assert r.status_code == 200 and r.json()["succeeded"] == 1
    # SAME batch id + photo but DIFFERENT tags: the per-photo ledger
    # detects the parameter drift and refuses - a typed per-item
    # batch_id_conflict, never a silent overwrite
    r2 = env.client.post("/api/v1/photos/batch/tags", headers=env.A,
                         json={"batch_id": "tag-b1",
                               "photo_ids": [photo],
                               "action": "add", "tags": ["wall"]})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["succeeded"] == 0 and body["failed"] == 1
    item = body["results"][0]
    assert item["status"] == "failed"
    assert item["error"]["code"] == "batch_id_conflict"
    # and the ORIGINAL tag survived untouched
    r3 = env.client.get(f"/api/v1/photos/search?tag=roof&limit=5",
                        headers=env.A)
    assert any(p["photo_id"] == photo for p in r3.json()["items"])


# ------------------------------------------------------------- 5

def test_issue_status_double_transition(env):
    iid = env.make_issue("iss-1", "Crack in beam")
    t1 = env.batch([{"op": "issue_transition",
                     "operation_id": "tr-1",
                     "payload": {"operation_id": "tr-1",
                                 "server_issue_id": iid,
                                 "expected_revision": 1,
                                 "target_status": "in_progress"}}])
    assert t1["receipts"][0]["status"] == "applied"
    # second stream transitions from the SAME base revision
    t2 = env.batch([{"op": "issue_transition",
                     "operation_id": "tr-2",
                     "payload": {"operation_id": "tr-2",
                                 "server_issue_id": iid,
                                 "expected_revision": 1,
                                 "target_status": "resolved"}}])
    rec = t2["receipts"][0]
    assert rec["status"] == "rejected"
    assert rec["error"]["code"] in ("revision_conflict",
                                    "issue_status_conflict")


# ------------------------------------------------------------- 6

def test_delete_wins_then_edit_is_typed_409(env):
    photo = env.photo_for("del-photo")
    ann, rev = env.annotate(photo, "an-d1", "D")
    gone = env.batch([{"op": "annotation_delete",
                       "operation_id": "del-1",
                       "payload": {"operation_id": "del-1",
                                   "server_annotation_id": ann}}])
    assert gone["receipts"][0]["status"] == "applied"
    # a stale stream edits the deleted annotation -> typed refusal;
    # delete stays final for edits (no resurrection)
    edit = env.batch([{"op": "annotation_update",
                       "operation_id": "edit-late",
                       "payload": {"operation_id": "edit-late",
                                   "server_annotation_id": ann,
                                   "expected_revision": rev,
                                   "text_content": "late"}}])
    rec = edit["receipts"][0]
    assert rec["status"] == "rejected"
    assert rec["error"]["code"] == "annotation_deleted"
    # re-delete replays idempotently (same receipt shape, stays gone)
    again = env.batch([{"op": "annotation_delete",
                        "operation_id": "del-2",
                        "payload": {"operation_id": "del-2",
                                    "server_annotation_id": ann}}])
    assert again["receipts"][0]["status"] == "applied"
    r = env.client.get(f"/api/v1/photos/{photo}/annotations",
                       headers=env.A)
    assert all(a["annotation_id"] != ann for a in r.json()["items"])


# ------------------------------------------------------------- 7

def test_archived_project_refuses_new_photos(env):
    r = env.client.post(f"/api/v1/projects/{env.pid}/archive",
                        headers=env.A)
    assert r.status_code == 200, r.text
    data = _photo(9)
    r = env.client.post(f"/api/v1/projects/{env.pid}/photos",
                        headers=env.A, json={
        "idempotency_key": "post-archive",
        "original_sha256": hashlib.sha256(data).hexdigest(),
        "original_filename": "late.jpg", "mime_type": "image/jpeg",
        "byte_size": len(data),
        "captured_at": "2026-11-26T07:00:00+00:00",
        "device_id": "cam"})
    assert r.status_code == 409, r.text
    assert "archived" in r.json()["detail"]["code"]


# ------------------------------------------------------------- 8

def test_dependent_op_before_prerequisite_rejected(env):
    out = env.batch([{"op": "annotation_create",
                      "operation_id": "orphan-an",
                      "payload": {"operation_id": "orphan-an",
                                  "local_id": "l-orphan",
                                  "server_photo_id":
                                      "00000000-0000-0000-0000"
                                      "-0000000000ff",
                                  "annotation_type": "text",
                                  "norm_x1": 0, "norm_y1": 0,
                                  "text_content": "orphan"}}])
    rec = out["receipts"][0]
    assert rec["status"] == "rejected"
    assert rec["error"]["code"] in ("not_found", "invalid_local_ref")


# ------------------------------------------------------------- 9

def test_server_version_change_beats_stale_client(env):
    made = env.batch([{"op": "project_update",
                       "operation_id": "pv-1",
                       "payload": {"operation_id": "pv-1",
                                   "server_project_id": env.pid,
                                   "expected_revision": 1,
                                   "name": "v2"}}])
    assert made["receipts"][0]["status"] == "applied"
    made2 = env.batch([{"op": "project_update",
                        "operation_id": "pv-2",
                        "payload": {"operation_id": "pv-2",
                                    "server_project_id": env.pid,
                                    "expected_revision": 2,
                                    "name": "v3"}}])
    assert made2["receipts"][0]["status"] == "applied"
    stale = env.batch([{"op": "project_update",
                        "operation_id": "pv-3",
                        "payload": {"operation_id": "pv-3",
                                    "server_project_id": env.pid,
                                    "expected_revision": 1,
                                    "name": "v4"}}])
    rec = stale["receipts"][0]
    assert rec["status"] == "rejected"
    assert rec["error"]["code"] == "revision_conflict"
    # the conflict names the CURRENT revision so the client rebases
    assert "'current_revision': 3" in rec["error"]["message"]
    fixed = env.batch([{"op": "project_update",
                        "operation_id": "pv-4",
                        "payload": {"operation_id": "pv-4",
                                    "server_project_id": env.pid,
                                    "expected_revision": 3,
                                    "name": "v4"}}])
    assert fixed["receipts"][0]["status"] == "applied"


# ------------------------------------------------------------ 10

def test_cross_owner_isolation_is_404_not_data(env):
    photo = env.photo_for("iso-photo")
    r = env.client.get(f"/api/v1/photos/{photo}", headers=env.B)
    assert r.status_code == 404, r.text
    r = env.client.get(f"/api/v1/projects/{env.pid}", headers=env.B)
    assert r.status_code == 404, r.text
    # through the sync path the second owner also sees plain 404 -
    # the isolation discipline holds on every surface
    r = env.client.post("/api/v1/sync/batch", headers=env.B, json={
        "operations": [{"op": "project_update",
                        "operation_id": "b-peek",
                        "payload": {"operation_id": "b-peek",
                                    "server_project_id": env.pid,
                                    "expected_revision": 1,
                                    "name": "hijack"}}]})
    rec = r.json()["receipts"][0]
    assert rec["status"] == "rejected"
    assert rec["error"]["code"] == "not_found"
