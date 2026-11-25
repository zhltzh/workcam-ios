"""B02-W021 - baseline security regression audit (P0), 2026-11-25.

One pinned regression test per audit checklist item (16 items) against
the 31917e3 production surface. "PASS-pinned" semantics: an attack that
is already defended gets a pinned assertion so it can never silently
regress. Newly FOUND issues carry an explicit ISSUE comment and assert
the CURRENT behavior until fixed; fixes flip the assertion to the
defended behavior.

Local-only: every test runs against an isolated per-test SQLite DB via
env overrides; no network access is performed anywhere in this file.

Checklist map (item -> test):
 01 auth bypass .................... test_01_auth_bypass_surface_locked
 02 legacy token shutdown .......... test_02_legacy_token_shutdown_is_total
 03 device revocation .............. test_03_revocation_is_instant_and_total
 04 signature replay (ledger) ...... test_04_sync_replay_rejected_and_deduplicated
 05 evidence substitution ........... test_05_evidence_substitution_blocked
 06 trusted-status forgery .......... test_06_client_cannot_claim_trusted_status
 07 cross-project privilege ........ test_07_cross_project_and_device_isolation
 08 path traversal ................. test_08_path_traversal_inputs_rejected
 09 zip unpack surface ............. test_09_production_surface_has_no_zip_unpack
 10 malicious image input ........... test_10_malicious_images_full_chain
 11 oversized files ................ test_11_oversized_inputs_rejected
 12 transaction interruption ....... test_12_transaction_rollback_on_injected_failure
 13 upload crash mid-flight ......... test_13_store_crash_before_rename_is_atomic
 14 sync interrupted mid-flight .... test_14_sync_interrupt_replay_is_safe
 15 joint DB+originals backup ....... test_15_joint_db_and_originals_backup_restore
 16 sensitive leakage ............... test_16_sensitive_material_never_leaks
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import uuid
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


@pytest.fixture()
def sec_env(tmp_path, monkeypatch):
    """Isolated business DB + two enrolled devices (A owner, B rival)."""
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_DEVICE_REGISTRY",
                       str(tmp_path / "devices.jsonl"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "w021-code")
    import app.db as dbm
    dbm.migrate(tmp_path / "biz.db")
    from app.services import device_identity
    import app.services.device_identity as _di
    monkeypatch.setattr(_di, "check_rate", lambda *a, **k: None)

    def _mk(name):
        key = ec.generate_private_key(ec.SECP256R1())
        pub = base64.b64encode(key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)).decode()
        return key, pub, device_identity.enroll_device(
            pub, name, "w021-code")

    _, pub_a, tok_a = _mk("w021-A")
    _, pub_b, tok_b = _mk("w021-B")
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "biz.db",
        "pub_a": pub_a, "pub_b": pub_b,
        "A": {"X-Device-Token": tok_a}, "B": {"X-Device-Token": tok_b},
        "tok_a": tok_a, "tok_b": tok_b,
    })()


client = TestClient(app)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _mk_project(env, name="Audit Site", headers=None) -> str:
    r = client.post("/api/v1/projects", json={"name": name},
                    headers=headers or env.A)
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _register_photo(env, project_id, data=b"photo-bytes", key=None,
                    headers=None, mime="image/jpeg") -> str:
    r = client.post(f"/api/v1/projects/{project_id}/photos", json={
        "idempotency_key": key or "k" + uuid.uuid4().hex[:8],
        "original_sha256": _sha(data),
        "original_filename": "a.jpg", "mime_type": mime,
        "byte_size": len(data), "captured_at": "2026-11-25 08:00",
        "device_id": "device-A"}, headers=headers or env.A)
    assert r.status_code == 201, r.text
    return r.json()["photo_id"]


def _upload_original(env, photo_id, data, headers=None):
    return client.put(f"/api/v1/photos/{photo_id}/original",
                      content=data, headers={**(headers or env.A),
                      "Content-Type": "application/octet-stream"})


def _jpeg_bytes(color=(120, 160, 90), size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ---------------------------------------------------------------- item 01


def test_01_auth_bypass_surface_locked(sec_env):
    """01 PASS-pinned: no header / empty / forged token is rejected with
    401 on every /api/v1 business surface (bodies are made VALID so the
    identity gate is what answers); the admin surface rejects a wrong
    X-Admin-Code; a forged token creates nothing."""
    bodies = {
        "POST /api/v1/projects": {"json": {"name": "X"}},
        "POST /api/v1/sync/batch": {"json": {"operations": [
            {"op": "project_create", "operation_id": "x",
             "payload": {"operation_id": "x", "local_id": "L",
                         "name": "n"}}]}},
        "POST /api/v1/devices/challenge": {},
    }
    surfaces = [
        ("GET", "/api/v1/projects", {}),
        ("POST", "/api/v1/projects", bodies["POST /api/v1/projects"]),
        ("GET", "/api/v1/photos/00000000-0000-4000-8000-000000000000", {}),
        ("POST", "/api/v1/sync/batch",
         bodies["POST /api/v1/sync/batch"]),
        ("GET", "/api/v1/sync/events", {}),
        ("POST", "/api/v1/devices/challenge", {}),
    ]
    for method, url, kwargs in surfaces:
        r = getattr(client, method.lower())(url, **kwargs)
        assert r.status_code == 401, (method, url, r.status_code)
        r2 = getattr(client, method.lower())(
            url, headers={"X-Device-Token": ""}, **kwargs)
        assert r2.status_code == 401, (method, url)
        r3 = getattr(client, method.lower())(
            url, headers={"X-Device-Token": "f" * 64}, **kwargs)
        assert r3.status_code == 401, (method, url)

    # admin surface: wrong admin code
    r = client.post(
        f"/api/v1/devices/{sec_env.tok_a}/revoke",
        json={"admin_code": "wrong"},
        headers={"X-Admin-Code": "not-the-code"})
    assert r.status_code == 401
    # forged token does NOT create anything
    before = client.get("/api/v1/projects",
                        headers=sec_env.A).json()["total"]
    client.post("/api/v1/projects", json={"name": "X"},
                headers={"X-Device-Token": "f" * 64})
    after = client.get("/api/v1/projects", headers=sec_env.A).json()["total"]
    assert before == after


# ---------------------------------------------------------------- item 02


def test_02_legacy_token_shutdown_is_total(sec_env, tmp_path, monkeypatch):
    """02 PASS-pinned (W013-R1 legacy token shutdown): a token derived
    as sha256(public_key) from the legacy devices.jsonl is refused as a
    bearer credential on EVERY protected surface - including the
    experimental upload auth layer - and re-enrollment with the same
    key yields a NEW random token that works everywhere."""
    from tests.conftest import _encode_pub

    key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
    derived = hashlib.sha256(pub_b64.encode("ascii")).hexdigest()
    reg = tmp_path / "devices.jsonl"
    reg.write_text(json.dumps({
        "device_id": "legacy-dev", "token": derived,
        "public_key_b64": pub_b64}) + "\n", encoding="utf-8")
    monkeypatch.setenv("WORKCAM_DEVICE_REGISTRY", str(reg))

    # business API refuses the derived credential with the typed reason
    r = client.get("/api/v1/projects",
                   headers={"X-Device-Token": derived})
    assert r.status_code == 401
    assert "legacy_token_must_re_enroll" in r.text

    # the experimental upload auth layer routes through the SAME gate
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    monkeypatch.setenv("WORKCAM_UPLOAD_AUTH", "1")
    r2 = client.post("/upload/experimental/auth/challenge",
                    headers={"X-Device-Token": derived})
    assert r2.status_code == 401

    # re-enrollment (same public key) issues a RANDOM opaque token
    from app.services import device_identity
    new_tok = device_identity.enroll_device(
        pub_b64, "legacy-dev", "w021-code")
    assert new_tok != derived
    assert len(new_tok) == 64
    r3 = client.get("/api/v1/projects",
                    headers={"X-Device-Token": new_tok})
    assert r3.status_code == 200


# ---------------------------------------------------------------- item 03


def test_03_revocation_is_instant_and_total(sec_env):
    """03 PASS-pinned: after revocation the token is refused on every
    business surface, outstanding challenges are killed, and the SAME
    public key cannot silently re-enroll (409 device_revoked)."""
    from app.services import device_identity
    pid = _mk_project(sec_env)
    photo = _register_photo(sec_env, pid)

    # issue a challenge first - it must die with the device
    ch = client.post("/api/v1/devices/challenge",
                    headers={"X-Device-Token": sec_env.tok_a})
    assert ch.status_code == 200
    nonce = ch.json()["nonce"]

    device_identity.revoke_device(sec_env.tok_a)

    wm_body = {"template": "minimal", "fields": {
        "project_name": "P", "photo_id": photo,
        "captured_time": "t", "evidence_status": "no_evidence"}}
    batch_body = {"operations": [
        {"op": "project_create", "operation_id": "x",
         "payload": {"operation_id": "x", "local_id": "L", "name": "n"}}]}
    surfaces = [
        ("get", "/api/v1/projects", {}),
        ("get", f"/api/v1/projects/{pid}", {}),
        ("get", f"/api/v1/photos/{photo}", {}),
        ("get", f"/api/v1/photos/{photo}/original", {}),
        ("get", f"/api/v1/photos/{photo}/original/integrity", {}),
        ("get", f"/api/v1/photos/{photo}/annotations", {}),
        ("get", f"/api/v1/projects/{pid}/issues", {}),
        ("get", "/api/v1/sync/events", {}),
        ("post", "/api/v1/sync/batch", {"json": batch_body}),
        ("post", f"/api/v1/photos/{photo}/derivatives/watermark",
         {"json": wm_body}),
    ]
    for method, url, kwargs in surfaces:
        r = getattr(client, method)(url, headers=sec_env.A, **kwargs)
        assert r.status_code == 401, (method, url, r.status_code)

    # the outstanding challenge is dead (issued BEFORE revocation)
    sig = client.post("/api/v1/devices/challenge/verify", json={
        "device_token": sec_env.tok_a, "nonce": nonce,
        "signature_b64": base64.b64encode(b"x").decode()})
    assert sig.status_code == 401

    # re-enrolling the SAME public key is refused (no silent comeback)
    r = client.post("/api/v1/devices/enroll", json={
        "public_key_b64": sec_env.pub_a,
        "device_id": "w021-A", "enrollment_code": "w021-code"})
    assert r.status_code == 409
    assert "device_revoked" in r.text

    # rival B is unaffected
    assert client.get("/api/v1/projects",
                      headers=sec_env.B).status_code == 200


# ---------------------------------------------------------------- item 04


def test_04_sync_replay_rejected_and_deduplicated(sec_env):
    """04 PASS-pinned: the idempotency ledger treats a same-hash replay
    as duplicate (stored receipt, no re-execution) and a changed-payload
    replay under the same operation_id as op_id_conflict; photo
    idempotency keys behave identically on the direct API surface."""
    ops = [{"op": "project_create", "operation_id": "p1",
            "payload": {"operation_id": "p1", "local_id": "L1",
                        "name": "Site"}}]
    r1 = client.post("/api/v1/sync/batch",
                     json={"operations": ops}, headers=sec_env.A)
    assert [x["status"] for x in r1.json()["receipts"]] == ["applied"]

    # identical replay -> duplicate, same server id, no new row
    r2 = client.post("/api/v1/sync/batch",
                     json={"operations": ops}, headers=sec_env.A)
    assert [x["status"] for x in r2.json()["receipts"]] == ["duplicate"]
    assert r2.json()["id_map"]["L1"] == r1.json()["id_map"]["L1"]
    assert client.get(
        "/api/v1/projects", headers=sec_env.A).json()["total"] == 1

    # changed payload under the same operation_id -> typed conflict
    ops_bad = [{"op": "project_create", "operation_id": "p1",
                "payload": {"operation_id": "p1", "local_id": "L1",
                            "name": "Renamed"}}]
    r3 = client.post("/api/v1/sync/batch",
                     json={"operations": ops_bad}, headers=sec_env.A)
    assert r3.json()["receipts"][0]["status"] == "rejected"
    assert r3.json()["receipts"][0]["error"]["code"] == "op_id_conflict"

    # direct API: same idempotency key + different content -> 409
    pid = r1.json()["id_map"]["L1"]
    client.post(f"/api/v1/projects/{pid}/photos", json={
        "idempotency_key": "dup-key", "original_sha256": _sha(b"x"),
        "original_filename": "a.jpg", "mime_type": "image/jpeg",
        "byte_size": 1, "captured_at": "t", "device_id": "d"},
        headers=sec_env.A)
    r4 = client.post(f"/api/v1/projects/{pid}/photos", json={
        "idempotency_key": "dup-key", "original_sha256": _sha(b"y"),
        "original_filename": "a.jpg", "mime_type": "image/jpeg",
        "byte_size": 1, "captured_at": "t", "device_id": "d"},
        headers=sec_env.A)
    assert r4.status_code == 409
    assert "idempotency_conflict" in r4.text


# ---------------------------------------------------------------- item 05


def test_05_evidence_substitution_blocked(sec_env):
    """05 PASS-pinned: evidence substitution is blocked at three points:
    (a) uploading bytes whose hash differs from the registered
    original_sha256 -> 400, nothing stored; (b) the stored original is
    immutable - no failed or hostile upload ever changes it; (c) binding
    a manifest to the wrong photo/reference -> typed 400s, never a
    status write."""
    pid = _mk_project(sec_env)
    real = b"real-photo-bytes"
    photo = _register_photo(sec_env, pid, data=real)

    # (a) tampered body vs registered hash
    r = _upload_original(sec_env, photo, b"tampered-photo-bytes")
    assert r.status_code == 400
    assert "hash_mismatch" in r.text

    # bind the real bytes
    assert _upload_original(sec_env, photo, real).status_code == 200

    # stored original never changed by failed attempts
    dl = client.get(f"/api/v1/photos/{photo}/original", headers=sec_env.A)
    assert dl.status_code == 200
    assert hashlib.sha256(dl.content).hexdigest() == _sha(real)

    # (c) manifest binding guards -> typed 400s
    from tests.conftest import make_manifest, _encode_pub
    key = ec.generate_private_key(ec.SECP256R1())
    manifest = make_manifest(key, _encode_pub(key.public_key()),
                             photo_bytes=real)
    md = manifest.model_dump()
    ref = md["record_id"]
    link = client.post(f"/api/v1/photos/{photo}/evidence-links", json={
        "evidence_reference": ref,
        "evidence_type": "signature_manifest"}, headers=sec_env.A)
    assert link.status_code == 201
    link_id = link.json()["evidence_link_id"]

    # wrong record_id -> reference mismatch
    r5 = client.post(
        f"/api/v1/photos/{photo}/evidence-links/{link_id}/verify",
        json={"manifest": {**md, "record_id": str(uuid.uuid4())}},
        headers=sec_env.A)
    assert r5.status_code == 400
    assert "reference_mismatch" in r5.text
    # right record_id but photo_hash bound to a different photo
    r6 = client.post(
        f"/api/v1/photos/{photo}/evidence-links/{link_id}/verify",
        json={"manifest": {**md, "photo_hash": _sha(b"other-photo")}},
        headers=sec_env.A)
    assert r6.status_code == 400
    assert "hash_binding_mismatch" in r6.text
    # no partial status write happened on either attempt
    links = client.get(f"/api/v1/photos/{photo}/evidence-links",
                      headers=sec_env.A).json()
    assert links["items"][0]["verification_status"] == "unverified"


# ---------------------------------------------------------------- item 06


def test_06_client_cannot_claim_trusted_status(sec_env):
    """06 PASS-pinned: a client-supplied verification_status is IGNORED
    on evidence-link creation (schema has no such field), sync has no
    evidence op at all, and the watermark engine overrides the client
    status with the server-resolved one (proven by the idempotency
    probe: two requests with DIFFERENT claimed statuses hash equal
    after the override and return the same derivative)."""
    pid = _mk_project(sec_env)
    data = _jpeg_bytes()
    photo = _register_photo(sec_env, pid, data=data)

    # (a) create_evidence_link ignores a smuggled status field
    r = client.post(f"/api/v1/photos/{photo}/evidence-links", json={
        "evidence_reference": "ref-1",
        "evidence_type": "signature_manifest",
        "verification_status": "cryptographically_valid",
        "status": "server_anchored"}, headers=sec_env.A)
    assert r.status_code == 201
    assert r.json()["verification_status"] == "unverified"

    # (b) sync exposes no evidence op: every evidence-ish op name is
    # rejected at the SCHEMA level (SyncOp.op is a Literal whitelist -
    # unknown ops never even reach an executor) or by the executor
    # whitelist if one slips through
    for op_name in ("evidence_verify", "evidence_link_create",
                    "evidence_status_update", "set_verification_status"):
        rb = client.post("/api/v1/sync/batch", json={"operations": [{
            "op": op_name, "operation_id": "e1",
            "payload": {"operation_id": "e1",
                        "verification_status": "cryptographically_valid"}}]},
            headers=sec_env.A)
        if rb.status_code == 200:
            assert rb.json()["receipts"][0]["status"] == "rejected", (
                op_name)
            assert rb.json()["receipts"][0]["error"]["code"] == "unknown_op", (
                op_name)
        else:
            assert rb.status_code == 422, (op_name, rb.status_code)

    # (c) watermark claims the highest status -> server override
    assert _upload_original(sec_env, photo, data).status_code == 200
    wm_fields = {"project_name": "P", "photo_id": photo,
                 "show_photo_id": True, "captured_time": "t"}
    r_high = client.post(
        f"/api/v1/photos/{photo}/derivatives/watermark",
        json={"template": "minimal", "fields": {
            **wm_fields, "evidence_status": "cryptographically_valid"}},
        headers=sec_env.A)
    assert r_high.status_code == 201, r_high.text
    r_low = client.post(
        f"/api/v1/photos/{photo}/derivatives/watermark",
        json={"template": "minimal", "fields": {
            **wm_fields, "evidence_status": "no_evidence"}},
        headers=sec_env.A)
    assert r_low.status_code == 201
    # the override made both render-parameter hashes identical -> the
    # second request is an idempotent replay of the FIRST (server-side
    # state: photo has no verified evidence link at all)
    assert r_low.json()["idempotent_replay"] is True
    assert (r_low.json()["derivative_id"]
            == r_high.json()["derivative_id"])


# ---------------------------------------------------------------- item 07


def test_07_cross_project_and_device_isolation(sec_env):
    """07 PASS-pinned: rival B gets 404-identical responses (no
    existence oracle) on every one of A's resources and cannot mutate
    them; the sync ledger and the event stream are owner-scoped."""
    pid_a = _mk_project(sec_env, name="A secret site")
    photo_a = _register_photo(sec_env, pid_a)
    _upload_original(sec_env, photo_a, b"photo-bytes")
    issue_a = client.post(f"/api/v1/projects/{pid_a}/issues", json={
        "title": "Crack", "severity": "high"}, headers=sec_env.A).json()

    ghost = "00000000-0000-4000-8000-000000000000"
    probes = [
        ("get", f"/api/v1/projects/{pid_a}", {}),
        ("patch", f"/api/v1/projects/{pid_a}",
         {"json": {"description": "hijack"}}),
        ("get", f"/api/v1/photos/{photo_a}", {}),
        ("get", f"/api/v1/photos/{photo_a}/original", {}),
        ("put", f"/api/v1/photos/{photo_a}/original",
         {"content": b"evil", "headers": None}),
        ("get", f"/api/v1/photos/{photo_a}/original/integrity", {}),
        ("post", f"/api/v1/photos/{photo_a}/annotations",
         {"json": {"annotation_type": "text", "norm_x1": 0.1,
                   "norm_y1": 0.1, "text_content": "x"}}),
        ("get", f"/api/v1/photos/{photo_a}/annotations", {}),
        ("get", f"/api/v1/projects/{pid_a}/issues", {}),
        ("patch", f"/api/v1/issues/{issue_a['issue_id']}",
         {"json": {"expected_revision": 1, "title": "hijack"}}),
        ("post", f"/api/v1/projects/{pid_a}/reports",
         {"json": {"report_type": "site_photo",
                   "photo_ids": [photo_a], "per_page": 1}}),
        ("post", f"/api/v1/projects/{pid_a}/photos",
         {"json": {"idempotency_key": "k",
                   "original_sha256": _sha(b"z"),
                   "original_filename": "a.jpg",
                   "mime_type": "image/jpeg", "byte_size": 1,
                   "captured_at": "t", "device_id": "d"}}),
        ("post", f"/api/v1/projects/{pid_a}/archive", {}),
    ]
    for method, url, kwargs in probes:
        kw = dict(kwargs)
        kw["headers"] = {**sec_env.B, **(kw.get("headers") or {})}
        r_b = getattr(client, method)(url, **kw)
        assert r_b.status_code == 404, (method, url, r_b.status_code)
        # cross-subject and nonexistent answer identically
        kw2 = dict(kwargs)
        kw2["headers"] = {**sec_env.A, **(kw2.get("headers") or {})}
        url_ghost = url.replace(pid_a, ghost).replace(
            photo_a, ghost).replace(issue_a["issue_id"], ghost)
        r_g = getattr(client, method)(url_ghost, **kw2)
        assert r_g.status_code == r_b.status_code, (method, url)

    # sync: B cannot touch A's project even with the server id
    r = client.post("/api/v1/sync/batch", json={"operations": [{
        "op": "photo_register", "operation_id": "evil-1",
        "payload": {"operation_id": "evil-1", "local_id": "L",
                    "server_project_id": pid_a,
                    "idempotency_key": "kk", "original_sha256": _sha(b"z"),
                    "original_filename": "a.jpg", "mime_type": "image/jpeg",
                    "byte_size": 1, "captured_at": "t", "device_id": "d"}}]},
        headers=sec_env.B)
    assert r.json()["receipts"][0]["status"] == "rejected"

    # events pull is owner-scoped: B never sees A's event stream
    ev_b = client.get("/api/v1/sync/events", headers=sec_env.B).json()
    assert ev_b["items"] == []


# ---------------------------------------------------------------- item 08


def test_08_path_traversal_inputs_rejected(sec_env, tmp_path, monkeypatch):
    """08 PASS-pinned: traversal payloads are rejected by validation
    BEFORE any filesystem touch: photo/project ids must be UUIDv4 (bad
    ids die as 400), sha256 object addressing must be 64 lowercase hex,
    and the experimental upload surface only accepts lowercase-hex
    batch ids. Raw '../..' path segments get normalized by the HTTP
    layer and die as 404 (no route) - never reaching any handler."""
    pid = _mk_project(sec_env)
    _register_photo(sec_env, pid)

    # ids that survive URL parsing die at the handler's UUIDv4 gate
    # (400); percent-encoded separators get split at the routing layer
    # and die as unrouted 404 - both paths mean the payload NEVER
    # reaches a filesystem address
    for bad in ["a%2F..%2F..", "..\\..\\windows", "x" * 41,
                "not-a-uuid", "CON", "NUL", "..%00"]:
        r = client.get(f"/api/v1/photos/{bad}", headers=sec_env.A)
        assert r.status_code in (400, 404), (bad, r.status_code)
        r2 = client.get(f"/api/v1/projects/{bad}", headers=sec_env.A)
        assert r2.status_code in (400, 404), bad
    # raw dotdot segments normalize away before routing -> unrouted
    # 404, never a handler hit
    for raw in ["..", "../.."]:
        r3 = client.get(f"/api/v1/photos/{raw}", headers=sec_env.A)
        assert r3.status_code == 404, (raw, r3.status_code)
        r3b = client.get(f"/api/v1/projects/{raw}", headers=sec_env.A)
        assert r3b.status_code == 404, raw

    # sha-validated object addressing: the store API refuses malformed
    from app.services import original_store
    from app.db import ApiError
    for evil_sha in ("../../etc/passwd", "ZZ" * 32, "A" * 64,
                     "abc", "..%2F..%2F"):
        with pytest.raises(ApiError):
            original_store.object_path(evil_sha)

    # store root contains ONLY two-hex-char subdirectories
    _upload_original(sec_env, client.get(
        f"/api/v1/projects/{pid}/photos",
        headers=sec_env.A).json()["items"][0]["photo_id"], b"photo-bytes")
    root = original_store.store_root()
    assert root.exists()
    for sub in root.iterdir():
        assert len(sub.name) == 2, sub.name
        assert all(c in "0123456789abcdef" for c in sub.name), sub.name

    # experimental upload: batch id must be lowercase hex; a traversal
    # id creates nothing on disk
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    monkeypatch.setenv("WORKCAM_UPLOAD_DIR", str(tmp_path / "uploads"))
    for bad in ("../../evil", "AABB0011", "abc.txt", "..", "con"):
        r = client.put(f"/upload/experimental/batches/{bad}/chunks/0")
        assert r.status_code in (400, 404), (bad, r.status_code)
    uploads = tmp_path / "uploads"
    assert not uploads.exists() or not any(
        ".." in str(p.relative_to(uploads))
        for p in uploads.rglob("*") if p.is_dir())


# ---------------------------------------------------------------- item 09


def test_09_production_surface_has_no_zip_unpack(sec_env):
    """09 PASS-pinned: the production /api/v1 surface only WRITES zip
    packages (delivery-package); no app/ code calls zipfile extract or
    extractall, so there is no zip-slip unpack surface to attack. The
    only unpacking implementation (W018 CLI, tools/) is reviewed
    separately. Pinned here: (a) static scan of app/, (b) the zip the
    delivery endpoint produces has server-constructed, traversal-free
    member names."""
    app_dir = Path(__file__).parent.parent / "app"
    offenders = []
    for py in app_dir.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        for needle in ("extractall", ".extract(", "unpack_archive"):
            if needle in src:
                offenders.append(f"{py}: {needle}")
    assert offenders == [], offenders

    # runtime: build a report and pull its delivery package
    pid = _mk_project(sec_env)
    photo = _register_photo(sec_env, pid)
    _upload_original(sec_env, photo, b"photo-bytes")
    rep = client.post(f"/api/v1/projects/{pid}/reports", json={
        "report_type": "site_photo", "photo_ids": [photo],
        "per_page": 1}, headers=sec_env.A)
    assert rep.status_code == 201, rep.text
    rid = rep.json()["report_id"]
    pkg = client.post(f"/api/v1/reports/{rid}/delivery-package",
                      json={"include_originals": True,
                            "include_derivatives": False,
                            "include_evidence": True}, headers=sec_env.A)
    assert pkg.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    for name in zf.namelist():
        assert not name.startswith(("/", "\\")), name
        assert ".." not in Path(name).parts, name
        assert ":" not in name, name
        assert "\\" not in name, name
    assert "manifest.json" in zf.namelist()
    assert "verification-guide.txt" in zf.namelist()


# ---------------------------------------------------------------- item 10


def test_10_malicious_images_full_chain(sec_env):
    """10 PASS-pinned (after fixes): fake JPEG/PNG headers are stored
    verbatim (content-addressed storage does not parse image content -
    by design). REPORT generation is fail-safe (image skipped, 201);
    the PIL-rendering endpoints answer hostile bytes with a typed 400
    (ISSUE-W021-01 comparison-image / ISSUE-W021-02 watermark,
    both fixed 2026-11-25) and originals stay intact."""
    pid = _mk_project(sec_env)
    fake_jpg = b"\xff\xd8\xff\xe0GARBAGE-NOT-A-REAL-JPEG" + os.urandom(512)
    fake_png = b"\x89PNG\r\n\x1a\n" + os.urandom(512)
    ph1 = _register_photo(sec_env, pid, data=fake_jpg)
    assert _upload_original(sec_env, ph1, fake_jpg).status_code == 200
    ph2 = _register_photo(sec_env, pid, data=fake_png, mime="image/png")
    assert _upload_original(sec_env, ph2, fake_png).status_code == 200

    # downloads return the bytes verbatim
    dl = client.get(f"/api/v1/photos/{ph1}/original", headers=sec_env.A)
    assert dl.status_code == 200
    assert dl.content == fake_jpg

    # report generation stays fail-safe with hostile bytes
    rep = client.post(f"/api/v1/projects/{pid}/reports", json={
        "report_type": "site_photo", "photo_ids": [ph1, ph2],
        "per_page": 1}, headers=sec_env.A)
    assert rep.status_code == 201, rep.text

    # FIXED (ISSUE-W021-01 / -02, 2026-11-25): the PIL render endpoints
    # now answer hostile bytes with a typed 400 instead of an unhandled
    # 500; originals stay intact either way.
    quiet = TestClient(app, raise_server_exceptions=False)
    issue = client.post(f"/api/v1/projects/{pid}/issues", json={
        "title": "T", "severity": "low"}, headers=sec_env.A).json()
    r_cmp = quiet.post(
        f"/api/v1/issues/{issue['issue_id']}/comparison-image",
        json={"before_photo_id": ph1, "after_photo_id": ph2},
        headers=sec_env.A)
    assert r_cmp.status_code == 400            # was 500 (ISSUE-W021-01)
    assert "invalid_image" in r_cmp.text
    r_wm = quiet.post(f"/api/v1/photos/{ph1}/derivatives/watermark",
                      json={"template": "minimal", "fields": {
                          "project_name": "P", "photo_id": ph1,
                          "show_photo_id": True,
                          "captured_time": "t",
                          "evidence_status": "no_evidence"}},
                      headers=sec_env.A)
    assert r_wm.status_code == 400            # was 500 (ISSUE-W021-02)
    assert "invalid_image_or_request" in r_wm.text

    # no data damage: originals remain intact and identical
    assert client.get(
        f"/api/v1/photos/{ph1}/original",
        headers=sec_env.A).content == fake_jpg
    integ = client.get(f"/api/v1/photos/{ph1}/original/integrity",
                       headers=sec_env.A).json()
    assert integ["original"]["file_hash_valid"] is True


# ---------------------------------------------------------------- item 11


def test_11_oversized_inputs_rejected(sec_env, tmp_path, monkeypatch):
    """11 PASS-pinned (after fix): size ceilings are enforced by schema
    BEFORE any storage work: photo byte_size > 100 MiB -> 422 (direct
    API); a schema-violating field inside a queued sync op is a typed
    REJECTED receipt (ISSUE-W021-03, fixed 2026-11-25) with ledger
    replay discipline; a sync batch over 50 ops -> 422; the experimental
    upload surface rejects chunk_size > 4 MiB and total > 2 GiB at
    schema level."""
    pid = _mk_project(sec_env)
    r = client.post(f"/api/v1/projects/{pid}/photos", json={
        "idempotency_key": "big", "original_sha256": _sha(b"x"),
        "original_filename": "a.jpg", "mime_type": "image/jpeg",
        "byte_size": 100 * 1024 * 1024 + 1,
        "captured_at": "t", "device_id": "d"}, headers=sec_env.A)
    assert r.status_code == 422

    # FIXED (ISSUE-W021-03, 2026-11-25): a schema-violating payload
    # inside a queued op is now a typed REJECTED receipt (ledger
    # discipline preserved) instead of an unhandled 500.
    quiet = TestClient(app, raise_server_exceptions=False)
    r2 = quiet.post("/api/v1/sync/batch", json={"operations": [{
        "op": "photo_register", "operation_id": "big1",
        "payload": {"operation_id": "big1", "local_id": "L",
                    "server_project_id": pid,
                    "idempotency_key": "k",
                    "original_sha256": _sha(b"x"),
                    "original_filename": "a.jpg",
                    "mime_type": "image/jpeg",
                    "byte_size": 200 * 1024 * 1024,
                    "captured_at": "t", "device_id": "d"}}]},
        headers=sec_env.A)
    assert r2.status_code == 200               # was 500 (ISSUE-W021-03)
    rec = r2.json()["receipts"][0]
    assert rec["status"] == "rejected"
    assert rec["error"]["code"] == "invalid_payload"
    assert "byte_size" in rec["error"]["message"]
    # ledger discipline: replaying the same op replays the rejection
    r2b = quiet.post("/api/v1/sync/batch", json={"operations": [{
        "op": "photo_register", "operation_id": "big1",
        "payload": {"operation_id": "big1", "local_id": "L",
                    "server_project_id": pid,
                    "idempotency_key": "k",
                    "original_sha256": _sha(b"x"),
                    "original_filename": "a.jpg",
                    "mime_type": "image/jpeg",
                    "byte_size": 200 * 1024 * 1024,
                    "captured_at": "t", "device_id": "d"}}]},
        headers=sec_env.A)
    assert r2b.json()["receipts"][0]["status"] == "duplicate"

    # sync batch op-count cap (MAX_BATCH_OPS = 50)
    r3 = client.post("/api/v1/sync/batch", json={"operations": [
        {"op": "project_create", "operation_id": f"c{i}",
         "payload": {"operation_id": f"c{i}", "local_id": f"L{i}",
                     "name": f"n{i}"}} for i in range(51)]},
        headers=sec_env.A)
    assert r3.status_code == 422

    # experimental upload surface caps
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    monkeypatch.setenv("WORKCAM_UPLOAD_DIR", str(tmp_path / "up"))
    r4 = client.post("/upload/experimental/batches", json={
        "total_bytes": 1024,
        "total_sha256": _sha(b"x"),
        "chunk_size": 4 * 1024 * 1024 + 1})
    assert r4.status_code == 422
    r5 = client.post("/upload/experimental/batches", json={
        "total_bytes": 2 * 1024 * 1024 * 1024 + 1,
        "total_sha256": _sha(b"x"), "chunk_size": 1024})
    assert r5.status_code == 422
    # plan exceeding the 512-chunk cap is refused before any storage
    r6 = client.post("/upload/experimental/batches", json={
        "total_bytes": 512 * 1024 * 1024 * 2, "total_sha256": _sha(b"x"),
        "chunk_size": 1024 * 1024})
    assert r6.status_code in (400, 422)


# ---------------------------------------------------------------- item 12


def test_12_transaction_rollback_on_injected_failure(sec_env):
    """12 PASS-pinned: an exception injected mid-transaction rolls the
    whole unit of work back (no half-committed rows); a failing sync op
    only poisons its own receipt - previously applied ops in the same
    batch stay committed, and replaying the batch is consistent."""
    from app.db import db

    with pytest.raises(RuntimeError):
        with db() as conn:
            conn.execute(
                "INSERT INTO mvp_projects (project_id, owner_token, "
                "name, description, site_address, status, created_at, "
                "updated_at, revision) VALUES (?,?,?,?,?,'active',?,?,1)",
                ("p-tx", sec_env.tok_a, "Half", "", "", "t0", "t0"))
            raise RuntimeError("injected mid-transaction")
    with db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM mvp_projects WHERE project_id=?",
            ("p-tx",)).fetchone()[0]
    assert n == 0

    # sync: op1 ok, op2 references a missing project -> rejected; op1
    # remains committed and replaying the batch changes nothing
    batch = [
        {"op": "project_create", "operation_id": "tx-1",
         "payload": {"operation_id": "tx-1", "local_id": "L1",
                     "name": "Survivor"}},
        {"op": "photo_register", "operation_id": "tx-2",
         "payload": {"operation_id": "tx-2", "local_id": "L2",
                     "server_project_id": "00000000-0000-4000-8000-"
                                          "000000000000",
                     "idempotency_key": "k",
                     "original_sha256": _sha(b"x"),
                     "original_filename": "a.jpg",
                     "mime_type": "image/jpeg", "byte_size": 1,
                     "captured_at": "t", "device_id": "d"}},
    ]
    r = client.post("/api/v1/sync/batch", json={"operations": batch},
                    headers=sec_env.A)
    assert [x["status"] for x in r.json()["receipts"]] == [
        "applied", "rejected"]
    survived = client.get("/api/v1/projects", headers=sec_env.A).json()
    assert any(p["name"] == "Survivor" for p in survived["items"])
    # replay: the applied op is a stored duplicate, the rejected op
    # replays its stored rejection - both WITHOUT touching the DB again
    r2 = client.post("/api/v1/sync/batch", json={"operations": batch},
                     headers=sec_env.A)
    assert [x["status"] for x in r2.json()["receipts"]] == [
        "duplicate", "duplicate"]
    assert survived["total"] == client.get(
        "/api/v1/projects", headers=sec_env.A).json()["total"]


# ---------------------------------------------------------------- item 13


def test_13_store_crash_before_rename_is_atomic(sec_env, monkeypatch):
    """13 PASS-pinned: a crash (injected) right before the atomic rename
    persists NOTHING - no object, no DB ledger row - and leaves only a
    fsck-reportable temp residue; after the 'restart' (next request)
    the same upload succeeds and the store holds exactly one intact
    object. In-process simulation of an upload interrupted by a service
    restart."""
    pid = _mk_project(sec_env)
    data = b"photo-bytes-crash-test"
    photo = _register_photo(sec_env, pid, data=data)

    quiet = TestClient(app, raise_server_exceptions=False)

    real_replace = os.replace

    def boom(*a, **k):
        raise RuntimeError("simulated crash before rename")
    monkeypatch.setattr("os.replace", boom)
    r = quiet.put(f"/api/v1/photos/{photo}/original", content=data,
                  headers={**sec_env.A,
                          "Content-Type": "application/octet-stream"})
    assert r.status_code == 500               # the crash surfaces as 500
    monkeypatch.setattr("os.replace", real_replace)   # surgical restore

    from app.services import original_store
    sha = _sha(data)
    assert not original_store.object_path(sha).exists()
    from app.db import db
    with db() as conn:
        row = conn.execute("SELECT * FROM mvp_originals WHERE sha256=?",
                           (sha,)).fetchone()
    assert row is None                        # no ledger row either

    # SOFT crash (exception unwinds): the writer's except-path already
    # unlinked the temp - no residue at all
    report = original_store.fsck()
    assert report["temp_residue"] == []

    # HARD crash simulation (process killed before cleanup): a
    # leftover .part is fsck-visible and safely repairable without
    # ever touching committed objects
    root = original_store.store_root()
    root.mkdir(parents=True, exist_ok=True)
    stray = root / ".w007_hardcrash.part"
    stray.write_bytes(b"partial upload from a killed process")
    report2 = original_store.fsck()
    assert any("hardcrash" in p for p in report2["temp_residue"])
    original_store.fsck(fix_temps=True)
    assert not stray.exists()
    assert original_store.fsck()["repaired_temps"] == 0

    # "restart" recovery: the same upload now succeeds, exactly once
    r2 = _upload_original(sec_env, photo, data)
    assert r2.status_code == 200
    assert r2.json()["idempotent_replay"] is False
    assert original_store.object_path(sha).exists()
    assert hashlib.sha256(
        original_store.read_original(sha)).hexdigest() == sha


# ---------------------------------------------------------------- item 14


def test_14_sync_interrupt_replay_is_safe(sec_env):
    """14 PASS-pinned (network drop mid-sync): a client that loses the
    response mid-batch can replay the ENTIRE batch safely - stored
    receipts come back as duplicates, no operation re-executes, and the
    event stream / row counts show no duplication."""
    pre_pid = _mk_project(sec_env, name="NetDrop Host")
    ops = [
        {"op": "project_create", "operation_id": "n-1",
         "payload": {"operation_id": "n-1", "local_id": "L1",
                     "name": "NetDrop Site"}},
        {"op": "photo_register", "operation_id": "n-2",
         "payload": {"operation_id": "n-2", "local_id": "L2",
                     "server_project_id": pre_pid,
                     "idempotency_key": "netdrop-k",
                     "original_sha256": _sha(b"netdrop-photo"),
                     "original_filename": "a.jpg",
                     "mime_type": "image/jpeg", "byte_size": 16,
                     "captured_at": "t", "device_id": "d"}},
    ]
    # first attempt: server applies both ops; the response is 'lost'
    # (we discard it - simulating the network break AFTER server commit)
    r1 = client.post("/api/v1/sync/batch", json={"operations": ops},
                     headers=sec_env.A)
    assert r1.status_code == 200
    assert [x["status"] for x in r1.json()["receipts"]] == [
        "applied", "applied"]

    # client retries the WHOLE batch after reconnecting
    r2 = client.post("/api/v1/sync/batch", json={"operations": ops},
                     headers=sec_env.A)
    assert [x["status"] for x in r2.json()["receipts"]] == [
        "duplicate", "duplicate"]
    assert (r2.json()["receipts"][0]["result"]
            == r1.json()["receipts"][0]["result"])

    # exactly one new project, one photo, no duplicated event stream
    photos = client.get(
        f"/api/v1/projects/{pre_pid}/photos", headers=sec_env.A).json()
    assert photos["total"] == 1
    projects = client.get("/api/v1/projects", headers=sec_env.A).json()
    assert projects["total"] == 2            # pre_pid + NetDrop Site
    assert [p["name"] for p in projects["items"]].count(
        "NetDrop Site") == 1
    events = client.get(
        "/api/v1/sync/events?limit=100", headers=sec_env.A).json()
    photo_created = [e for e in events["items"]
                    if e["action"] == "created"
                    and e["entity_type"] == "photo"]
    assert len(photo_created) == 1


# ---------------------------------------------------------------- item 15


def test_15_joint_db_and_originals_backup_restore(sec_env, tmp_path,
                                                  monkeypatch):
    """15 PASS-pinned: joint backup/restore of the business DB
    (VACUUM INTO) and the content-addressed originals store survives a
    full environment rebuild: after restoring into a FRESH path, the
    enrolled identities, projects, photos and byte-identical originals
    are all back and hash-verified."""
    from app.db import backup_to, verify_backup, restore_from
    from app.services import original_store

    pid = _mk_project(sec_env, name="Backup Site")
    data = b"original-bytes-for-backup"
    photo = _register_photo(sec_env, pid, data=data)
    assert _upload_original(sec_env, photo, data).status_code == 200

    # ---- backup both parts
    db_bak = tmp_path / "backup" / "biz.db"
    pages = backup_to(db_bak)
    assert pages > 0
    info = verify_backup(db_bak)
    assert info["counts"]["projects"] == 1
    assert info["counts"]["photos"] == 1
    orig_dir = tmp_path / "backup" / "originals"
    manifest = original_store.backup(orig_dir)
    assert _sha(data) in manifest["objects"]
    assert manifest["failed"] == []

    # ---- simulate a fresh environment (new DB path + new store root)
    new_db = tmp_path / "fresh" / "workcam.db"
    restore_from(db_bak, new_db)
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(new_db))
    restored = original_store.restore(orig_dir, manifest)
    assert restored["objects"] == 1
    assert restored["refused"] == []

    # identities, project, photo all back; original byte-identical
    r = client.get(f"/api/v1/projects/{pid}", headers=sec_env.A)
    assert r.status_code == 200
    ph = client.get(f"/api/v1/photos/{photo}", headers=sec_env.A)
    assert ph.status_code == 200
    dl = client.get(f"/api/v1/photos/{photo}/original", headers=sec_env.A)
    assert dl.status_code == 200
    assert dl.content == data
    integ = client.get(
        f"/api/v1/photos/{photo}/original/integrity",
        headers=sec_env.A).json()
    assert integ["original"]["file_hash_valid"] is True

    # restore never overwrites: restoring again skips intact objects
    again = original_store.restore(orig_dir, manifest)
    assert again["objects"] == 0 and again["skipped_intact"] == 1


# ---------------------------------------------------------------- item 16


def test_16_sensitive_material_never_leaks(sec_env):
    """16 PASS-pinned: device tokens never enter the audit log (only
    fingerprints), the device status endpoint never returns key
    material, the delivery package never contains tokens or the
    enrollment code, and the logging filter redacts sensitive keys."""
    from app.db import db

    pid = _mk_project(sec_env)
    photo = _register_photo(sec_env, pid, data=_jpeg_bytes())
    _upload_original(sec_env, photo, _jpeg_bytes())

    # audit log: tokens are fingerprints only
    with db() as conn:
        rows = conn.execute(
            "SELECT device_token, detail FROM mvp_audit_log").fetchall()
    assert rows, "expected audit rows"
    for row in rows:
        assert row["device_token"] != sec_env.tok_a
        blob = json.dumps(dict(row))
        assert sec_env.tok_a not in blob
        assert sec_env.tok_b not in blob

    # device status: no public key material in the response
    st = client.get(f"/api/v1/devices/{sec_env.tok_b}",
                    headers={"X-Admin-Code": "w021-code"})
    assert st.status_code == 200
    assert "public_key" not in json.dumps(st.json())
    assert st.json()["key_fingerprint"] != sec_env.pub_b

    # delivery package: no tokens, no enrollment code, no key material
    proj2 = client.post("/api/v1/projects", json={"name": "P2"},
                        headers=sec_env.B).json()
    photo2 = _register_photo(sec_env, proj2["project_id"],
                             data=_jpeg_bytes((80, 80, 80)),
                             headers=sec_env.B)
    _upload_original(sec_env, photo2, _jpeg_bytes((80, 80, 80)),
                     headers=sec_env.B)
    rep = client.post(
        f"/api/v1/projects/{proj2['project_id']}/reports", json={
            "report_type": "site_photo", "photo_ids": [photo2],
            "per_page": 1}, headers=sec_env.B)
    assert rep.status_code == 201, rep.text
    pkg = client.post(
        f"/api/v1/reports/{rep.json()['report_id']}/delivery-package",
        json={}, headers=sec_env.B)
    assert pkg.status_code == 200
    secrets = [sec_env.tok_a.encode(), sec_env.tok_b.encode(),
               b"w021-code", sec_env.pub_a.encode(), sec_env.pub_b.encode()]
    for s in secrets:
        assert s not in pkg.content, (
            f"LEAK: {s[:12]!r} found in delivery package")

    # logging filter: messages naming sensitive keys are redacted.
    # (pytest pre-attaches a root handler so app.main's basicConfig is
    # a no-op and the root level stays WARNING - set the logger level
    # explicitly or the INFO records are dropped before the filter.)
    import logging
    logger = logging.getLogger("workcam")
    logger.setLevel(logging.INFO)
    records: list = []

    class _Cap(logging.Handler):
        def emit(self, rec):
            records.append(rec)

    cap = _Cap()
    logger.addHandler(cap)
    try:
        logger.info("operation ok with token %s", sec_env.tok_a)
        logger.info("plain message without secrets")
    finally:
        logger.removeHandler(cap)
    redacted = [r.getMessage() for r in records]
    assert any("REDACTED" in m for m in redacted)
    assert all(sec_env.tok_a not in m for m in redacted)
