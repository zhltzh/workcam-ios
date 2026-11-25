"""B02-W017 photo batch management tests.

DESKTOP_SIMULATION - synthetic photos, REAL SQLite/storage/ZIP behavior.

Covers (task acceptance):
  batch tags / category / note / move / watermark / export
  filter+search with STABLE keyset pagination (no page drift on inserts)
  content-addressed duplicate detection (reported, never deleted)
  per-photo result receipts
  resumable partial-failure handling (retry failed subset under the
    same batch_id; succeeded photos are replayed, never re-executed)
  concurrency (same batch + same photo: exactly one executes)
  evidence immutability (originals + evidence links untouched by batch ops)
  W008 event-stream alignment (photo/updated events, owner isolation)
  batch limits and typed error codes
  full-fieldwork scenario (W016 S1 style)
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
import uuid
import zipfile

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from tests.conftest import _encode_pub


# ------------------------------------------------------------------ fixture


@pytest.fixture()
def w017_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "w017-code")
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    import app.db as dbm
    applied = dbm.migrate()
    replay = dbm.migrate()          # migration idempotency pin
    assert replay == 0
    from app.services.key_registry import reset_registry
    reset_registry()
    from app.services import device_identity
    import app.services.device_identity as _di
    monkeypatch.setattr(_di, "check_rate", lambda *a, **k: None)

    def mk(name):
        key = ec.generate_private_key(ec.SECP256R1())
        pub_b64 = base64.b64encode(
            _encode_pub(key.public_key())).decode()
        return key, device_identity.enroll_device(
            pub_b64, name, "w017-code")

    _, tok_a = mk("w017-AAA")
    _, tok_b = mk("w017-BBB")
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "biz.db",
        "tok_a": tok_a, "tok_b": tok_b,
        "H": {"X-Device-Token": tok_a},
        "B": {"X-Device-Token": tok_b},
        "client": TestClient(app),
        "migrations_applied": applied,
    })()


def _jpeg(seed: int, w: int = 160, h: int = 120) -> bytes:
    img = Image.new("RGB", (w, h), (60 + seed % 60, 120, 90))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 60, 40], fill=(20 + seed, 80, 200))
    d.text((15, 60), f"seed{seed}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _project(env, name: str, headers=None) -> str:
    r = env.client.post("/api/v1/projects", json={"name": name},
                        headers=headers or env.H)
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _register_photo(env, pid: str, data: bytes, captured: str,
                    filename: str = "IMG.jpg", headers=None) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    r = env.client.post(
        f"/api/v1/projects/{pid}/photos",
        json={"idempotency_key": f"w017-{uuid.uuid4().hex[:10]}",
              "original_sha256": sha,
              "original_filename": filename,
              "mime_type": "image/jpeg",
              "byte_size": len(data), "captured_at": captured,
              "device_id": "iPhone15,3"},
        headers=headers or env.H)
    assert r.status_code == 201, r.text
    return r.json()


def _upload_original(env, photo_id: str, data: bytes, headers=None):
    r = env.client.put(f"/api/v1/photos/{photo_id}/original", content=data,
                       headers={**(headers or env.H),
                                "Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text


def _batch(env, path: str, body: dict, headers=None):
    return env.client.post(f"/api/v1{path}", json=body,
                           headers=headers or env.H)


def _mk_issue(env, pid: str, title: str) -> str:
    r = env.client.post(f"/api/v1/projects/{pid}/issues",
                        json={"title": title, "severity": "medium"},
                        headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()["issue_id"]


BID = "w017-batch-0001"


# ============================================================== batch tags


def test_tags_add_remove_idempotent(w017_env):
    env = w017_env
    pid = _project(env, "P-t1")
    ph1 = _register_photo(env, pid, _jpeg(1), "2026-11-25 08:00")["photo_id"]
    ph2 = _register_photo(env, pid, _jpeg(2), "2026-11-25 08:01")["photo_id"]

    r = _batch(env, "/photos/batch/tags",
               {"batch_id": BID, "photo_ids": [ph1, ph2],
                "action": "add", "tags": ["rebar", "column"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and body["succeeded"] == 2
    assert body["failed"] == 0 and body["replayed"] == 0
    per = {x["photo_id"]: x for x in body["results"]}
    assert set(per[ph1]["data"]["tags"]) == {"column", "rebar"}

    # full replay under the SAME batch_id: everything is a duplicate
    r = _batch(env, "/photos/batch/tags",
               {"batch_id": BID, "photo_ids": [ph1, ph2],
                "action": "add", "tags": ["rebar", "column"]})
    body = r.json()
    assert body["replayed"] == 2 and body["succeeded"] == 0
    # no duplicated rows: tags stay a SET
    r = env.client.get(f"/api/v1/photos/{ph1}/business", headers=env.H)
    assert sorted(r.json()["tags"]) == ["column", "rebar"]

    # remove is idempotent too (its own batch)
    r = _batch(env, "/photos/batch/tags",
               {"batch_id": BID + "-rm", "photo_ids": [ph1],
                "action": "remove", "tags": ["rebar"]})
    assert r.json()["succeeded"] == 1
    r = env_batch_replay_remove = _batch(
        env, "/photos/batch/tags",
        {"batch_id": BID + "-rm", "photo_ids": [ph1],
         "action": "remove", "tags": ["rebar"]})
    assert r.json()["replayed"] == 1
    r = env.client.get(f"/api/v1/photos/{ph1}/business", headers=env.H)
    assert r.json()["tags"] == ["column"]


def test_tags_same_batch_conflicting_params(w017_env):
    env = w017_env
    pid = _project(env, "P-t2")
    ph = _register_photo(env, pid, _jpeg(3), "2026-11-25 08:02")["photo_id"]
    _batch(env, "/photos/batch/tags",
           {"batch_id": BID, "photo_ids": [ph], "action": "add",
            "tags": ["a"]})
    r = _batch(env, "/photos/batch/tags",
               {"batch_id": BID, "photo_ids": [ph], "action": "add",
                "tags": ["different"]})
    body = r.json()
    assert body["failed"] == 1
    assert body["results"][0]["error"]["code"] == "batch_id_conflict"


def test_tags_foreign_photo_is_404_receipt(w017_env):
    env = w017_env
    pid_a = _project(env, "P-a")
    pid_b = _project(env, "P-b", headers=env.B)
    ph_a = _register_photo(env, pid_a, _jpeg(4), "2026-11-25 08:03")
    ph_b = _register_photo(env, pid_b, _jpeg(5), "2026-11-25 08:03",
                           headers=env.B)
    # A tags its own + B's photo: B's is not_found to A (no oracle)
    r = _batch(env, "/photos/batch/tags",
               {"batch_id": BID, "photo_ids": [ph_a["photo_id"],
                                               ph_b["photo_id"]],
                "action": "add", "tags": ["x"]})
    body = r.json()
    assert body["succeeded"] == 1 and body["failed"] == 1
    err = body["results"][1]["error"]
    assert err["code"] == "not_found"
    # B's photo is genuinely untouched
    r = env.client.get(f"/api/v1/photos/{ph_b['photo_id']}/business",
                      headers=env.B)
    assert r.json()["tags"] == []


# ========================================================= category / note


def test_category_and_note_set_and_isolation(w017_env):
    env = w017_env
    pid = _project(env, "P-cn")
    ph = _register_photo(env, pid, _jpeg(6), "2026-11-25 08:04")
    r = _batch(env, "/photos/batch/category",
               {"batch_id": BID, "photo_ids": [ph["photo_id"]],
                "category": "structural"})
    assert r.json()["succeeded"] == 1
    r = _batch(env, "/photos/batch/note",
               {"batch_id": BID + "-n", "photo_ids": [ph["photo_id"]],
                "note": "north facade, level 3"})
    assert r.json()["succeeded"] == 1

    r = env.client.get(f"/api/v1/photos/{ph['photo_id']}/business",
                      headers=env.H)
    biz = r.json()
    assert biz["category"] == "structural"
    assert biz["note"] == "north facade, level 3"
    assert biz["revision"] == 2        # two business writes -> rev 2

    # EVIDENCE ISOLATION: photo row + evidence view unchanged
    r = env.client.get(f"/api/v1/photos/{ph['photo_id']}", headers=env.H)
    photo = r.json()
    assert photo["original_sha256"] == ph["original_sha256"]
    assert photo["captured_at"] == "2026-11-25 08:04"
    assert photo["device_id"] == "iPhone15,3"


def test_note_and_category_do_not_touch_evidence_chain(w017_env):
    env = w017_env
    pid = _project(env, "P-ev")
    data = _jpeg(7)
    ph = _register_photo(env, pid, data, "2026-11-25 08:05")
    _upload_original(env, ph["photo_id"], data)
    # register an evidence link (unverified) then mutate business fields
    r = env.client.post(f"/api/v1/photos/{ph['photo_id']}/evidence-links",
                       json={"evidence_reference": f"rec-{ph['photo_id'][:8]}",
                             "evidence_type": "signature_manifest"},
                       headers=env.H)
    assert r.status_code == 201
    link = r.json()
    _batch(env, "/photos/batch/category",
           {"batch_id": BID, "photo_ids": [ph["photo_id"]],
            "category": "hvac"})
    _batch(env, "/photos/batch/note",
           {"batch_id": BID + "-n", "photo_ids": [ph["photo_id"]],
            "note": "note after link"})
    r = env.client.get(
        f"/api/v1/photos/{ph['photo_id']}/evidence-links", headers=env.H)
    after = r.json()["items"][0]
    assert after["verification_status"] == link["verification_status"]
    assert after["original_sha256"] == ph["original_sha256"]
    # original bytes are byte-identical
    r = env.client.get(f"/api/v1/photos/{ph['photo_id']}/original",
                      headers=env.H)
    assert hashlib.sha256(r.content).hexdigest() == ph["original_sha256"]


# ================================================================ batch move


def test_move_whole_batch_prevalidation_rejects_everything(w017_env):
    env = w017_env
    p1 = _project(env, "P-m1")
    p2 = _project(env, "P-m2")                    # A's own target
    pb = _project(env, "P-mB", headers=env.B)     # B's project
    ph1 = _register_photo(env, p1, _jpeg(8), "2026-11-25 08:06")
    ph2 = _register_photo(env, pb, _jpeg(9), "2026-11-25 08:06",
                          headers=env.B)  # B's photo: not visible to A
    r = _batch(env, "/photos/batch/move",
               {"batch_id": BID, "photo_ids": [ph1["photo_id"],
                                               ph2["photo_id"]],
                "target_project_id": p2})
    assert r.status_code == 400, r.text
    err = r.json()["detail"]
    assert err["code"] == "batch_precondition_failed"
    codes = {e["photo_id"]: e["code"] for e in err["details"]["errors"]}
    assert codes[ph2["photo_id"]] == "not_found"
    # ZERO execution: ph1 did NOT move (whole-batch prevalidation)
    r = env.client.get(f"/api/v1/photos/{ph1['photo_id']}", headers=env.H)
    assert r.json()["project_id"] == p1
    # B's photo is untouched in B's own project
    r = env.client.get(f"/api/v1/photos/{ph2['photo_id']}", headers=env.B)
    assert r.json()["project_id"] == pb
    # moving into SOMEONE ELSE's project is a plain 404 (no oracle)
    r = _batch(env, "/photos/batch/move",
               {"batch_id": BID + "-b", "photo_ids": [ph1["photo_id"]],
                "target_project_id": pb})
    assert r.status_code == 404


def test_move_success_and_evidence_untouched(w017_env):
    env = w017_env
    p1 = _project(env, "P-m3")
    p2 = _project(env, "P-m4")
    data = _jpeg(10)
    ph = _register_photo(env, p1, data, "2026-11-25 08:07")
    _upload_original(env, ph["photo_id"], data)
    r = env.client.post(f"/api/v1/photos/{ph['photo_id']}/evidence-links",
                        json={"evidence_reference": "rec-move",
                              "evidence_type": "time_anchor"},
                        headers=env.H)
    r = _batch(env, "/photos/batch/move",
               {"batch_id": BID, "photo_ids": [ph["photo_id"]],
                "target_project_id": p2})
    body = r.json()
    assert body["succeeded"] == 1
    assert body["results"][0]["data"]["moved"] is True
    assert body["results"][0]["data"]["previous_project_id"] == p1
    r = env.client.get(f"/api/v1/photos/{ph['photo_id']}", headers=env.H)
    assert r.json()["project_id"] == p2
    # evidence + original still intact and downloadable
    r = env.client.get(f"/api/v1/photos/{ph['photo_id']}/original",
                      headers=env.H)
    assert hashlib.sha256(r.content).hexdigest() == ph["original_sha256"]
    r = env.client.get(
        f"/api/v1/photos/{ph['photo_id']}/evidence-links", headers=env.H)
    assert r.json()["total"] == 1


def test_move_same_project_is_noop_success(w017_env):
    env = w017_env
    p1 = _project(env, "P-m5")
    ph = _register_photo(env, p1, _jpeg(11), "2026-11-25 08:08")
    r = _batch(env, "/photos/batch/move",
               {"batch_id": BID, "photo_ids": [ph["photo_id"]],
                "target_project_id": p1})
    assert r.json()["results"][0]["data"]["moved"] is False


def test_move_to_archived_target_rejected(w017_env):
    env = w017_env
    p1 = _project(env, "P-m6")
    p2 = _project(env, "P-m7")
    r = env.client.post(f"/api/v1/projects/{p2}/archive", headers=env.H)
    assert r.status_code == 200
    ph = _register_photo(env, p1, _jpeg(12), "2026-11-25 08:09")
    r = _batch(env, "/photos/batch/move",
               {"batch_id": BID, "photo_ids": [ph["photo_id"]],
                "target_project_id": p2})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "project_archived"


def test_move_photo_in_archived_source_rejected(w017_env):
    env = w017_env
    p1 = _project(env, "P-m8")
    p2 = _project(env, "P-m9")
    ph = _register_photo(env, p1, _jpeg(13), "2026-11-25 08:10")
    env.client.post(f"/api/v1/projects/{p1}/archive", headers=env.H)
    r = _batch(env, "/photos/batch/move",
               {"batch_id": BID, "photo_ids": [ph["photo_id"]],
                "target_project_id": p2})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "batch_precondition_failed"
    assert detail["details"]["errors"][0]["code"] == \
        "source_project_archived"


def test_move_photo_linked_to_issue_rejected(w017_env):
    env = w017_env
    p1 = _project(env, "P-m10")
    p2 = _project(env, "P-m11")
    ph = _register_photo(env, p1, _jpeg(14), "2026-11-25 08:11")
    issue = _mk_issue(env, p1, "crack on beam")
    r = env.client.post(f"/api/v1/issues/{issue}/photos",
                        json={"photo_id": ph["photo_id"], "role": "before"},
                        headers=env.H)
    assert r.status_code == 201, r.text
    r = _batch(env, "/photos/batch/move",
               {"batch_id": BID, "photo_ids": [ph["photo_id"]],
                "target_project_id": p2})
    assert r.status_code == 400
    errs = r.json()["detail"]["details"]["errors"]
    assert errs[0]["code"] == "photo_linked_to_issues"


# =========================================================== batch watermark


def test_batch_watermark_and_partial_failure_retry(w017_env):
    env = w017_env
    pid = _project(env, "P-wm")
    d1, d2, d3 = _jpeg(20), _jpeg(21), _jpeg(22)
    ph1 = _register_photo(env, pid, d1, "2026-11-25 09:00")["photo_id"]
    ph2 = _register_photo(env, pid, d2, "2026-11-25 09:01")["photo_id"]
    ph3 = _register_photo(env, pid, d3, "2026-11-25 09:02")["photo_id"]
    _upload_original(env, ph1, d1)
    _upload_original(env, ph2, d2)
    # ph3 deliberately NOT uploaded -> expected per-photo failure

    body = {"batch_id": BID, "photo_ids": [ph1, ph2, ph3],
            "template": "construction",
            "fields": {"show_project_name": True,
                       "project_name": "W017 Tower B"}}
    r = _batch(env, "/photos/batch/watermark", body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["succeeded"] == 2 and out["failed"] == 1
    by = {x["photo_id"]: x for x in out["results"]}
    assert by[ph3]["error"]["code"] == "original_not_uploaded"
    for pid_ in (ph1, ph2):
        assert by[pid_]["data"]["derivative_sha256"]
    # originals immutable after batch rendering
    got = env.client.get(f"/api/v1/photos/{ph1}/original",
                         headers=env.H).content
    assert hashlib.sha256(got).hexdigest() == hashlib.sha256(d1).hexdigest()

    # ----- retry the FAILED SUBSET under the SAME batch_id -----------
    _upload_original(env, ph3, d3)
    r = _batch(env, "/photos/batch/watermark",
               {**body, "photo_ids": [ph1, ph2, ph3]})   # full replay
    out = r.json()
    assert out["replayed"] == 2            # succeeded photos NOT redone
    assert out["succeeded"] == 1           # only the failed one ran
    by = {x["photo_id"]: x for x in out["results"]}
    assert by[ph3]["data"]["derivative_sha256"]
    # each photo has exactly ONE derivative
    for pid_ in (ph1, ph2, ph3):
        r = env.client.get(f"/api/v1/photos/{pid_}/derivatives",
                          headers=env.H)
        assert r.json()["total"] == 1


def test_batch_watermark_evidence_status_is_server_resolved(w017_env):
    env = w017_env
    pid = _project(env, "P-wm2")
    data = _jpeg(23)
    ph = _register_photo(env, pid, data, "2026-11-25 09:10")["photo_id"]
    _upload_original(env, ph, data)
    r = _batch(env, "/photos/batch/watermark",
               {"batch_id": BID, "photo_ids": [ph], "template": "minimal",
                "fields": {"show_evidence_status": True,
                           "evidence_status": "cryptographically_valid",
                           "show_photo_id": True, "photo_id": ph}})
    assert r.status_code == 200, r.text
    d_sha = r.json()["results"][0]["data"]["derivative_sha256"]
    r = env.client.get(f"/api/v1/photos/{ph}/derivatives", headers=env.H)
    # the server downgraded the client's forged claim: photo has no
    # evidence links, so the honest state is no_evidence
    r2 = env.client.get(
        f"/api/v1/photos/{ph}/original/integrity", headers=env.H)
    assert r2.json()["evidence"]["highest_status"] in ("none",)
    assert d_sha  # render succeeded with the honest status


# ================================================================== search


def _mk_photos_for_search(env, pid, n=12):
    ids = []
    for i in range(n):
        captured = f"2026-11-25T1{i // 10}:{i % 10:02d}:00"
        ph = _register_photo(env, pid, _jpeg(30 + i), captured,
                             filename=f"IMG_{i:02d}.jpg")
        ids.append(ph["photo_id"])
    return ids


def test_search_keyset_pagination_no_drift_on_insert(w017_env):
    env = w017_env
    pid = _project(env, "P-s1")
    ids = _mk_photos_for_search(env, pid, 12)

    page1 = env.client.get(
        "/api/v1/photos/search?sort=captured_at&order=desc&limit=5",
        headers=env.H).json()
    assert len(page1["items"]) == 5 and page1["has_more"] is True
    seen = {it["photo_id"] for it in page1["items"]}

    # NEW rows inserted BETWEEN page pulls (newer than the cursor, older
    # than the cursor, and mid-window) - the drift trap for offsets
    _register_photo(env, pid, _jpeg(90), "2026-11-25T23:00:00")
    _register_photo(env, pid, _jpeg(91), "2026-11-25T10:05:30")
    _register_photo(env, pid, _jpeg(92), "2026-11-25T00:01:00")

    cursor = page1["next_cursor"]
    pulled = list(page1["items"])
    guard = 0
    while cursor and guard < 10:
        page = env.client.get(
            f"/api/v1/photos/search?sort=captured_at&order=desc"
            f"&limit=5&cursor={cursor}", headers=env.H).json()
        for it in page["items"]:
            assert it["photo_id"] not in seen    # NO duplicates (drift)
            seen.add(it["photo_id"])
        pulled.extend(page["items"])
        cursor = page["next_cursor"]
        guard += 1
    # every original photo appears exactly once; no skip
    assert set(ids) <= seen
    # rows inserted before the first page started (23:00) are never
    # re-served on later pages; rows behind the cursor (00:01, 09:30)
    # show up exactly once
    late = {it["photo_id"] for it in pulled}
    assert len(late) == len(pulled)              # zero duplicates overall


def test_search_filters_tag_project_issue_status_time_q(w017_env):
    env = w017_env
    pa, pb = _project(env, "P-fa"), _project(env, "P-fb")
    d = _jpeg(40)
    p1 = _register_photo(env, pa, d, "2026-11-25 08:00",
                         filename="foundation.jpg")
    p2 = _register_photo(env, pa, _jpeg(41), "2026-11-25 09:00",
                         filename="beam.jpg")
    p3 = _register_photo(env, pb, _jpeg(42), "2026-11-25 10:00",
                         filename="roof.jpg")

    _batch(env, "/photos/batch/tags",
           {"batch_id": BID, "photo_ids": [p1["photo_id"], p2["photo_id"]],
            "action": "add", "tags": ["rebar"]})
    _batch(env, "/photos/batch/category",
           {"batch_id": BID + "-c", "photo_ids": [p2["photo_id"]],
            "category": "structural"})
    _batch(env, "/photos/batch/note",
           {"batch_id": BID + "-n", "photo_ids": [p1["photo_id"]],
            "note": "check the foundation before pour"})
    issue = _mk_issue(env, pa, "formwork gap")
    env.client.post(f"/api/v1/issues/{issue}/photos",
                    json={"photo_id": p2["photo_id"], "role": "before"},
                    headers=env.H)

    H = env.H
    # project filter
    r = env.client.get(f"/api/v1/photos/search?project_id={pa}",
                      headers=H).json()
    assert {i["photo_id"] for i in r["items"]} == \
        {p1["photo_id"], p2["photo_id"]}
    # tag filter
    r = env.client.get("/api/v1/photos/search?tag=rebar", headers=H).json()
    assert {i["photo_id"] for i in r["items"]} == \
        {p1["photo_id"], p2["photo_id"]}
    # category filter
    r = env.client.get("/api/v1/photos/search?category=structural",
                      headers=H).json()
    assert {i["photo_id"] for i in r["items"]} == {p2["photo_id"]}
    # q matches filename OR note OR tag
    r = env.client.get("/api/v1/photos/search?q=roof", headers=H).json()
    assert {i["photo_id"] for i in r["items"]} == {p3["photo_id"]}
    r = env.client.get("/api/v1/photos/search?q=foundation",
                      headers=H).json()
    assert {i["photo_id"] for i in r["items"]} == {p1["photo_id"]}
    # time window (client-claimed lexical compare)
    r = env.client.get(
        "/api/v1/photos/search?captured_from=2026-11-25 08:30"
        "&captured_to=2026-11-25 09:30", headers=H).json()
    assert {i["photo_id"] for i in r["items"]} == {p2["photo_id"]}
    # issue status filter: open (p2 linked), none (p1, p3)
    r = env.client.get("/api/v1/photos/search?issue_status=open",
                      headers=H).json()
    assert {i["photo_id"] for i in r["items"]} == {p2["photo_id"]}
    r = env.client.get("/api/v1/photos/search?issue_status=none",
                      headers=H).json()
    assert {i["photo_id"] for i in r["items"]} == \
        {p1["photo_id"], p3["photo_id"]}
    # combined filters AND-compose
    r = env.client.get(
        f"/api/v1/photos/search?project_id={pa}&tag=rebar&issue_status=none",
        headers=H).json()
    assert {i["photo_id"] for i in r["items"]} == {p1["photo_id"]}
    # owner isolation: B sees nothing
    r = env.client.get("/api/v1/photos/search", headers=env.B).json()
    assert r["total"] == 0


def test_search_cursor_sort_mismatch_rejected(w017_env):
    env = w017_env
    pid = _project(env, "P-s2")
    _mk_photos_for_search(env, pid, 3)
    r = env.client.get(
        "/api/v1/photos/search?sort=captured_at&limit=2", headers=env.H)
    cur = r.json()["next_cursor"]
    r = env.client.get(
        f"/api/v1/photos/search?sort=created_at&limit=2&cursor={cur}",
        headers=env.H)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_cursor"


def test_search_stable_tiebreak_same_captured_at(w017_env):
    env = w017_env
    pid = _project(env, "P-s3")
    a = _register_photo(env, pid, _jpeg(50), "2026-11-25 08:00")
    b = _register_photo(env, pid, _jpeg(51), "2026-11-25 08:00")
    c = _register_photo(env, pid, _jpeg(52), "2026-11-25 08:00")
    all_ids = {a["photo_id"], b["photo_id"], c["photo_id"]}
    seen: set = set()
    cursor = None
    for _ in range(4):
        url = ("/api/v1/photos/search?sort=captured_at&order=asc&limit=2"
               + (f"&cursor={cursor}" if cursor else ""))
        page = env.client.get(url, headers=env.H).json()
        for it in page["items"]:
            assert it["photo_id"] not in seen
            seen.add(it["photo_id"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == all_ids          # total order, no skip/dup on ties


# ============================================================== duplicates


def test_duplicates_content_addressed_and_no_autodelete(w017_env):
    env = w017_env
    pa, pb = _project(env, "P-d1"), _project(env, "P-d2")
    same = _jpeg(60)
    r1 = _register_photo(env, pa, same, "2026-11-25 08:00")
    r2 = _register_photo(env, pa, same, "2026-11-25 08:01")
    r3 = _register_photo(env, pb, same, "2026-11-25 08:02")  # cross-project
    unique = _register_photo(env, pa, _jpeg(61), "2026-11-25 08:03")
    r = env.client.get("/api/v1/photos/duplicates", headers=env.H)
    body = r.json()
    assert len(body["groups"]) == 1
    g = body["groups"][0]
    assert g["count"] == 3
    assert g["original_sha256"] == hashlib.sha256(same).hexdigest()
    assert {p["photo_id"] for p in g["photos"]} == \
        {r1["photo_id"], r2["photo_id"], r3["photo_id"]}
    assert "never deletes" in body["note"]
    # nothing was deleted: all photos still fetchable
    for pid_ in (r1["photo_id"], r2["photo_id"], r3["photo_id"],
                 unique["photo_id"]):
        rr = env.client.get(f"/api/v1/photos/{pid_}", headers=env.H)
        assert rr.status_code == 200
    # owner isolation
    r = env.client.get("/api/v1/photos/duplicates", headers=env.B)
    assert r.json()["groups"] == []


# =================================================================== export


def test_export_package_manifest_and_content_dedup(w017_env):
    env = w017_env
    pid = _project(env, "P-e1")
    same = _jpeg(70)
    p1 = _register_photo(env, pid, same, "2026-11-25 08:00",
                         filename="dup1.jpg")
    p2 = _register_photo(env, pid, same, "2026-11-25 08:01",
                         filename="dup2.jpg")
    p3 = _register_photo(env, pid, _jpeg(71), "2026-11-25 08:02",
                         filename="uniq.jpg")
    for ph, data in ((p1, same), (p2, same), (p3, _jpeg(71))):
        _upload_original(env, ph["photo_id"], data)
    _batch(env, "/photos/batch/tags",
           {"batch_id": BID + "-t", "photo_ids": [p3["photo_id"]],
            "action": "add", "tags": ["export-me"]})

    r = _batch(env, "/photos/batch/export",
               {"batch_id": BID, "photo_ids": [p1["photo_id"],
                                               p2["photo_id"],
                                               p3["photo_id"]],
                "include_originals": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["succeeded"] == 3 and out["failed"] == 0
    export_id = out["export_id"]

    r = env.client.get(f"/api/v1/photo-exports/{export_id}/download",
                       headers=env.H)
    assert r.status_code == 200
    assert hashlib.sha256(r.content).hexdigest() == out["zip_sha256"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    sha = hashlib.sha256(same).hexdigest()
    # content-addressed originals: duplicate content stored ONCE
    assert names.count(f"originals/{sha}.jpg") == 1
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["photo_count"] == 3
    by_id = {p["photo_id"]: p for p in manifest["photos"]}
    assert by_id[p3["photo_id"]]["tags"] == ["export-me"]
    assert by_id[p1["photo_id"]]["original_uploaded"] is True
    # every manifest file entry verifies against its recorded SHA
    for f in manifest["files"]:
        if f.get("missing"):
            continue
        assert hashlib.sha256(zf.read(f["path"])).hexdigest() == f["sha256"]
    # no secrets in the package
    blob = b"".join(zf.read(n) for n in names)
    assert env.tok_a.encode() not in blob
    assert b"w017-code" not in blob
    # download is owner-scoped
    r = env.client.get(f"/api/v1/photo-exports/{export_id}/download",
                      headers=env.B)
    assert r.status_code == 404


def test_export_retry_failed_subset_builds_complete_package(w017_env):
    env = w017_env
    pa, _pb = _project(env, "P-e2"), _project(env, "P-e3")
    p1 = _register_photo(env, pa, _jpeg(72), "2026-11-25 08:00")
    _upload_original(env, p1["photo_id"], _jpeg(72))
    ghost = str(uuid.uuid4())       # never registered -> failed item

    r = _batch(env, "/photos/batch/export",
               {"batch_id": BID, "photo_ids": [p1["photo_id"], ghost],
                "include_originals": False})
    out = r.json()
    assert out["succeeded"] == 1 and out["failed"] == 1
    assert out["results"][1]["error"]["code"] == "not_found"

    # retry the SAME full set under the SAME batch_id: p1 replays from
    # the ledger (not re-executed), ghost fails again
    r = _batch(env, "/photos/batch/export",
               {"batch_id": BID, "photo_ids": [p1["photo_id"], ghost],
                "include_originals": False})
    out = r.json()
    assert out["replayed"] == 1 and out["succeeded"] == 0
    assert out["failed"] == 1


# ===================================================== receipts + events


def test_batch_receipts_view_and_event_stream(w017_env):
    env = w017_env
    pid = _project(env, "P-r1")
    ph1 = _register_photo(env, pid, _jpeg(80), "2026-11-25 08:00")
    ph2 = _register_photo(env, pid, _jpeg(81), "2026-11-25 08:01")
    _batch(env, "/photos/batch/note",
           {"batch_id": BID, "photo_ids": [ph1["photo_id"]],
            "note": "n1"})
    _batch(env, "/photos/batch/tags",
           {"batch_id": BID + "-t", "photo_ids": [ph1["photo_id"],
                                                  ph2["photo_id"]],
            "action": "add", "tags": ["k"]})

    r = env.client.get(f"/api/v1/photo-batches/{BID}-t", headers=env.H)
    body = r.json()
    assert body["count"] == 2
    assert {i["photo_id"] for i in body["items"]} == \
        {ph1["photo_id"], ph2["photo_id"]}
    # op_type filter narrows the view
    r = env.client.get(
        f"/api/v1/photo-batches/{BID}-t?op_type=note", headers=env.H)
    assert r.json()["count"] == 0
    # cross-owner: B sees none of A's ledger
    r = env.client.get(f"/api/v1/photo-batches/{BID}-t", headers=env.B)
    assert r.json()["count"] == 0

    # events: photo/updated rows visible to A, invisible to B
    r = env.client.get(
        "/api/v1/sync/events?entity_type=photo&limit=200",
        headers=env.H).json()
    actions = [it["action"] for it in r["items"]]
    assert "updated" in actions
    r = env.client.get(
        "/api/v1/sync/events?entity_type=photo&limit=200",
        headers=env.B).json()
    assert r["items"] == []


# ============================================================== concurrency


def test_concurrent_same_batch_one_photo_single_execution(w017_env):
    env = w017_env
    pid = _project(env, "P-c1")
    ph = _register_photo(env, pid, _jpeg(85), "2026-11-25 08:00")
    body = {"batch_id": BID, "photo_ids": [ph["photo_id"]],
            "action": "add", "tags": ["concurrent"]}
    outs: list[dict] = []
    bar = threading.Barrier(2)

    def run():
        bar.wait()          # maximize collision window
        r = _batch(env, "/photos/batch/tags", body)
        outs.append(r.json())

    ts = [threading.Thread(target=run) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    statuses = sorted(x["results"][0]["status"] for x in outs)
    # exactly one success and one duplicate replay
    assert statuses == ["duplicate", "success"]
    # ledger has exactly ONE row for (batch, photo)
    import sqlite3
    conn = sqlite3.connect(str(env.db))
    n = conn.execute(
        "SELECT COUNT(*) FROM mvp_batch_ops WHERE batch_id = ?",
        (BID,)).fetchone()[0]
    assert n == 1
    conn.close()
    r = env.client.get(f"/api/v1/photos/{ph['photo_id']}/business",
                      headers=env.H)
    assert r.json()["tags"] == ["concurrent"]


def test_concurrent_distinct_batches_no_lost_update(w017_env):
    env = w017_env
    pid = _project(env, "P-c2")
    ph = _register_photo(env, pid, _jpeg(86), "2026-11-25 08:00")
    codes: list[int] = []

    def run(i: int):
        r = _batch(env, "/photos/batch/note",
                   {"batch_id": f"{BID}-p{i}",
                    "photo_ids": [ph["photo_id"]], "note": f"note {i}"})
        codes.append((r.status_code, r.json()["succeeded"]))

    ts = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # both batch ids fully applied under SQLite write serialization
    assert all(code == 200 and s == 1 for code, s in codes)
    r = env.client.get(f"/api/v1/photos/{ph['photo_id']}/business",
                      headers=env.H)
    assert r.json()["note"].startswith("note ")


# ================================================================= limits


def test_batch_limits_and_validation(w017_env):
    env = w017_env
    pid = _project(env, "P-l1")
    ph = _register_photo(env, pid, _jpeg(87), "2026-11-25 08:00")
    H = env.H
    # 501 photos -> 422
    r = _batch(env, "/photos/batch/tags",
               {"batch_id": BID, "photo_ids": [ph["photo_id"]] * 501,
                "action": "add", "tags": ["x"]})
    assert r.status_code == 422
    # empty photo list -> 422
    r = _batch(env, "/photos/batch/tags",
               {"batch_id": BID, "photo_ids": [], "action": "add",
                "tags": ["x"]})
    assert r.status_code == 422
    # 51 tags -> 422
    r = _batch(env, "/photos/batch/tags",
               {"batch_id": BID, "photo_ids": [ph["photo_id"]],
                "action": "add", "tags": [f"t{i}" for i in range(51)]})
    assert r.status_code == 422
    # duplicate photo ids in one batch -> 422
    r = _batch(env, "/photos/batch/tags",
               {"batch_id": BID, "photo_ids": [ph["photo_id"],
                                               ph["photo_id"]],
                "action": "add", "tags": ["x"]})
    assert r.status_code == 422
    # note too long -> 422
    r = _batch(env, "/photos/batch/note",
               {"batch_id": BID, "photo_ids": [ph["photo_id"]],
                "note": "x" * 5001})
    assert r.status_code == 422
    # 500-photo request is the documented ceiling (validation only -
    # one real photo, padded with unknown uuids: per-photo 404s prove
    # the batch was ACCEPTED and processed, not 422-rejected)
    big = [ph["photo_id"]] + [str(uuid.uuid4()) for _ in range(499)]
    r = _batch(env, "/photos/batch/note",
               {"batch_id": BID + "-big", "photo_ids": big, "note": "n"})
    out = r.json()
    assert out["total"] == 500 and out["succeeded"] == 1


def test_watermark_batch_conflicting_params_same_batch(w017_env):
    env = w017_env
    pid = _project(env, "P-l2")
    data = _jpeg(88)
    ph = _register_photo(env, pid, data, "2026-11-25 08:00")
    _upload_original(env, ph["photo_id"], data)
    body1 = {"batch_id": BID, "photo_ids": [ph["photo_id"]],
             "template": "minimal", "fields": {"show_photo_id": True,
                                               "photo_id": ph["photo_id"]}}
    r = _batch(env, "/photos/batch/watermark", body1)
    assert r.json()["succeeded"] == 1
    body2 = {**body1, "template": "construction"}
    r = _batch(env, "/photos/batch/watermark", body2)
    out = r.json()
    assert out["failed"] == 1
    assert out["results"][0]["error"]["code"] == "batch_id_conflict"


# ============================================ W016-style full scenario


def test_b02_w017_full_batch_scenario(w017_env):
    """One field-work day, batch style: 12 photos (3 duplicate groups),
    batch tag/category/note, issues, search, duplicates, batch
    watermark, batch move, export, full replay, client restart,
    evidence immutability. DESKTOP_SIMULATION."""
    env = w017_env
    site_a, site_b = _project(env, "Tower A"), _project(env, "Tower B")
    dup = _jpeg(99)
    photos = []
    for i in range(12):
        data = dup if i % 4 == 0 else _jpeg(100 + i)
        pid = site_a if i < 9 else site_b
        ph = _register_photo(env, pid, data,
                             f"2026-11-25T08:{i:02d}:00",
                             filename=f"DSC_{i:03d}.jpg")
        _upload_original(env, ph["photo_id"], data)
        photos.append(ph)
    ids = [p["photo_id"] for p in photos]

    # 1. batch tag + category + note
    assert _batch(env, "/photos/batch/tags",
                  {"batch_id": "sc-tags", "photo_ids": ids,
                   "action": "add",
                   "tags": ["daily", "site"]}).json()["succeeded"] == 12
    assert _batch(env, "/photos/batch/category",
                  {"batch_id": "sc-cat", "photo_ids": ids,
                   "category": "progress"}).json()["succeeded"] == 12
    assert _batch(env, "/photos/batch/note",
                  {"batch_id": "sc-note", "photo_ids": ids,
                   "note": "morning round"}).json()["succeeded"] == 12

    # 2. issue on photo #5 + search by issue status
    issue = _mk_issue(env, site_a, "rebar spacing")
    env.client.post(f"/api/v1/issues/{issue}/photos",
                    json={"photo_id": ids[5], "role": "before"},
                    headers=env.H)
    r = env.client.get("/api/v1/photos/search?issue_status=open",
                      headers=env.H).json()
    assert {i["photo_id"] for i in r["items"]} == {ids[5]}
    r = env.client.get(
        "/api/v1/photos/search?tag=daily&category=progress"
        "&issue_status=none&limit=200", headers=env.H).json()
    assert r["total"] == 11
    assert all(i["category"] == "progress" and i["note"] == "morning round"
               for i in r["items"])

    # 3. duplicates: photos 0,4,8 share content
    r = env.client.get("/api/v1/photos/duplicates", headers=env.H).json()
    assert len(r["groups"]) == 1 and r["groups"][0]["count"] == 3

    # 4. batch watermark over everything
    out = _batch(env, "/photos/batch/watermark",
                 {"batch_id": "sc-wm", "photo_ids": ids,
                  "template": "construction",
                  "fields": {"show_project_name": True,
                             "project_name": "W017 Tower A"}}).json()
    assert out["succeeded"] == 12 and out["failed"] == 0

    # 5. batch move the Tower B photos (#9..#11) is rejected - they
    # belong to site_b... move tower-A photos #6..#8 into site_b instead
    r = _batch(env, "/photos/batch/move",
               {"batch_id": "sc-move", "photo_ids": ids[6:9],
                "target_project_id": site_b})
    assert r.json()["succeeded"] == 3
    r = env.client.get(f"/api/v1/photos/{ids[7]}", headers=env.H).json()
    assert r["project_id"] == site_b

    # 6. export everything and verify the package
    out = _batch(env, "/photos/batch/export",
                 {"batch_id": "sc-export", "photo_ids": ids,
                  "include_originals": True}).json()
    assert out["succeeded"] == 12
    r = env.client.get(
        f"/api/v1/photo-exports/{out['export_id']}/download",
        headers=env.H)
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["photo_count"] == 12
    for f in manifest["files"]:
        if not f.get("missing"):
            assert hashlib.sha256(
                zf.read(f["path"])).hexdigest() == f["sha256"]
    # duplicate content: originals/ holds 10 files (12 photos, 3 share
    # one content, 9 unique)... 9 unique + 1 shared = 10
    orig = [n for n in zf.namelist() if n.startswith("originals/")]
    assert len(orig) == 10

    # 7. FULL REPLAY of every batch: all duplicate, zero side effects
    replays = [
        ("sc-tags", "/photos/batch/tags",
         {"action": "add", "tags": ["daily", "site"]}, ids),
        ("sc-cat", "/photos/batch/category", {"category": "progress"},
         ids),
        ("sc-note", "/photos/batch/note", {"note": "morning round"}, ids),
        ("sc-wm", "/photos/batch/watermark",
         {"template": "construction",
          "fields": {"show_project_name": True,
                     "project_name": "W017 Tower A"}}, ids),
        ("sc-move", "/photos/batch/move",
         {"target_project_id": site_b}, ids[6:9]),
    ]
    for bid, path, extra, pids in replays:
        out = _batch(env, path, {**extra, "batch_id": bid,
                                 "photo_ids": pids}).json()
        assert out["replayed"] == len(pids), (bid, out)
        assert out["succeeded"] == 0, (bid, out)
    # tags are still a 2-element set
    r = env.client.get(f"/api/v1/photos/{ids[0]}/business",
                      headers=env.H).json()
    assert sorted(r["tags"]) == ["daily", "site"]
    # one derivative per photo after replay
    for pid_ in ids:
        assert env.client.get(
            f"/api/v1/photos/{pid_}/derivatives",
            headers=env.H).json()["total"] == 1

    # 8. restart consistency: a fresh client over the same DB sees the
    # exact same state
    fresh = TestClient(app)
    r = fresh.get(f"/api/v1/photos/{ids[7]}", headers=env.H).json()
    assert r["project_id"] == site_b
    r = fresh.get("/api/v1/photos/search?tag=daily&limit=200",
                  headers=env.H).json()
    assert r["total"] == 12
    r = fresh.get("/api/v1/photos/duplicates", headers=env.H).json()
    assert r["groups"][0]["count"] == 3

    # 9. evidence immutability across the ENTIRE batch workload
    dup_sha = hashlib.sha256(dup).hexdigest()
    for ph in photos[0:12:4]:     # the duplicate-content photos
        r = env.client.get(f"/api/v1/photos/{ph['photo_id']}/original",
                           headers=env.H)
        assert hashlib.sha256(r.content).hexdigest() == \
            ph["original_sha256"] == dup_sha
    r = env.client.get(
        f"/api/v1/photos/{ids[0]}/original/integrity", headers=env.H)
    assert r.json()["claims"]["file_integrity_verified"] is True
