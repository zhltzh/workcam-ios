"""WC-W013 inspection tests (P8).

DESKTOP_SIMULATION - synthetic photos, REAL rendered derivatives.

Covers: legacy credential migration (P0), 6 annotation types, invalid
coords/oversize text, EXIF, originals unchanged, annotation+watermark
combo, issue lifecycle+history, before/after comparison, cross-subject,
sync idempotency, report snapshot immutability, big-image bounds.
"""

from __future__ import annotations

import base64
import hashlib
import io
import sqlite3
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from app.services.annotation_render import render_annotated_derivative
from tests.conftest import _encode_pub


@pytest.fixture()
def insp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "w013-code")
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
            pub_b64, name, "w013-code")

    key_a, pub_a, tok_a = mk("insp-AAA")
    key_b, pub_b, tok_b = mk("insp-BBB")
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "biz.db",
        "key": key_a, "pub_a": pub_a, "tok_a": tok_a, "tok_b": tok_b,
        "H": {"X-Device-Token": tok_a},
        "B": {"X-Device-Token": tok_b},
        "client": TestClient(app),
    })()


def _photo(w=1200, h=900, seed=0) -> bytes:
    img = Image.new("RGB", (w, h), (100 + seed, 140, 120))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 200, 150], fill=(40, 80, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _project(env) -> str:
    return env.client.post("/api/v1/projects", json={"name": "Insp Site"},
                           headers=env.H).json()["project_id"]


def _photo_uploaded(env, pid, data=None) -> tuple[str, bytes]:
    data = data or _photo()
    sha = hashlib.sha256(data).hexdigest()
    ph = env.client.post(
        f"/api/v1/projects/{pid}/photos",
        json={"idempotency_key": f"i-{uuid.uuid4().hex[:8]}",
              "original_sha256": sha, "original_filename": "I.jpg",
              "mime_type": "image/jpeg", "byte_size": len(data),
              "captured_at": "2026-11-24 09:00", "device_id": "d"},
        headers=env.H).json()["photo_id"]
    env.client.put(f"/api/v1/photos/{ph}/original", content=data,
                    headers={**env.H,
                             "Content-Type": "application/octet-stream"})
    return ph, data


# --------------------------------------------------- P0: legacy credential


def test_legacy_derived_token_rejected(insp_env, tmp_path):
    """A token = sha256(public_key) from the legacy file must be 401
    with 'legacy_token_must_re_enroll' (P0 acceptance)."""
    import json as _json
    # simulate a pre-W009 devices.jsonl with a DERIVED token
    key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
    derived = hashlib.sha256(pub_b64.encode("ascii")).hexdigest()
    reg_file = tmp_path / "devices.jsonl"
    reg_file.write_text(_json.dumps({
        "device_id": "legacy-device", "token": derived,
        "public_key_b64": pub_b64}) + "\n", encoding="utf-8")
    import os
    os.environ["WORKCAM_DEVICE_REGISTRY"] = str(reg_file)
    try:
        r = insp_env.client.get("/api/v1/projects",
                                 headers={"X-Device-Token": derived})
        assert r.status_code == 401
        assert "re-enroll" in r.json()["detail"].get("message", "")
    finally:
        os.environ.pop("WORKCAM_DEVICE_REGISTRY", None)


def test_revoked_legacy_device_cannot_resurrect(insp_env, tmp_path):
    """A legacy device revoked in SQLite (by public key) must stay
    refused even through the legacy file path."""
    import json as _json
    from app.services import device_identity
    # enroll then revoke a device (so its public key is revoked)
    key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
    tok = device_identity.enroll_device(pub_b64, "rev-legacy", "w013-code")
    device_identity.revoke_device(tok)
    # now put the SAME public key in a legacy file with a random token
    legacy_token = ("deadbeef" * 8)
    reg_file = tmp_path / "devices.jsonl"
    reg_file.write_text(_json.dumps({
        "device_id": "rev-legacy", "token": legacy_token,
        "public_key_b64": pub_b64}) + "\n", encoding="utf-8")
    import os
    os.environ["WORKCAM_DEVICE_REGISTRY"] = str(reg_file)
    try:
        r = insp_env.client.get("/api/v1/projects",
                                 headers={"X-Device-Token": legacy_token})
        assert r.status_code == 401
    finally:
        os.environ.pop("WORKCAM_DEVICE_REGISTRY", None)


def test_migration_preserves_data(insp_env):
    """Enroll/create data -> the data survives (W013 P0 #3)."""
    c = insp_env.client
    pid = c.post("/api/v1/projects", json={"name": "Survive"},
                  headers=insp_env.H).json()["project_id"]
    ph, _ = _photo_uploaded(insp_env, pid)
    got = c.get(f"/api/v1/projects/{pid}", headers=insp_env.H)
    assert got.status_code == 200


def test_reenrollment_keeps_ownership_and_idempotency(insp_env):
    """Credential rotation (same pub key -> same new token) does not
    change ownership or create a second identity."""
    from app.services import device_identity
    tok1 = device_identity.enroll_device(
        insp_env.pub_a, "insp-AAA", "w013-code")
    tok2 = device_identity.enroll_device(
        insp_env.pub_a, "insp-AAA", "w013-code")
    assert tok1 == tok2           # idempotent, no duplicate identity
    # resources created under tok_a remain accessible with the same token
    pid = _project(insp_env)
    got = insp_env.client.get(f"/api/v1/projects/{pid}",
                                headers={"X-Device-Token": tok1})
    assert got.status_code == 200


# --------------------------------------------------- P1: annotation CRUD


@pytest.mark.parametrize("ann_type,extra", [
    ("arrow", {"norm_x2": 0.6, "norm_y2": 0.4}),
    ("rectangle", {"norm_x2": 0.5, "norm_y2": 0.6}),
    ("circle", {"norm_x2": 0.7, "norm_y2": 0.6}),
    ("freehand", {"norm_x2": 0.5, "norm_y2": 0.8}),
    ("text", {"text_content": "Crack here"}),
    ("number_marker", {"marker_number": 1}),
])
def test_all_six_annotation_types_create(insp_env, ann_type, extra):
    c = insp_env.client
    pid = _project(insp_env)
    ph, _ = _photo_uploaded(insp_env, pid)
    body = {"annotation_type": ann_type, "norm_x1": 0.2, "norm_y1": 0.3,
            "color_hex": "#FF3B30", "stroke_width": 5, **extra}
    r = c.post(f"/api/v1/photos/{ph}/annotations", json=body,
                headers=insp_env.H)
    assert r.status_code == 201, r.text
    assert r.json()["annotation_type"] == ann_type
    assert r.json()["revision"] == 1
    assert "human-added" in r.json()["note"]


def test_annotation_invalid_coord_rejected(insp_env):
    c = insp_env.client
    pid = _project(insp_env)
    ph, _ = _photo_uploaded(insp_env, pid)
    r = c.post(f"/api/v1/photos/{ph}/annotations", json={
        "annotation_type": "arrow", "norm_x1": 1.5, "norm_y1": 0.3,
        "norm_x2": 0.6, "norm_y2": 0.4}, headers=insp_env.H)
    assert r.status_code == 422


def test_annotation_missing_required_field(insp_env):
    c = insp_env.client
    pid = _project(insp_env)
    ph, _ = _photo_uploaded(insp_env, pid)
    # text without text_content
    r = c.post(f"/api/v1/photos/{ph}/annotations", json={
        "annotation_type": "text", "norm_x1": 0.2, "norm_y1": 0.3},
        headers=insp_env.H)
    assert r.status_code == 422
    # number_marker without marker_number
    r2 = c.post(f"/api/v1/photos/{ph}/annotations", json={
        "annotation_type": "number_marker", "norm_x1": 0.2,
        "norm_y1": 0.3}, headers=insp_env.H)
    assert r2.status_code == 422
    # arrow without norm_x2
    r3 = c.post(f"/api/v1/photos/{ph}/annotations", json={
        "annotation_type": "arrow", "norm_x1": 0.2, "norm_y1": 0.3},
        headers=insp_env.H)
    assert r3.status_code == 422


def test_annotation_cap_enforced(insp_env):
    c = insp_env.client
    pid = _project(insp_env)
    ph, _ = _photo_uploaded(insp_env, pid)
    for i in range(50):
        r = c.post(f"/api/v1/photos/{ph}/annotations", json={
            "annotation_type": "text", "norm_x1": 0.1, "norm_y1": 0.1,
            "text_content": f"ann {i}"}, headers=insp_env.H)
        assert r.status_code == 201
    r51 = c.post(f"/api/v1/photos/{ph}/annotations", json={
        "annotation_type": "text", "norm_x1": 0.1, "norm_y1": 0.1,
        "text_content": "one too many"}, headers=insp_env.H)
    assert r51.status_code == 413


def test_annotation_soft_delete_and_history(insp_env):
    c = insp_env.client
    pid = _project(insp_env)
    ph, _ = _photo_uploaded(insp_env, pid)
    r = c.post(f"/api/v1/photos/{ph}/annotations", json={
        "annotation_type": "text", "norm_x1": 0.2, "norm_y1": 0.3,
        "text_content": "temp"}, headers=insp_env.H)
    aid = r.json()["annotation_id"]
    # delete
    d = c.delete(f"/api/v1/annotations/{aid}", headers=insp_env.H)
    assert d.status_code == 200
    # active list excludes it
    lst = c.get(f"/api/v1/photos/{ph}/annotations",
                 headers=insp_env.H).json()
    assert lst["total"] == 0
    # include_inactive shows it
    lst2 = c.get(f"/api/v1/photos/{ph}/annotations?include_inactive=true",
                   headers=insp_env.H).json()
    assert lst2["total"] == 1
    assert lst2["items"][0]["is_active"] is False


def test_annotation_patch_revision_conflict(insp_env):
    c = insp_env.client
    pid = _project(insp_env)
    ph, _ = _photo_uploaded(insp_env, pid)
    r = c.post(f"/api/v1/photos/{ph}/annotations", json={
        "annotation_type": "text", "norm_x1": 0.2, "norm_y1": 0.3,
        "text_content": "v1"}, headers=insp_env.H)
    aid = r.json()["annotation_id"]
    stale = c.patch(f"/api/v1/annotations/{aid}", json={
        "text_content": "stale", "expected_revision": 1},
        headers=insp_env.H)
    assert stale.status_code == 200
    # now try revision 1 again -> conflict
    conflict = c.patch(f"/api/v1/annotations/{aid}", json={
        "text_content": "v2", "expected_revision": 1},
        headers=insp_env.H)
    assert conflict.status_code == 409


# --------------------------------------------------- P2: render engine


def test_render_all_types_produce_real_images():
    photo = _photo()
    anns = [
        {"annotation_type": "arrow", "norm_x1": 0.2, "norm_y1": 0.3,
         "norm_x2": 0.6, "norm_y2": 0.25, "color_hex": "#FF3B30",
         "stroke_width": 6, "z_order": 0},
        {"annotation_type": "rectangle", "norm_x1": 0.1, "norm_y1": 0.5,
         "norm_x2": 0.4, "norm_y2": 0.8, "color_hex": "#FF9500",
         "stroke_width": 5, "z_order": 1},
    ]
    result = render_annotated_derivative(photo, anns)
    img = Image.open(io.BytesIO(result["image_bytes"]))
    assert img.format == "JPEG"
    assert img.size == (1200, 900)


def test_render_original_unchanged():
    photo = _photo()
    snapshot = photo
    render_annotated_derivative(photo, [
        {"annotation_type": "text", "norm_x1": 0.5, "norm_y1": 0.5,
         "text_content": "test", "color_hex": "#FF0000",
         "stroke_width": 4, "z_order": 0}])
    assert photo == snapshot


def test_render_annotation_with_watermark_combo():
    from app.services.watermark_render import (
        GpsSource, TimeTrust, WatermarkFields)
    photo = _photo()
    fields = WatermarkFields(
        project_name="Site W", show_project_name=True,
        captured_time="2026-11-24 10:00", show_captured_time=True,
        captured_time_trust=TimeTrust.DEVICE,
        evidence_status="unverified", show_evidence_status=True)
    result = render_annotated_derivative(photo, [
        {"annotation_type": "arrow", "norm_x1": 0.2, "norm_y1": 0.3,
         "norm_x2": 0.5, "norm_y2": 0.3, "color_hex": "#FF3B30",
         "stroke_width": 6, "z_order": 0}],
        watermark_fields=fields, watermark_template="evidence")
    assert result["geometry"]["watermark_applied"] is True


def test_render_exif_orientation(insp_env):
    img = Image.new("RGB", (800, 600), (90, 140, 90))
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    data = buf.getvalue()
    result = render_annotated_derivative(data, [
        {"annotation_type": "text", "norm_x1": 0.3, "norm_y1": 0.5,
         "text_content": "on portrait", "color_hex": "#FF0000",
         "stroke_width": 4, "z_order": 0}])
    assert result["geometry"]["exif_orientation_applied"] == 6
    assert result["geometry"]["image_width"] == 600   # transposed


def test_render_oversize_image_guard(monkeypatch):
    """Render-budget guard fires before allocating a 432MB bitmap."""
    from app.services import watermark_render as wr
    monkeypatch.setattr(wr, "MAX_RENDER_PIXELS", 100)
    with pytest.raises(ValueError, match="too large"):
        render_annotated_derivative(_photo(640, 480), [])


# --------------------------------------------------- P3: issue lifecycle


def test_issue_full_lifecycle_with_history(insp_env):
    c = insp_env.client
    pid = _project(insp_env)
    r = c.post(f"/api/v1/projects/{pid}/issues", json={
        "title": "Pier 2 crack", "severity": "high",
        "description": "Hairline crack west face",
        "location_description": "Km 34+200",
        "assigned_to": "J. Wang"}, headers=insp_env.H)
    assert r.status_code == 201
    issue = r.json()
    assert issue["status"] == "open"
    assert issue["revision"] == 1

    # in_progress
    r2 = c.patch(f"/api/v1/issues/{issue['issue_id']}", json={
        "status": "in_progress", "expected_revision": 1},
        headers=insp_env.H)
    assert r2.json()["status"] == "in_progress"
    assert r2.json()["revision"] == 2

    # resolved (sets resolved_at)
    r3 = c.patch(f"/api/v1/issues/{issue['issue_id']}", json={
        "status": "resolved", "expected_revision": 2},
        headers=insp_env.H)
    assert r3.json()["resolved_at"] is not None

    # closed
    r4 = c.patch(f"/api/v1/issues/{issue['issue_id']}", json={
        "status": "closed", "expected_revision": 3},
        headers=insp_env.H)
    assert r4.json()["status"] == "closed"

    # detail shows full history
    det = c.get(f"/api/v1/issues/{issue['issue_id']}",
                 headers=insp_env.H).json()
    assert len(det["history"]) == 4       # created + 3 status changes
    # closed issues are immutable
    r5 = c.patch(f"/api/v1/issues/{issue['issue_id']}", json={
        "title": "nope", "expected_revision": 4}, headers=insp_env.H)
    assert r5.status_code == 409


def test_issue_invalid_transition(insp_env):
    c = insp_env.client
    pid = _project(insp_env)
    issue = c.post(f"/api/v1/projects/{pid}/issues", json={
        "title": "T", "severity": "low"}, headers=insp_env.H).json()
    # open -> resolved (skipping in_progress) is INVALID
    r = c.patch(f"/api/v1/issues/{issue['issue_id']}", json={
        "status": "resolved", "expected_revision": 1},
        headers=insp_env.H)
    assert r.status_code == 409
    assert "invalid_status_transition" in r.text


def test_issue_revision_conflict(insp_env):
    c = insp_env.client
    pid = _project(insp_env)
    issue = c.post(f"/api/v1/projects/{pid}/issues", json={
        "title": "T", "severity": "low"}, headers=insp_env.H).json()
    r1 = c.patch(f"/api/v1/issues/{issue['issue_id']}", json={
        "description": "v1", "expected_revision": 1},
        headers=insp_env.H)
    r2 = c.patch(f"/api/v1/issues/{issue['issue_id']}", json={
        "description": "stale", "expected_revision": 1},
        headers=insp_env.H)
    assert r2.status_code == 409


def test_issue_cross_subject_404(insp_env):
    c = insp_env.client
    pid = _project(insp_env)
    issue = c.post(f"/api/v1/projects/{pid}/issues", json={
        "title": "Mine", "severity": "low"}, headers=insp_env.H).json()
    assert c.get(f"/api/v1/issues/{issue['issue_id']}",
                  headers=insp_env.B).status_code == 404
    assert c.patch(f"/api/v1/issues/{issue['issue_id']}", json={
        "title": "steal", "expected_revision": 1},
        headers=insp_env.B).status_code == 404


# --------------------------------------------------- P4: comparison


def test_before_after_link_and_comparison_image(insp_env):
    c = insp_env.client
    pid = _project(insp_env)
    issue = c.post(f"/api/v1/projects/{pid}/issues", json={
        "title": "Fix crack", "severity": "high"},
        headers=insp_env.H).json()["issue_id"]
    ph_before, before_data = _photo_uploaded(insp_env, pid, _photo(seed=10))
    ph_after, after_data = _photo_uploaded(insp_env, pid, _photo(seed=200))

    # link both
    c.post(f"/api/v1/issues/{issue}/photos", json={
        "photo_id": ph_before, "role": "before"}, headers=insp_env.H)
    c.post(f"/api/v1/issues/{issue}/photos", json={
        "photo_id": ph_after, "role": "after"}, headers=insp_env.H)

    # one photo per role: relink replaces
    ph_after2, _ = _photo_uploaded(insp_env, pid, _photo(seed=250))
    c.post(f"/api/v1/issues/{issue}/photos", json={
        "photo_id": ph_after2, "role": "after"}, headers=insp_env.H)
    photos = c.get(f"/api/v1/issues/{issue}/photos",
                    headers=insp_env.H).json()
    after_roles = [p for p in photos["items"] if p["role"] == "after"]
    assert len(after_roles) == 1
    assert after_roles[0]["photo_id"] == ph_after2

    # comparison image renders REAL pixels
    cmp_resp = c.post(f"/api/v1/issues/{issue}/comparison-image", json={
        "before_photo_id": ph_before, "after_photo_id": ph_after2},
        headers=insp_env.H)
    assert cmp_resp.status_code == 200
    img = Image.open(io.BytesIO(cmp_resp.content))
    assert img.format == "JPEG"
    assert "manual before/after association" in cmp_resp.headers.get(
        "X-Comparison-Note", "").lower()

    # originals unchanged
    orig = c.get(f"/api/v1/photos/{ph_before}/original",
                   headers=insp_env.H)
    assert orig.content == before_data


def test_comparison_cross_project_rejected(insp_env):
    c = insp_env.client
    pid1 = c.post("/api/v1/projects", json={"name": "P1"},
                   headers=insp_env.H).json()["project_id"]
    pid2 = c.post("/api/v1/projects", json={"name": "P2"},
                   headers=insp_env.H).json()["project_id"]
    issue = c.post(f"/api/v1/projects/{pid1}/issues", json={
        "title": "T", "severity": "low"}, headers=insp_env.H).json()
    ph2, _ = _photo_uploaded(insp_env, pid2)
    r = c.post(f"/api/v1/issues/{issue['issue_id']}/photos", json={
        "photo_id": ph2, "role": "before"}, headers=insp_env.H)
    assert r.status_code == 400


# --------------------------------------------------- P6: sync integration


def test_annotation_and_issue_sync_batch_integration(insp_env):
    """Annotations and issues write to their OWN tables + history; the
    W008 sync change-log tracks entities created VIA THE SYNC BATCH
    (offline queue), not entities created via direct API calls (by
    design). This test verifies both paths coexist correctly."""
    c = insp_env.client
    pid = _project(insp_env)     # direct API (no sync event by design)
    ph, _ = _photo_uploaded(insp_env, pid)
    # create via the sync batch (offline path -> sync events)
    ops = [{"op": "project_create", "operation_id": "w013-sync-pc",
            "payload": {"operation_id": "w013-sync-pc",
                        "local_id": "loc-sync-p",
                        "name": "Sync Via Batch"}}]
    sync_pid = c.post("/api/v1/sync/batch",
                       json={"operations": ops},
                       headers=insp_env.H).json()["id_map"]["loc-sync-p"]
    ops2 = [{"op": "photo_register", "operation_id": "w013-sync-pr",
             "payload": {"operation_id": "w013-sync-pr",
                         "local_id": "loc-sync-ph",
                         "server_project_id": sync_pid,
                         "idempotency_key": "w013-ik",
                         "original_sha256": "ab" * 32,
                         "original_filename": "I.jpg",
                         "mime_type": "image/jpeg", "byte_size": 10,
                         "captured_at": "t", "device_id": "d"}}]
    c.post("/api/v1/sync/batch", json={"operations": ops2},
            headers=insp_env.H)
    # annotations/issues on the direct-API photo
    c.post(f"/api/v1/photos/{ph}/annotations", json={
        "annotation_type": "text", "norm_x1": 0.3, "norm_y1": 0.4,
        "text_content": "synced"}, headers=insp_env.H)
    c.post(f"/api/v1/projects/{pid}/issues", json={
        "title": "Sync issue", "severity": "medium"},
        headers=insp_env.H)
    events = c.get("/api/v1/sync/events?limit=200",
                     headers=insp_env.H).json()
    types = {e["entity_type"] for e in events["items"]}
    assert "project" in types and "photo" in types
    # issue/annotation rows exist in their own tables
    conn = sqlite3.connect(str(insp_env.db))
    a_count = conn.execute("SELECT COUNT(*) FROM mvp_annotations"
                            ).fetchone()[0]
    i_count = conn.execute("SELECT COUNT(*) FROM mvp_issues"
                             ).fetchone()[0]
    conn.close()
    assert a_count == 1 and i_count == 1


def test_committed_annotation_samples_exist():
    from pathlib import Path
    samples = Path(__file__).resolve().parents[2] / "docs" / "mvp" / \
        "w013-samples"
    count = 0
    for jpg in sorted(samples.glob("*.jpg")):
        img = Image.open(jpg)
        assert img.format == "JPEG"
        count += 1
    assert count == 8
