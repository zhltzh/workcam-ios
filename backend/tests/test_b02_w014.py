"""B02-W014 tests: offline sync completion for annotations + issues.

DESKTOP_SIMULATION - synthetic photos, REAL SQLite/FSS/ZIP behavior,
threaded concurrency where stated. Covers the B02-W014 acceptance:

  V9  migration: event-stream CHECK extension carries rows + seq
  E1  event stream broadcasts annotation/issue/link/soft-delete ops
  E2  intra-batch local refs: one queue, one round-trip, full chain
  E3  unresolved / forward local refs are typed rejections (ledgered)
  E4  soft delete syncs to a second device via the event stream
  E5  concurrent edits to one issue: exactly one winner (threads)
  E6  cross-user + cross-project isolation on every new op
  E7  network-interruption retry: nothing lost, replays duplicate
  E8  clients cannot smuggle trusted evidence state via new ops
  E9  full-day fieldwork scenario within the 50-op batch cap
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
import threading
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from tests.conftest import _encode_pub


@pytest.fixture()
def b02_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "b02-code")
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
            pub_b64, name, "b02-code")

    key_a, pub_a, tok_a = mk("b02-AAA")
    key_b, pub_b, tok_b = mk("b02-BBB")
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "biz.db",
        "key": key_a, "pub_a": pub_a, "tok_a": tok_a, "tok_b": tok_b,
        "H": {"X-Device-Token": tok_a},
        "B": {"X-Device-Token": tok_b},
        "client": TestClient(app),
    })()


def _photo(w=1200, h=900, seed=0) -> bytes:
    img = Image.new("RGB", (w, h), (90 + seed % 128, 140, 120))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 200, 150], fill=(40, 80, 160 + seed % 96))
    d.text((60, 300), f"B02 seed {seed}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _batch(env, ops, headers=None) -> dict:
    r = env.client.post("/api/v1/sync/batch", json={"operations": ops},
                         headers=headers or env.H)
    assert r.status_code == 200, r.text
    return r.json()


def _direct_project(env, name, headers=None) -> str:
    r = env.client.post("/api/v1/projects", json={"name": name},
                         headers=headers or env.H)
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _register_direct_photo(env, pid, data: bytes, headers=None) -> str:
    r = env.client.post(
        f"/api/v1/projects/{pid}/photos",
        json={"idempotency_key": f"b02-{uuid.uuid4().hex[:8]}",
              "original_sha256": hashlib.sha256(data).hexdigest(),
              "original_filename": "IMG.jpg", "mime_type": "image/jpeg",
              "byte_size": len(data), "captured_at": "2026-11-25 07:00",
              "device_id": "iPhone15,3"}, headers=headers or env.H)
    assert r.status_code == 201, r.text
    photo_id = r.json()["photo_id"]
    up = env.client.put(f"/api/v1/photos/{photo_id}/original",
                         content=data,
                         headers={**(headers or env.H),
                                  "Content-Type": "application/octet-stream"})
    assert up.status_code == 200, up.text
    return photo_id


def _events(env, cursor=0, entity_type=None, limit=200, headers=None):
    url = f"/api/v1/sync/events?cursor={cursor}&limit={limit}"
    if entity_type:
        url += f"&entity_type={entity_type}"
    r = env.client.get(url, headers=headers or env.H)
    assert r.status_code == 200, r.text
    return r.json()


def _sql(env):
    conn = sqlite3.connect(str(env.db))
    conn.row_factory = sqlite3.Row
    return conn


# ================================================== V9: migration


def test_b02_v9_migration_carries_events_and_seq(b02_env, monkeypatch):
    """v8 -> v9 rebuild keeps every event row AND the AUTOINCREMENT
    counter: the client cursor survives the upgrade and the seq only
    moves forward."""
    env = b02_env
    # seed two v8-era events (sync-batch path; the plain API does not
    # log events by W008 design)
    _batch(env, [
        {"op": "project_create", "operation_id": "v9-a",
         "payload": {"operation_id": "v9-a", "local_id": "lp1",
                     "name": "V9 Carry Site"}}])
    _batch(env, [
        {"op": "project_create", "operation_id": "v9-b",
         "payload": {"operation_id": "v9-b", "local_id": "lp2",
                     "name": "V9 Carry Site 2"}}])
    conn = _sql(env)
    old = conn.execute(
        "SELECT seq, entity_id, action FROM mvp_sync_events "
        "ORDER BY seq").fetchall()
    assert len(old) == 2 and all(r["action"] == "created" for r in old)
    max_seq = old[-1]["seq"]
    # simulate a v8 database: rewind user_version, re-run migrate()
    conn.execute("PRAGMA user_version = 8")
    conn.close()
    import app.db as dbm
    # W022 RC2 premerge: the chain above v8 also holds the ALTER-type
    # v12 (W015 remediation_note), which cannot be replayed against a
    # schema that already carries the column - and this test database
    # DOES carry it (the fixture migrated to latest). A real v8
    # database never does. Apply v9 in isolation - exactly the next
    # step migrate() takes for a real v8 database - then restore the
    # full chain and jump to the post-upgrade steady state.
    full_chain = dict(dbm.MIGRATIONS)
    monkeypatch.setattr(dbm, "MIGRATIONS", {9: full_chain[9]})
    assert dbm.migrate(env.db) == 1       # exactly v9 was applied
    monkeypatch.setattr(dbm, "MIGRATIONS", full_chain)
    conn = _sql(env)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
    carried = conn.execute(
        "SELECT seq, entity_id, action FROM mvp_sync_events "
        "ORDER BY seq").fetchall()
    assert [(r["seq"], r["entity_id"], r["action"])
            for r in carried] == \
        [(r["seq"], r["entity_id"], r["action"]) for r in old]
    # the new CHECK values are accepted
    conn.execute(
        "INSERT INTO mvp_sync_events (owner_token, entity_type, "
        "entity_id, action, revision, payload_hint, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("x", "annotation", "a1", "soft_deleted", 2, "h",
         "2026-11-25T00:00:00+00:00"))
    new_seq = conn.execute(
        "SELECT MAX(seq) FROM mvp_sync_events").fetchone()[0]
    assert new_seq > max_seq               # counter carried forward
    # steady state: at the latest version migrate() is a no-op.
    # Commit BEFORE the PRAGMA: python sqlite3 opens an implicit
    # transaction on the INSERT above; an uncommitted close rolls
    # the transaction back and user_version goes with it (found
    # during the W022 RC2 premerge).
    conn.commit()
    conn.execute(f"PRAGMA user_version = {max(full_chain)}")
    conn.close()
    assert dbm.migrate(env.db) == 0


def test_b02_v9_fresh_db_reaches_latest(tmp_path, monkeypatch):
    """Fresh DB reaches the LATEST schema version (v9 + everything
    above it; W022 RC2 premerge renamed this from 'reaches v9' because
    the chain keeps growing past v9)."""
    import app.db as dbm
    p = tmp_path / "fresh.db"
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(p))
    dbm.migrate(p)
    conn = sqlite3.connect(str(p))
    assert conn.execute("PRAGMA user_version").fetchone()[0] \
        == max(dbm.MIGRATIONS)
    conn.close()


# ================================================= E1: event stream


def test_b02_events_broadcast_annotation_issue_lifecycle(b02_env):
    """Every W013-R1 sync op now emits a typed event a second device
    can pull: created / updated / soft_deleted / photo_linked /
    photo_unlinked / status_changed, with the entity filter working."""
    env = b02_env
    pid = _direct_project(env, "Event Site")
    data = _photo(seed=3)
    photo_id = _register_direct_photo(env, pid, data)

    ops = [
        {"op": "annotation_create", "operation_id": "ev-an-1",
         "payload": {"operation_id": "ev-an-1", "local_id": "lan1",
                     "server_photo_id": photo_id,
                     "annotation_type": "arrow",
                     "norm_x1": 0.1, "norm_y1": 0.1,
                     "norm_x2": 0.5, "norm_y2": 0.5}},
        {"op": "annotation_update", "operation_id": "ev-an-2",
         "payload": {"operation_id": "ev-an-2",
                     "server_annotation_id": "local:lan1",
                     "expected_revision": 1, "stroke_width": 12}},
        {"op": "annotation_delete", "operation_id": "ev-an-3",
         "payload": {"operation_id": "ev-an-3",
                     "server_annotation_id": "local:lan1"}},
        {"op": "issue_create", "operation_id": "ev-is-1",
         "payload": {"operation_id": "ev-is-1", "local_id": "lis1",
                     "server_project_id": pid,
                     "title": "Crack on pier 3", "severity": "high"}},
        {"op": "issue_photo_link", "operation_id": "ev-ln-1",
         "payload": {"operation_id": "ev-ln-1",
                     "server_issue_id": "local:lis1",
                     "server_photo_id": photo_id, "role": "before"}},
        {"op": "issue_photo_unlink", "operation_id": "ev-ul-1",
         "payload": {"operation_id": "ev-ul-1",
                     "server_issue_id": "local:lis1",
                     "server_photo_id": photo_id, "role": "before"}},
        {"op": "issue_transition", "operation_id": "ev-tr-1",
         "payload": {"operation_id": "ev-tr-1",
                     "server_issue_id": "local:lis1",
                     "expected_revision": 1,
                     "target_status": "in_progress"}},
    ]
    resp = _batch(env, ops)
    assert all(r["status"] == "applied" for r in resp["receipts"]), \
        resp["receipts"]
    ann_id = resp["id_map"]["lan1"]

    items = _events(env, entity_type="annotation")["items"]
    assert [(i["action"], i["entity_id"]) for i in items] == [
        ("created", ann_id), ("updated", ann_id),
        ("soft_deleted", ann_id)]
    assert items[1]["revision"] == 2 and items[2]["revision"] == 3

    items = _events(env, entity_type="issue")["items"]
    actions = [i["action"] for i in items]
    assert actions == ["created", "photo_linked", "photo_unlinked",
                       "status_changed"]
    assert items[0]["revision"] == 1
    assert "open->in_progress" in items[3]["payload_hint"]

    # entity_type filter isolates the streams
    assert all(i["entity_type"] == "annotation"
               for i in _events(env, entity_type="annotation")["items"])
    # device B sees none of device A's events
    assert _events(env, headers=env.B)["items"] == []


# ============================================ E2: intra-batch refs


def test_b02_intra_batch_local_refs_full_chain_one_round(b02_env):
    """One offline queue, ONE round-trip: project -> photo ->
    annotation -> annotation update -> issue -> before/after link ->
    transition, all via 'local:<id>' refs (the client only knows its
    own local ids while offline). Original bytes stay untouched."""
    env = b02_env
    data = _photo(seed=5)
    sha = hashlib.sha256(data).hexdigest()
    ops = [
        {"op": "project_create", "operation_id": "ch-1",
         "payload": {"operation_id": "ch-1", "local_id": "lproj",
                     "name": "Local Ref Site",
                     "site_address": "Km 12+100"}},
        {"op": "photo_register", "operation_id": "ch-2",
         "payload": {"operation_id": "ch-2", "local_id": "lph",
                     "server_project_id": "local:lproj",
                     "idempotency_key": "ch-ik-2",
                     "original_sha256": sha,
                     "original_filename": "IMG.jpg",
                     "mime_type": "image/jpeg", "byte_size": len(data),
                     "captured_at": "2026-11-25 08:00",
                     "device_id": "iPhone15,3"}},
        {"op": "annotation_create", "operation_id": "ch-3",
         "payload": {"operation_id": "ch-3", "local_id": "lan",
                     "server_photo_id": "local:lph",
                     "annotation_type": "text",
                     "norm_x1": 0.3, "norm_y1": 0.4,
                     "text_content": "rebar exposure"}},
        {"op": "annotation_update", "operation_id": "ch-4",
         "payload": {"operation_id": "ch-4",
                     "server_annotation_id": "local:lan",
                     "expected_revision": 1, "stroke_width": 8}},
        {"op": "issue_create", "operation_id": "ch-5",
         "payload": {"operation_id": "ch-5", "local_id": "lis",
                     "server_project_id": "local:lproj",
                     "title": "Exposed rebar at pier 2",
                     "severity": "high"}},
        {"op": "issue_photo_link", "operation_id": "ch-6",
         "payload": {"operation_id": "ch-6",
                     "server_issue_id": "local:lis",
                     "server_photo_id": "local:lph", "role": "before"}},
        {"op": "issue_photo_link", "operation_id": "ch-7",
         "payload": {"operation_id": "ch-7",
                     "server_issue_id": "local:lis",
                     "server_photo_id": "local:lph", "role": "after"}},
        {"op": "issue_transition", "operation_id": "ch-8",
         "payload": {"operation_id": "ch-8",
                     "server_issue_id": "local:lis",
                     "expected_revision": 1,
                     "target_status": "in_progress"}},
    ]
    resp = _batch(env, ops)
    statuses = [r["status"] for r in resp["receipts"]]
    assert statuses == ["applied"] * 8, resp["receipts"]
    pid = resp["id_map"]["lproj"]
    photo_id = resp["id_map"]["lph"]
    issue_id = resp["id_map"]["lis"]

    # the local refs really landed on the server ids
    conn = _sql(env)
    ann = conn.execute(
        "SELECT photo_id, revision, is_active FROM mvp_annotations "
        "WHERE annotation_id = ?",
        (resp["id_map"]["lan"],)).fetchone()
    conn.close()
    assert ann["photo_id"] == photo_id and ann["revision"] == 2 \
        and ann["is_active"] == 1

    # upload + verify the ORIGINAL bytes are byte-identical afterwards
    up = env.client.put(f"/api/v1/photos/{photo_id}/original",
                         content=data,
                         headers={**env.H,
                                  "Content-Type": "application/octet-stream"})
    assert up.status_code == 200, up.text
    more = [
        {"op": "issue_update", "operation_id": "ch-9",
         "payload": {"operation_id": "ch-9",
                     "server_issue_id": issue_id,
                     "expected_revision": 2,
                     "description": "grinding scheduled"}},
        {"op": "issue_transition", "operation_id": "ch-10",
         "payload": {"operation_id": "ch-10",
                     "server_issue_id": issue_id,
                     "expected_revision": 3,
                     "target_status": "resolved"}},
    ]
    resp2 = _batch(env, more)
    assert [r["status"] for r in resp2["receipts"]] == \
        ["applied", "applied"]
    dl = env.client.get(f"/api/v1/photos/{photo_id}/original",
                         headers=env.H)
    assert dl.content == data
    det = env.client.get(f"/api/v1/issues/{issue_id}",
                          headers=env.H).json()
    assert det["status"] == "resolved" and det["revision"] == 4

    # events are pullable for the whole chain, in order
    items = _events(env, entity_type="issue")["items"]
    assert [i["action"] for i in items] == ["created", "photo_linked",
                                            "photo_linked",
                                            "status_changed", "updated",
                                            "status_changed"]

    # replay of the original queue: all duplicate, ids stable
    replay = _batch(env, ops)
    assert all(r["status"] == "duplicate" for r in replay["receipts"])
    assert replay["id_map"] == resp["id_map"]


# ===================================== E3: unresolved / forward refs


def test_b02_unresolved_local_ref_typed_rejection(b02_env):
    """A local ref with no earlier applied op is a typed rejection
    (dependency-order check); it is LEDGERED like every deterministic
    failure and never poisons the ops around it."""
    env = b02_env
    good = {"op": "project_create", "operation_id": "ur-1",
             "payload": {"operation_id": "ur-1", "local_id": "lproj",
                         "name": "Unresolved Site"}}
    bad = {"op": "annotation_create", "operation_id": "ur-2",
            "payload": {"operation_id": "ur-2", "local_id": "lan",
                         "server_photo_id": "local:missing-photo",
                         "annotation_type": "arrow",
                         "norm_x1": 0.1, "norm_y1": 0.1,
                         "norm_x2": 0.6, "norm_y2": 0.4}}
    good2 = {"op": "project_create", "operation_id": "ur-3",
              "payload": {"operation_id": "ur-3", "local_id": "lproj2",
                          "name": "Unresolved Site 2"}}
    resp = _batch(env, [good, bad, good2])
    assert [r["status"] for r in resp["receipts"]] == \
        ["applied", "rejected", "applied"]
    err = resp["receipts"][1]["error"]
    assert err["code"] == "unresolved_local_ref"
    assert err["message"] != ""

    # the rejection is ledgered: replay returns the stored rejection
    replay = _batch(env, [good, bad, good2])
    assert [r["status"] for r in replay["receipts"]] == \
        ["duplicate", "duplicate", "duplicate"]
    # after fixing the queue the client retries under a NEW op id
    pid = resp["id_map"]["lproj2"]
    data = _photo(seed=9)
    ph = _register_direct_photo(env, pid, data)
    fixed = dict(bad, operation_id="ur-2-retry")
    fixed["payload"] = {**bad["payload"], "operation_id": "ur-2-retry",
                        "server_photo_id": ph}
    resp3 = _batch(env, [fixed])
    assert resp3["receipts"][0]["status"] == "applied"


def test_b02_forward_reference_is_rejected(b02_env):
    """A ref to an op LATER in the batch does not resolve: dependency
    order is enforced strictly (only earlier applied ops count)."""
    env = b02_env
    ops = [
        {"op": "annotation_create", "operation_id": "fw-1",
         "payload": {"operation_id": "fw-1", "local_id": "lan",
                     "server_photo_id": "local:lph-later",
                     "annotation_type": "circle",
                     "norm_x1": 0.2, "norm_y1": 0.2}},
        {"op": "photo_register", "operation_id": "fw-2",
         "payload": {"operation_id": "fw-2", "local_id": "lph-later",
                     "server_project_id": "local:lproj",
                     "idempotency_key": "fw-ik",
                     "original_sha256":
                         hashlib.sha256(b"x").hexdigest(),
                     "original_filename": "IMG.jpg",
                     "mime_type": "image/jpeg", "byte_size": 1,
                     "captured_at": "t", "device_id": "d"}},
        {"op": "project_create", "operation_id": "fw-3",
         "payload": {"operation_id": "fw-3", "local_id": "lproj",
                     "name": "Forward Site"}},
    ]
    resp = _batch(env, ops)
    assert [r["status"] for r in resp["receipts"]] == \
        ["rejected", "rejected", "applied"]
    assert resp["receipts"][0]["error"]["code"] == \
        "unresolved_local_ref"


# ================================================= E4: soft delete


def test_b02_soft_delete_syncs_to_second_device(b02_env):
    """Device 1 soft-deletes an annotation through the sync batch;
    device 2 (same owner token) pulls the event stream, learns the
    soft-deleted id, and the annotation is inactive on read."""
    env = b02_env
    pid = _direct_project(env, "SoftDel Site")
    photo_id = _register_direct_photo(env, pid, _photo(seed=13))
    before = _events(env)["cursor"]
    ops = [
        {"op": "annotation_create", "operation_id": "sd-1",
         "payload": {"operation_id": "sd-1", "local_id": "lan",
                     "server_photo_id": photo_id,
                     "annotation_type": "rectangle",
                     "norm_x1": 0.1, "norm_y1": 0.1,
                     "norm_x2": 0.7, "norm_y2": 0.8}},
        {"op": "annotation_delete", "operation_id": "sd-2",
         "payload": {"operation_id": "sd-2",
                     "server_annotation_id": "local:lan"}},
    ]
    resp = _batch(env, ops)
    assert all(r["status"] == "applied" for r in resp["receipts"])
    ann_id = resp["id_map"]["lan"]

    # second device: fresh client, same owner, pulls from its cursor
    client2 = TestClient(app)
    r = client2.get(f"/api/v1/sync/events?cursor={before}&limit=200",
                    headers=env.H)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ann_events = [i for i in items if i["entity_type"] == "annotation"]
    assert [(i["action"], i["entity_id"]) for i in ann_events] == \
        [("created", ann_id), ("soft_deleted", ann_id)]
    # the deleted annotation is no longer served to readers
    r = client2.get(f"/api/v1/photos/{photo_id}/annotations",
                     headers=env.H)
    assert r.status_code == 200
    assert all(a["annotation_id"] != ann_id
               for a in r.json()["items"])


# =========================================== E5: concurrent edits


def test_b02_concurrent_edits_exactly_one_winner(b02_env):
    """Two threads push issue_update ops with the SAME
    expected_revision through the sync batch under true parallelism:
    exactly one applies, the loser gets a typed revision_conflict, the
    stored revision ends at 2 - no lost update, no double apply."""
    env = b02_env
    pid = _direct_project(env, "Race Site B02")
    iss = env.client.post(f"/api/v1/projects/{pid}/issues", json={
        "title": "Concurrent defect", "severity": "medium"},
        headers=env.H).json()
    iid = iss["issue_id"]

    outcomes: list[tuple[str, str]] = []
    lock = threading.Lock()

    def racer(tag: str):
        wc = TestClient(app)
        r = wc.post("/api/v1/sync/batch", json={"operations": [
            {"op": "issue_update", "operation_id": f"race-{tag}",
             "payload": {"operation_id": f"race-{tag}",
                          "server_issue_id": iid,
                          "expected_revision": 1,
                          "description": f"edit by {tag}"}}]},
            headers=env.H)
        body = r.json()
        status = body["receipts"][0]["status"]
        code = (body["receipts"][0].get("error") or {}).get("code", "")
        with lock:
            outcomes.append((status, code))

    barrier = threading.Barrier(2)

    class Synced(threading.Thread):
        def __init__(self, tag):
            super().__init__()
            self.tag = tag

        def run(self):
            barrier.wait()
            racer(self.tag)

    t1, t2 = Synced("T1"), Synced("T2")
    t1.start(); t2.start(); t1.join(); t2.join()

    statuses = sorted(s for s, _ in outcomes)
    assert statuses == ["applied", "rejected"], outcomes
    loser = [c for s, c in outcomes if s == "rejected"]
    assert loser == ["revision_conflict"]
    det = env.client.get(f"/api/v1/issues/{iid}",
                          headers=env.H).json()
    assert det["revision"] == 2
    assert det["description"] in ("edit by T1", "edit by T2")
    # replaying the losing op id returns the stored rejection
    loser_tag = next(t for t, (s, _) in zip(("T1", "T2"), outcomes)
                     if s == "rejected")
    replay = _batch(env, [{"op": "issue_update",
                            "operation_id": f"race-{loser_tag}",
                            "payload": {
                                "operation_id": f"race-{loser_tag}",
                                "server_issue_id": iid,
                                "expected_revision": 1,
                                "description": f"edit by {loser_tag}"}}])
    assert replay["receipts"][0]["status"] == "duplicate"


# ================================================ E6: isolation


def test_b02_cross_user_and_cross_project_isolation(b02_env):
    """Device B's sync ops cannot touch device A's project / photo /
    annotation / issue (404-equivalent, no oracle), and A's own ops
    cannot link photos across projects (typed 400)."""
    env = b02_env
    pidA = _direct_project(env, "Isolation Site A")
    pidA2 = _direct_project(env, "Isolation Site A2")
    photoA = _register_direct_photo(env, pidA, _photo(seed=21))
    photoA2 = _register_direct_photo(env, pidA2, _photo(seed=22))
    create = [
        {"op": "annotation_create", "operation_id": "iso-an",
         "payload": {"operation_id": "iso-an", "local_id": "lan",
                     "server_photo_id": photoA,
                     "annotation_type": "arrow",
                     "norm_x1": 0.1, "norm_y1": 0.1,
                     "norm_x2": 0.5, "norm_y2": 0.5}},
        {"op": "issue_create", "operation_id": "iso-is",
         "payload": {"operation_id": "iso-is", "local_id": "lis",
                     "server_project_id": pidA,
                     "title": "Isolation defect", "severity": "low"}},
    ]
    made = _batch(env, create)
    annA = made["id_map"]["lan"]
    issA = made["id_map"]["lis"]

    # B references A's ids -> every op is a typed 404 not_found
    steals = [
        {"op": "annotation_create", "operation_id": "st-an",
         "payload": {"operation_id": "st-an", "local_id": "lanB",
                     "server_photo_id": photoA,
                     "annotation_type": "circle",
                     "norm_x1": 0.2, "norm_y1": 0.2}},
        {"op": "annotation_update", "operation_id": "st-an2",
         "payload": {"operation_id": "st-an2",
                     "server_annotation_id": annA,
                     "expected_revision": 1, "stroke_width": 9}},
        {"op": "annotation_delete", "operation_id": "st-an3",
         "payload": {"operation_id": "st-an3",
                     "server_annotation_id": annA}},
        {"op": "issue_update", "operation_id": "st-is",
         "payload": {"operation_id": "st-is",
                     "server_issue_id": issA,
                     "expected_revision": 1, "severity": "critical"}},
        {"op": "issue_create", "operation_id": "st-is2",
         "payload": {"operation_id": "st-is2", "local_id": "lisB",
                     "server_project_id": pidA,
                     "title": "Stolen issue", "severity": "low"}},
    ]
    resp = _batch(env, steals, headers=env.B)
    assert all(r["status"] == "rejected" for r in resp["receipts"])
    assert all(r["error"]["code"] == "not_found"
               for r in resp["receipts"]), resp["receipts"]
    # A's entities are untouched
    det = env.client.get(f"/api/v1/issues/{issA}", headers=env.H).json()
    assert det["revision"] == 1 and det["severity"] == "low"

    # A links a photo from ANOTHER project into its issue -> typed 400
    cross = {"op": "issue_photo_link", "operation_id": "iso-ln",
              "payload": {"operation_id": "iso-ln",
                           "server_issue_id": issA,
                           "server_photo_id": photoA2,
                           "role": "before"}}
    resp = _batch(env, [cross])
    assert resp["receipts"][0]["status"] == "rejected"
    assert resp["receipts"][0]["error"]["code"] == \
        "photo_project_mismatch"


# ======================================= E7: interruption retry


def test_b02_network_interruption_retry_no_loss(b02_env):
    """The connection drops mid-sync (response never read). The client
    replays the SAME queue -> every applied op is duplicate (zero
    re-execution); a rejected op (revision conflict against a remote
    edit) is re-queued under a new op id after re-fetch -> applied.
    Nothing queued is lost."""
    env = b02_env
    pid = _direct_project(env, "Retry Site")
    data = _photo(seed=31)
    photo_id = _register_direct_photo(env, pid, data)
    queue = [
        {"op": "annotation_create", "operation_id": "rt-1",
         "payload": {"operation_id": "rt-1", "local_id": "lan",
                     "server_photo_id": photo_id,
                     "annotation_type": "text",
                     "norm_x1": 0.2, "norm_y1": 0.3,
                     "text_content": "check torque"}},
        {"op": "issue_create", "operation_id": "rt-2",
         "payload": {"operation_id": "rt-2", "local_id": "lis",
                     "server_project_id": pid,
                     "title": "Retry defect", "severity": "medium"}},
        {"op": "issue_update", "operation_id": "rt-3",
         "payload": {"operation_id": "rt-3",
                     "server_issue_id": "local:lis",
                     "expected_revision": 1,
                     "description": "offline edit"}},
    ]
    # first attempt: the response is "lost" (client never sees it)
    r = env.client.post("/api/v1/sync/batch",
                        json={"operations": queue}, headers=env.H)
    assert r.status_code == 200
    first = r.json()
    assert all(x["status"] == "applied" for x in first["receipts"])
    iss_id = first["id_map"]["lis"]

    # meanwhile ANOTHER device already bumped the revision to 3
    env.client.patch(f"/api/v1/issues/{iss_id}", json={
        "expected_revision": 2, "description": "remote edit 1"},
        headers=env.H)
    env.client.patch(f"/api/v1/issues/{iss_id}", json={
        "expected_revision": 3, "description": "remote edit 2"},
        headers=env.H)

    # retry: the OLD queue replays duplicate; the STALE update op
    # (same op id, same content) returns the stored receipt too
    retry = _batch(env, queue)
    assert [x["status"] for x in retry["receipts"]] == \
        ["duplicate", "duplicate", "duplicate"]
    assert retry["id_map"] == first["id_map"]

    # the client then re-fetches, merges and queues a NEW op id
    cur = env.client.get(f"/api/v1/issues/{iss_id}",
                          headers=env.H).json()["revision"]
    merged = {"op": "issue_update", "operation_id": "rt-3-r2",
               "payload": {"operation_id": "rt-3-r2",
                            "server_issue_id": iss_id,
                            "expected_revision": cur,
                            "description":
                                "offline edit (merged after retry)"}}
    resp = _batch(env, [merged])
    assert resp["receipts"][0]["status"] == "applied"
    det = env.client.get(f"/api/v1/issues/{iss_id}",
                          headers=env.H).json()
    assert det["revision"] == cur + 1
    assert "merged after retry" in det["description"]
    # the ORIGINAL bytes were never touched by any of this
    dl = env.client.get(f"/api/v1/photos/{photo_id}/original",
                         headers=env.H)
    assert dl.content == data


# ================================================ E8: trust rules


def test_b02_new_ops_cannot_smuggle_trusted_status(b02_env):
    """Payload fields hinting at evidence trust (verification_status,
    verified_at, trust) are NOT part of any annotation/issue schema:
    they are ignored, and no evidence_link row is created or altered
    by the sync engine."""
    env = b02_env
    pid = _direct_project(env, "Trust Site")
    photo_id = _register_direct_photo(env, pid, _photo(seed=41))
    ops = [
        {"op": "annotation_create", "operation_id": "tr-1",
         "payload": {"operation_id": "tr-1", "local_id": "lan",
                     "server_photo_id": photo_id,
                     "annotation_type": "arrow",
                     "norm_x1": 0.1, "norm_y1": 0.1,
                     "norm_x2": 0.4, "norm_y2": 0.4,
                     "verification_status": "verified",
                     "verified_at": "2026-11-25T00:00:00+00:00"}},
        {"op": "issue_create", "operation_id": "tr-2",
         "payload": {"operation_id": "tr-2", "local_id": "lis",
                     "server_project_id": pid,
                     "title": "Trust probe", "severity": "low",
                     "verification_status": "verified",
                     "trust": True}},
    ]
    resp = _batch(env, ops)
    assert [x["status"] for x in resp["receipts"]] == \
        ["applied", "applied"]
    # the stored rows carry no trust state
    conn = _sql(env)
    ev = conn.execute(
        "SELECT COUNT(*) FROM mvp_evidence_links").fetchone()[0]
    ann_row = conn.execute(
        "SELECT * FROM mvp_annotations WHERE annotation_id = ?",
        (resp["id_map"]["lan"],)).fetchone()
    cols = ann_row.keys()
    conn.close()
    assert ev == 0
    assert not any("verification" in c or "trust" in c for c in cols)
    # events never carry trust actions either
    actions = {i["action"] for i in _events(env)["items"]}
    assert actions <= {"created", "updated", "archived", "bound",
                       "status_changed", "soft_deleted",
                       "photo_linked", "photo_unlinked"}


# ============================================== E9: full day cap


def test_b02_full_day_scenario_within_batch_cap(b02_env):
    """A full field day queues to exactly the 50-op cap: 1 project +
    31 photos + 10 annotations + 3 issues + transitions + before/
    progress/after links, one round-trip with local refs; 51 ops is a
    422; the whole day replays duplicate; originals stay
    byte-identical; the event stream carries every entity."""
    env = b02_env
    # three photos get REAL bytes (uploaded later); the rest register
    # with synthetic hashes and no upload
    real: dict[int, bytes] = {}
    for i in (0, 7, 19):
        real[i] = _photo(seed=i * 3)
    ops = [{"op": "project_create", "operation_id": "fd-pc",
             "payload": {"operation_id": "fd-pc", "local_id": "lproj",
                         "name": "B02 Full Day",
                         "site_address": "Km 40+300"}}]
    for i in range(31):
        data = real.get(i)
        sha = (hashlib.sha256(data).hexdigest() if data
               else hashlib.sha256(f"b02-{i}".encode()).hexdigest())
        ops.append({"op": "photo_register",
                     "operation_id": f"fd-pr-{i}",
                     "payload": {"operation_id": f"fd-pr-{i}",
                                  "local_id": f"lph{i}",
                                  "server_project_id": "local:lproj",
                                  "idempotency_key": f"fd-ik-{i}",
                                  "original_sha256": sha,
                                  "original_filename":
                                      f"IMG_{i:04d}.jpg",
                                  "mime_type": "image/jpeg",
                                  "byte_size": len(data) if data else 1024,
                                  "captured_at": "2026-11-25 08:30",
                                  "device_id": "iPhone15,3"}})
    for i in range(10):
        ops.append({"op": "annotation_create",
                     "operation_id": f"fd-an-{i}",
                     "payload": {"operation_id": f"fd-an-{i}",
                                  "local_id": f"lan{i}",
                                  "server_photo_id": f"local:lph{i}",
                                  "annotation_type": "number_marker",
                                  "norm_x1": 0.5, "norm_y1": 0.5,
                                  "marker_number": i + 1}})
    for i in range(3):
        ops.append({"op": "issue_create", "operation_id": f"fd-is-{i}",
                     "payload": {"operation_id": f"fd-is-{i}",
                                  "local_id": f"lis{i}",
                                  "server_project_id": "local:lproj",
                                  "title": f"Defect {i}",
                                  "severity":
                                      ("low", "high", "medium")[i]}})
    flow = []
    for i in range(2):
        flow.append({"op": "issue_transition",
                     "operation_id": f"fd-tr-{i}",
                     "payload": {"operation_id": f"fd-tr-{i}",
                                  "server_issue_id": f"local:lis{i}",
                                  "expected_revision": 1,
                                  "target_status": "in_progress"}})
    for i, role in ((0, "before"), (1, "progress"), (2, "after")):
        flow.append({"op": "issue_photo_link",
                     "operation_id": f"fd-ln-{role}",
                     "payload": {"operation_id": f"fd-ln-{role}",
                                  "server_issue_id": "local:lis0",
                                  "server_photo_id": f"local:lph{i}",
                                  "role": role}})
    ops.extend(flow)
    assert len(ops) == 50                        # exactly at the cap

    resp = _batch(env, ops)
    assert all(x["status"] == "applied" for x in resp["receipts"]), \
        [x for x in resp["receipts"] if x["status"] != "applied"]
    assert resp["batch_received"] == 50

    # 51 ops -> 422, nothing applied
    overflow = ops + [{"op": "project_create",
                        "operation_id": "fd-ov",
                        "payload": {"operation_id": "fd-ov",
                                     "local_id": "lproj-ov",
                                     "name": "Overflow"}}]
    r = env.client.post("/api/v1/sync/batch",
                         json={"operations": overflow}, headers=env.H)
    assert r.status_code == 422
    conn = sqlite3.connect(str(env.db))
    assert conn.execute(
        "SELECT COUNT(*) FROM mvp_sync_ops WHERE operation_id = "
        "'fd-ov'").fetchone()[0] == 0
    conn.close()

    # upload the REAL bytes and prove immutability
    for i, data in real.items():
        photo_id = resp["id_map"][f"lph{i}"]
        up = env.client.put(f"/api/v1/photos/{photo_id}/original",
                             content=data,
                             headers={**env.H,
                                      "Content-Type":
                                          "application/octet-stream"})
        assert up.status_code == 200, up.text
        dl = env.client.get(f"/api/v1/photos/{photo_id}/original",
                             headers=env.H)
        assert dl.content == data

    # the whole day replays duplicate with a stable id_map
    replay = _batch(env, ops)
    assert all(x["status"] == "duplicate" for x in replay["receipts"])
    assert replay["id_map"] == resp["id_map"]

    # event stream: every entity type is present and ordered
    stream = _events(env, limit=200)
    while stream["has_more"]:
        stream_next = _events(env, cursor=stream["cursor"], limit=200)
        assert stream_next["items"], "no progress"
        stream = {"items": stream["items"] + stream_next["items"],
                  "cursor": stream_next["cursor"],
                  "has_more": stream_next["has_more"]}
    types = [i["entity_type"] for i in stream["items"]]
    assert types.count("project") == 1
    assert types.count("photo") == 31
    assert types.count("annotation") == 10
    assert types.count("issue") == 3 + 2 + 3     # 3 create + 2 tr + 3 ln
    seqs = [i["seq"] for i in stream["items"]]
    assert seqs == sorted(seqs)

    # before/progress/after roles landed on issue 0
    iss0 = resp["id_map"]["lis0"]
    links = env.client.get(f"/api/v1/issues/{iss0}/photos",
                            headers=env.H).json()
    roles = {l["role"] for l in links["items"]}
    assert roles == {"before", "progress", "after"}
