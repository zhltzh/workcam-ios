"""WC-W006 business API tests.

Five groups per the task brief: projects / photos / evidence links /
database / security. Every test uses an isolated DB + isolated device
registry via env overrides; two enrolled devices exercise ownership.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat)
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


@pytest.fixture()
def biz_env(tmp_path, monkeypatch):
    """Isolated business DB + enrolled devices A and B."""
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_DEVICE_REGISTRY",
                       str(tmp_path / "devices.jsonl"))
    import app.db as dbm
    import app.api.identity as ident
    import app.api.upload_auth as ua
    dbm.migrate(tmp_path / "biz.db")

    key_a = ec.generate_private_key(ec.SECP256R1())       # TEST key
    key_b = ec.generate_private_key(ec.SECP256R1())
    pub_a = base64.b64encode(key_a.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    pub_b = base64.b64encode(key_b.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    registry = ua.DeviceRegistry(tmp_path / "devices.jsonl")
    token_a = registry.enroll(pub_a, "device-AAA")
    token_b = registry.enroll(pub_b, "device-BBB")
    ua._batch_owners.clear(); ua._buckets.clear(); ua._challenges.clear()
    return type("E", (), {
        "tmp": tmp_path, "A": {"X-Device-Token": token_a},
        "B": {"X-Device-Token": token_b},
        "db_path": tmp_path / "biz.db",
    })()


def _mk_project(env, name="Site A", **extra) -> dict:
    body = {"name": name, **extra}
    r = client.post("/api/v1/projects", json=body, headers=env.A)
    assert r.status_code == 201, r.text
    return r.json()


def _sha() -> str:
    return hashlib.sha256(b"photo-bytes").hexdigest()


def _register_photo(env, project_id, key="idem-1", sha=None,
                    headers=None) -> object:
    body = {
        "idempotency_key": key,
        "original_sha256": sha or _sha(),
        "original_filename": "IMG_0001.jpg",
        "mime_type": "image/jpeg",
        "byte_size": 1_234_567,
        "captured_at": "2026-11-23T08:00:00+00:00",
        "device_id": "iPhone15,3",
    }
    return client.post(f"/api/v1/projects/{project_id}/photos",
                       json=body, headers=headers or env.A)


# ============================================================ P0: projects


def test_create_and_get_project(biz_env):
    proj = _mk_project(biz_env, name="Bridge N-12",
                       description="Main span", site_address="Nanjing")
    r = client.get(f"/api/v1/projects/{proj['project_id']}",
                   headers=biz_env.A)
    assert r.status_code == 200
    assert r.json()["name"] == "Bridge N-12"
    assert r.json()["status"] == "active"


def test_project_name_validation(biz_env):
    r = client.post("/api/v1/projects", json={"name": "   "},
                    headers=biz_env.A)
    assert r.status_code == 422
    r2 = client.post("/api/v1/projects", json={"name": "x" * 201},
                     headers=biz_env.A)
    assert r2.status_code == 422


def test_project_list_pagination_and_stable_order(biz_env):
    for i in range(5):
        _mk_project(biz_env, name=f"P{i}")
    r = client.get("/api/v1/projects?limit=3&offset=0", headers=biz_env.A)
    body = r.json()
    assert body["total"] == 5 and len(body["items"]) == 3
    order1 = [p["name"] for p in body["items"]]
    r2 = client.get("/api/v1/projects?limit=3&offset=0", headers=biz_env.A)
    assert [p["name"] for p in r2.json()["items"]] == order1   # stable
    r3 = client.get("/api/v1/projects?limit=200", headers=biz_env.A)
    assert len(r3.json()["items"]) <= 100                      # capped


def test_project_patch(biz_env):
    proj = _mk_project(biz_env)
    r = client.patch(f"/api/v1/projects/{proj['project_id']}",
                     json={"description": "updated"}, headers=biz_env.A)
    assert r.status_code == 200
    assert r.json()["description"] == "updated"
    assert r.json()["updated_at"] >= proj["updated_at"]


def test_project_patch_empty_rejected(biz_env):
    proj = _mk_project(biz_env)
    r = client.patch(f"/api/v1/projects/{proj['project_id']}",
                     json={}, headers=biz_env.A)
    assert r.status_code == 400 and "empty_patch" in r.text


def test_archive_idempotent(biz_env):
    proj = _mk_project(biz_env)
    r1 = client.post(f"/api/v1/projects/{proj['project_id']}/archive",
                     headers=biz_env.A)
    assert r1.status_code == 200 and r1.json()["status"] == "archived"
    assert r1.json()["archived_at"] is not None
    r2 = client.post(f"/api/v1/projects/{proj['project_id']}/archive",
                     headers=biz_env.A)
    assert r2.status_code == 200                    # IDEMPOTENT re-archive
    assert r2.json()["archived_at"] == r1.json()["archived_at"]
    # archived projects are immutable
    r3 = client.patch(f"/api/v1/projects/{proj['project_id']}",
                      json={"name": "nope"}, headers=biz_env.A)
    assert r3.status_code == 409


def test_archived_project_rejects_new_photos(biz_env):
    proj = _mk_project(biz_env)
    client.post(f"/api/v1/projects/{proj['project_id']}/archive",
                headers=biz_env.A)
    r = _register_photo(biz_env, proj["project_id"])
    assert r.status_code == 409 and "archived" in r.json()["detail"]["code"]


def test_invalid_uuid_rejected(biz_env):
    r = client.get("/api/v1/projects/not-a-uuid", headers=biz_env.A)
    assert r.status_code == 400 and "invalid_uuid" in r.text


def test_no_physical_delete(biz_env):
    """Projects are archived, never deleted (P0 evidence-preservation)."""
    proj = _mk_project(biz_env)
    r = client.delete(f"/api/v1/projects/{proj['project_id']}",
                      headers=biz_env.A)
    assert r.status_code == 405                    # no delete route at all


# ============================================================ P1: photos


def test_register_and_get_photo(biz_env):
    proj = _mk_project(biz_env)
    r = _register_photo(biz_env, proj["project_id"])
    assert r.status_code == 201
    photo = r.json()
    assert photo["original_sha256"] == _sha()
    # trust annotations: client claims are DATA, not trust
    assert photo["trust_notes"]["device_id"] == "client-claimed, untrusted"
    r2 = client.get(f"/api/v1/photos/{photo['photo_id']}", headers=biz_env.A)
    assert r2.status_code == 200


def test_register_photo_invalid_sha(biz_env):
    proj = _mk_project(biz_env)
    r = _register_photo(biz_env, proj["project_id"],
                        sha="ZZnot-hex")
    assert r.status_code == 422
    r2 = _register_photo(biz_env, proj["project_id"],
                         sha="abcd")              # wrong length
    assert r2.status_code == 422


def test_register_photo_idempotent_replay_and_conflict(biz_env):
    proj = _mk_project(biz_env)
    r1 = _register_photo(biz_env, proj["project_id"], key="same-key")
    assert r1.status_code == 201
    # SAME content, SAME key -> replay returns the SAME photo
    r2 = _register_photo(biz_env, proj["project_id"], key="same-key")
    assert r2.status_code == 201
    assert r2.json()["photo_id"] == r1.json()["photo_id"]
    assert r2.json().get("_idempotent_replay") is True
    # SAME key, DIFFERENT content -> explicit conflict
    r3 = _register_photo(biz_env, proj["project_id"], key="same-key",
                         sha=hashlib.sha256(b"different").hexdigest())
    assert r3.status_code == 409
    assert r3.json()["detail"]["code"] == "idempotency_conflict"


def test_photo_hash_immutable_no_overwrite_path(biz_env):
    """There is no API that can change original_sha256: PATCH on photos
    does not exist (405), and re-registration is caught by idempotency."""
    proj = _mk_project(biz_env)
    r1 = _register_photo(biz_env, proj["project_id"], key="k1")
    photo_id = r1.json()["photo_id"]
    r2 = client.patch(f"/api/v1/photos/{photo_id}",
                      json={"original_sha256": "ff" * 32},
                      headers=biz_env.A)
    assert r2.status_code == 405
    r3 = client.get(f"/api/v1/photos/{photo_id}", headers=biz_env.A)
    assert r3.json()["original_sha256"] == _sha()  # unchanged


def test_photos_listing_paginated(biz_env):
    proj = _mk_project(biz_env)
    for i in range(4):
        _register_photo(biz_env, proj["project_id"], key=f"k{i}")
    r = client.get(f"/api/v1/projects/{proj['project_id']}/photos"
                   "?limit=2", headers=biz_env.A)
    body = r.json()
    assert body["total"] == 4 and len(body["items"]) == 2


# ======================================================= P2: evidence links


def test_evidence_link_binds_photo_hash(biz_env):
    proj = _mk_project(biz_env)
    photo = _register_photo(biz_env, proj["project_id"]).json()
    r = client.post(f"/api/v1/photos/{photo['photo_id']}/evidence-links",
                   json={"evidence_reference": "rec-abc-001",
                         "evidence_type": "signature_manifest"},
                   headers=biz_env.A)
    assert r.status_code == 201
    link = r.json()
    # the link carries the photo's hash AT LINK TIME (P2 #4)
    assert link["original_sha256"] == photo["original_sha256"]
    # fresh links are unverified - trusted status comes from server
    # verification only (P2 #2/#3)
    assert link["verification_status"] == "unverified"
    assert "awaiting" in link["failure_reason"]


def test_evidence_link_cannot_be_forged_via_post(biz_env):
    """There is no field to submit a trusted status - the request schema
    (EvidenceLinkCreate) has exactly two fields; extra fields do not
    reach the DB, and no API writes trusted statuses from input."""
    proj = _mk_project(biz_env)
    photo = _register_photo(biz_env, proj["project_id"]).json()
    r = client.post(f"/api/v1/photos/{photo['photo_id']}/evidence-links",
                   json={"evidence_reference": "rec-1",
                         "evidence_type": "signature_manifest",
                         "verification_status": "cryptographically_valid"},
                   headers=biz_env.A)
    assert r.status_code == 201
    assert r.json()["verification_status"] == "unverified"   # forged value ignored


def test_evidence_link_listing(biz_env):
    proj = _mk_project(biz_env)
    photo = _register_photo(biz_env, proj["project_id"]).json()
    client.post(f"/api/v1/photos/{photo['photo_id']}/evidence-links",
                json={"evidence_reference": "rec-1",
                      "evidence_type": "time_anchor"},
                headers=biz_env.A)
    r = client.get(f"/api/v1/photos/{photo['photo_id']}/evidence-links",
                   headers=biz_env.A)
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["evidence_type"] == "time_anchor"


def test_evidence_link_invalid_type_rejected(biz_env):
    proj = _mk_project(biz_env)
    photo = _register_photo(biz_env, proj["project_id"]).json()
    r = client.post(f"/api/v1/photos/{photo['photo_id']}/evidence-links",
                   json={"evidence_reference": "rec-1",
                         "evidence_type": "c2pa_magic"},
                   headers=biz_env.A)
    assert r.status_code == 422


# ======================================================= P3: database


def test_foreign_keys_enforced(biz_env):
    """Direct DB write with a dangling project_id must fail (FK ON)."""
    import app.db as dbm
    with pytest.raises(sqlite3.IntegrityError):
        with dbm.db(biz_env.db_path) as conn:
            conn.execute(
                "INSERT INTO mvp_photos (photo_id, project_id, "
                "idempotency_key, original_sha256, original_filename, "
                "mime_type, byte_size, captured_at, received_at, device_id, "
                "created_at) VALUES ('x', 'no-such-project', 'k', "
                "?, 'f', 'image/jpeg', 1, 't', 't', 'd', 't')",
                (_sha(),))


def test_transaction_rollback_on_error(biz_env):
    """A mid-transaction failure must leave NO partial data."""
    import app.db as dbm
    before = None
    with dbm.db(biz_env.db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM mvp_projects").fetchone()[0]
    with pytest.raises(Exception):
        with dbm.db(biz_env.db_path) as conn:
            conn.execute("INSERT INTO mvp_projects (project_id, owner_token, "
                         "name, description, site_address, status, "
                         "created_at, updated_at) VALUES "
                         "('p1', 'tok', 'ok', '', '', 'active', 't', 't')")
            # force failure AFTER the insert
            raise RuntimeError("boom")
    with dbm.db(biz_env.db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM mvp_projects").fetchone()[0]
    assert after == before                    # rolled back completely


def test_backup_restore_roundtrip(biz_env):
    proj = _mk_project(biz_env)
    _register_photo(biz_env, proj["project_id"])
    photo = _register_photo(biz_env, proj["project_id"], key="k2").json()
    client.post(f"/api/v1/photos/{photo['photo_id']}/evidence-links",
                json={"evidence_reference": "rec-9",
                      "evidence_type": "signature_manifest"},
                headers=biz_env.A)

    import app.db as dbm
    backup = biz_env.tmp / "backup.db"
    dbm.backup_to(backup, biz_env.db_path)
    info = dbm.verify_backup(backup)      # integrity + FK audit
    assert info["counts"] == {"projects": 1, "photos": 2, "evidence_links": 1}

    restored = biz_env.tmp / "restored.db"
    rinfo = dbm.restore_from(backup, restored)
    assert rinfo["counts"] == info["counts"]

    # the RESTORED database serves the API (fresh DB path env switch)
    import os
    old = os.environ.get("WORKCAM_BUSINESS_DB")
    try:
        os.environ["WORKCAM_BUSINESS_DB"] = str(restored)
        r = client.get(f"/api/v1/projects/{proj['project_id']}",
                       headers=biz_env.A)
        assert r.status_code == 200 and r.json()["name"] == "Site A"
        r2 = client.get(f"/api/v1/projects/{proj['project_id']}/photos",
                        headers=biz_env.A)
        assert r2.json()["total"] == 2
        # archived projects keep photos (P3: archive never deletes)
    finally:
        os.environ["WORKCAM_BUSINESS_DB"] = old


def test_restore_refuses_nonempty_target(biz_env):
    import app.db as dbm
    backup = biz_env.tmp / "b2.db"
    dbm.backup_to(backup, biz_env.db_path)
    with pytest.raises(ValueError, match="non-empty"):
        dbm.restore_from(backup, biz_env.db_path)  # restore onto live DB


# ======================================================= P4: security


def test_unauthenticated_request_rejected(biz_env):
    r = client.get("/api/v1/projects")
    assert r.status_code == 401
    r2 = client.post("/api/v1/projects", json={"name": "x"})
    assert r2.status_code == 401


def test_unknown_token_rejected(biz_env):
    r = client.get("/api/v1/projects",
                   headers={"X-Device-Token": "ff" * 32})
    assert r.status_code == 401


def test_cross_subject_access_is_404_not_403(biz_env):
    """THE anti-oracle requirement: B reading A's project gets the SAME
    404 as a nonexistent id - B cannot even learn that it exists."""
    proj = _mk_project(biz_env)
    r_owner = client.get(f"/api/v1/projects/{proj['project_id']}",
                         headers=biz_env.B)
    r_none = client.get(f"/api/v1/projects/{str(proj['project_id'])[:8]}-0000-0000-0000-000000000000",
                        headers=biz_env.B)
    assert r_owner.status_code == 404
    assert r_none.status_code == 404
    assert r_owner.json()["detail"]["code"] == r_none.json()["detail"]["code"]


def test_cross_subject_cannot_list_others(biz_env):
    _mk_project(biz_env, name="A-secret")
    r = client.get("/api/v1/projects", headers=biz_env.B)
    assert r.status_code == 200
    assert r.json()["total"] == 0                # B sees only B's


def test_cross_subject_cannot_write_photo(biz_env):
    proj = _mk_project(biz_env)
    r = _register_photo(biz_env, proj["project_id"], headers=biz_env.B)
    assert r.status_code == 404                   # not 403: no oracle


def test_forged_device_id_is_not_identity(biz_env):
    """client device_id is DATA: B cannot write into A's project by
    CLAIMING A's device_id in the photo body."""
    proj = _mk_project(biz_env)
    body = {
        "idempotency_key": "spoof",
        "original_sha256": _sha(),
        "original_filename": "x.jpg",
        "mime_type": "image/jpeg",
        "byte_size": 100,
        "captured_at": "t",
        "device_id": "device-AAA",            # forged claim
    }
    r = client.post(f"/api/v1/projects/{proj['project_id']}/photos",
                   json=body, headers=biz_env.B)
    assert r.status_code == 404                # ownership is the token, not the claim


def test_cross_subject_cannot_read_photo_or_links(biz_env):
    proj = _mk_project(biz_env)
    photo = _register_photo(biz_env, proj["project_id"]).json()
    r1 = client.get(f"/api/v1/photos/{photo['photo_id']}",
                    headers=biz_env.B)
    assert r1.status_code == 404
    r2 = client.get(f"/api/v1/photos/{photo['photo_id']}/evidence-links",
                    headers=biz_env.B)
    assert r2.status_code == 404


def test_unified_error_structure(biz_env):
    r = client.get("/api/v1/projects/not-a-uuid", headers=biz_env.A)
    detail = r.json()["detail"]
    assert set(detail.keys()) == {"code", "message", "details"}


def test_openapi_documented(biz_env):
    """P4 #8: the /api/v1 surface must appear in the OpenAPI schema.
    (FastAPI groups same-path different-method routes under one key;
    assert on unique paths AND total operations.)"""
    spec = client.get("/openapi.json").json()
    v1_paths = [p for p in spec["paths"] if p.startswith("/api/v1")]
    ops = sum(len(spec["paths"][p]) for p in v1_paths)
    assert len(v1_paths) == 6          # unique resource paths
    assert ops >= 10                   # 5P+3Ph+2L operations present
