"""WC-W006 follow-up - evidence-link server-side verification closure.

Closes the W006 risk #2: statuses were never written back after a real
server verification. This suite drives the new
POST /api/v1/photos/{pid}/evidence-links/{lid}/verify endpoint through
every state of the EXISTING status machine:

  unverified -> signature_valid         (math ok, key unregistered)
  unverified -> cryptographically_valid (math ok + key registered)
  unverified -> verification_failed     (bad signature / bad bytes /
                                         bad manifest, reason preserved)
  unverified -> server_anchored         (real anchor receipt verified)
  failed    -> valid                    (re-verify is idempotent RE-RUN)

Anti-forgery and anti-misbinding guards (400, no state change):
  - manifest.record_id != evidence_reference
  - manifest.photo_hash != photo.original_sha256
  - receipt.batch_id != evidence_reference
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient

from app.main import app
from app.services.key_registry import get_registry, reset_registry
from tests.conftest import make_manifest, make_test_photo, _encode_pub


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_DEVICE_REGISTRY",
                       str(tmp_path / "devices.jsonl"))
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    import app.db as dbm
    import app.api.upload_auth as ua
    dbm.migrate(tmp_path / "biz.db")
    reset_registry()

    key = ec.generate_private_key(ec.SECP256R1())       # TEST key
    pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
    registry = ua.DeviceRegistry(tmp_path / "devices.jsonl")
    token = registry.enroll(pub_b64, "device-AAA")
    ua._batch_owners.clear(); ua._buckets.clear(); ua._challenges.clear()
    return type("E", (), {
        "tmp": tmp_path, "key": key, "pub_b64": pub_b64,
        "H": {"X-Device-Token": token}, "client": TestClient(app),
    })()


def _mk_project(env, name="Site A") -> str:
    r = env.client.post("/api/v1/projects", json={"name": name},
                        headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _register_photo(env, project_id, sha: str) -> str:
    r = env.client.post(f"/api/v1/projects/{project_id}/photos", json={
        "idempotency_key": f"k-{uuid.uuid4().hex[:8]}",
        "original_sha256": sha,
        "original_filename": "IMG.jpg", "mime_type": "image/jpeg",
        "byte_size": 1024, "captured_at": "2026-11-23T08:00:00Z",
        "device_id": "iPhone15,3"}, headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()["photo_id"]


def _link(env, photo_id: str, ref: str, type_: str) -> str:
    r = env.client.post(f"/api/v1/photos/{photo_id}/evidence-links",
                       json={"evidence_reference": ref,
                             "evidence_type": type_}, headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()["evidence_link_id"]


def _verify(env, photo_id: str, link_id: str, body: dict):
    return env.client.post(
        f"/api/v1/photos/{photo_id}/evidence-links/{link_id}/verify",
        json=body, headers=env.H)


def _get_links(env, photo_id: str) -> list[dict]:
    r = env.client.get(f"/api/v1/photos/{photo_id}/evidence-links",
                       headers=env.H)
    return r.json()["items"]


def _full_photo_and_manifest(env, seed: int = 0x42):
    """A real (desktop-synthetic) photo + a manifest SIGNED over it,
    aligned with the photo's registered sha256. The seed keeps two
    calls from producing IDENTICAL bytes (make_test_photo is constant
    content - found when the cross-photo guard test got 200)."""
    photo = bytes([seed]) * 2048
    photo_sha = hashlib.sha256(photo).hexdigest()
    manifest = make_manifest(env.key, _encode_pub(env.key.public_key()),
                             photo_bytes=photo)
    assert manifest.photo_hash == photo_sha
    return photo, manifest


# ------------------------------------------------------- signature_manifest


def test_verify_to_cryptographically_valid(env):
    """Registered key + valid signature + matching hash -> the link
    transitions to cryptographically_valid with a verified_at stamp."""
    pid = _mk_project(env)
    photo, manifest = _full_photo_and_manifest(env)
    photo_id = _register_photo(env, pid, manifest.photo_hash)
    link_id = _link(env, photo_id, manifest.record_id,
                    "signature_manifest")

    # enroll the SAME public key in the evidence registry (the /verify
    # trust model) -> registered path
    get_registry().register(env.pub_b64, "device-AAA")

    r = _verify(env, photo_id, link_id, {"manifest": manifest.model_dump()})
    assert r.status_code == 200, r.text
    link = r.json()
    assert link["verification_status"] == "cryptographically_valid"
    assert link["verified_at"] is not None
    assert link["failure_reason"] is None


def test_verify_to_signature_valid_when_key_unregistered(env):
    """Same math, UNREGISTERED key -> signature_valid (the EXISTING
    status machine's state; reason explains device identity is not
    established). Status is NEVER inflated to cryptographically_valid."""
    pid = _mk_project(env)
    photo, manifest = _full_photo_and_manifest(env)
    photo_id = _register_photo(env, pid, manifest.photo_hash)
    link_id = _link(env, photo_id, manifest.record_id,
                    "signature_manifest")

    r = _verify(env, photo_id, link_id, {"manifest": manifest.model_dump()})
    assert r.status_code == 200
    link = r.json()
    assert link["verification_status"] == "signature_valid"
    assert "not in the registry" in link["failure_reason"]


def test_verify_failure_reason_preserved_and_retry_recovers(env):
    """A bad signature writes verification_failed + reason; re-running
    verify with the CORRECT manifest recovers to valid (verify is a
    re-runnable server action, not a one-way trap)."""
    pid = _mk_project(env)
    photo, manifest = _full_photo_and_manifest(env)
    photo_id = _register_photo(env, pid, manifest.photo_hash)
    link_id = _link(env, photo_id, manifest.record_id,
                    "signature_manifest")

    broken = manifest.model_copy(
        update={"record_id": manifest.record_id})     # keep reference...
    broken = broken.model_copy(update={
        "device_id": "tampered-after-signing"})        # ...but break the sig
    r = _verify(env, photo_id, link_id, {"manifest": broken.model_dump()})
    assert r.status_code == 200
    assert r.json()["verification_status"] == "verification_failed"
    assert "signature invalid" in r.json()["failure_reason"]

    # retry with the genuine manifest -> recovers
    r2 = _verify(env, photo_id, link_id,
                 {"manifest": manifest.model_dump()})
    assert r2.status_code == 200
    assert r2.json()["verification_status"] in (
        "signature_valid", "cryptographically_valid")


def test_verify_with_photo_bytes_mismatch(env):
    """Providing the photo bytes lets the server check content too:
    mismatched bytes -> verification_failed even though the signature
    math over the (false) hash is fine."""
    pid = _mk_project(env)
    photo, manifest = _full_photo_and_manifest(env)
    photo_id = _register_photo(env, pid, manifest.photo_hash)
    link_id = _link(env, photo_id, manifest.record_id,
                    "signature_manifest")

    wrong_bytes = b"these-are-not-the-photo"
    r = _verify(env, photo_id, link_id,
                {"manifest": manifest.model_dump(),
                 "photo_base64": base64.b64encode(wrong_bytes).decode()})
    assert r.status_code == 200
    assert r.json()["verification_status"] == "verification_failed"
    assert "photo bytes do not match" in r.json()["failure_reason"]

    # and the CORRECT bytes now verify
    r2 = _verify(env, photo_id, link_id,
                 {"manifest": manifest.model_dump(),
                  "photo_base64": base64.b64encode(photo).decode()})
    assert r2.json()["verification_status"] in (
        "signature_valid", "cryptographically_valid")


def test_reference_mismatch_rejected_before_state_change(env):
    """The manifest's record_id must equal the link's registered
    reference - a 400 WITHOUT touching the link state."""
    pid = _mk_project(env)
    photo, manifest = _full_photo_and_manifest(env)
    photo_id = _register_photo(env, pid, manifest.photo_hash)
    link_id = _link(env, photo_id, "some-other-record",
                    "signature_manifest")
    r = _verify(env, photo_id, link_id, {"manifest": manifest.model_dump()})
    assert r.status_code == 400
    assert "reference_mismatch" in r.text
    links = _get_links(env, photo_id)
    assert links[0]["verification_status"] == "unverified"  # untouched


def test_hash_binding_mismatch_rejected(env):
    """Anti cross-photo misbinding: the manifest's photo_hash must equal
    the REGISTERED photo's hash - verifying photo B's manifest against
    photo A's link is a 400, never a state write. (record_id is made to
    match so the guard that fires is specifically the hash binding.)"""
    pid = _mk_project(env)
    photo_a, manifest_a = _full_photo_and_manifest(env, seed=0x42)
    photo_b, manifest_b = _full_photo_and_manifest(env, seed=0x43)
    photo_id_a = _register_photo(env, pid, manifest_a.photo_hash)
    link_a = _link(env, photo_id_a, manifest_a.record_id,
                   "signature_manifest")
    # photo B's manifest content, but carrying photo A's record_id so
    # the reference guard passes and the HASH guard is what fires
    wrong = manifest_b.model_dump()
    wrong["record_id"] = manifest_a.record_id
    r = _verify(env, photo_id_a, link_a, {"manifest": wrong})
    assert r.status_code == 400
    assert "hash_binding_mismatch" in r.text
    assert _get_links(env, photo_id_a)[0][
        "verification_status"] == "unverified"


def test_bad_manifest_schema_writes_failed_with_reason(env):
    pid = _mk_project(env)
    _, manifest = _full_photo_and_manifest(env)
    photo_id = _register_photo(env, pid, manifest.photo_hash)
    link_id = _link(env, photo_id, manifest.record_id,
                    "signature_manifest")
    broken = manifest.model_dump()
    broken["capture_timestamp"] = "not-a-timestamp"
    r = _verify(env, photo_id, link_id, {"manifest": broken})
    assert r.status_code == 200
    assert r.json()["verification_status"] == "verification_failed"
    assert "structure" in r.json()["failure_reason"]


# ------------------------------------------------------------- time_anchor


def test_verify_time_anchor_to_server_anchored(env):
    """A REAL anchor receipt from the anchor service verifies back as
    server_anchored."""
    from app.models.time_anchor import TimeAnchorRequest
    from app.services.time_anchor import (
        get_time_anchor_service, reset_time_anchor_service)
    reset_time_anchor_service()
    pid = _mk_project(env)
    _, manifest = _full_photo_and_manifest(env)
    photo_id = _register_photo(env, pid, manifest.photo_hash)

    service = get_time_anchor_service()
    request = TimeAnchorRequest(
        batch_id=f"biz-{uuid.uuid4().hex[:8]}",
        chain_head_hash="a" * 64, chain_tail_hash="b" * 64,
        record_count=1,
        device_reported_start_time="2026-11-23T08:00:00+00:00",
        device_reported_end_time="2026-11-23T08:01:00+00:00")
    service.create_anchor(request)
    receipt = service._store.get_by_batch_id(request.batch_id).model_dump()

    link_id = _link(env, photo_id, request.batch_id, "time_anchor")
    r = _verify(env, photo_id, link_id, {"receipt": receipt})
    assert r.status_code == 200, r.text
    assert r.json()["verification_status"] == "server_anchored"
    assert r.json()["verified_at"] is not None


def test_verify_time_anchor_forged_receipt_fails(env):
    """A receipt whose signed bytes were tampered with -> signature path
    rejects -> verification_failed with the reason preserved."""
    from app.models.time_anchor import TimeAnchorRequest
    from app.services.time_anchor import (
        get_time_anchor_service, reset_time_anchor_service)
    reset_time_anchor_service()
    pid = _mk_project(env)
    _, manifest = _full_photo_and_manifest(env)
    photo_id = _register_photo(env, pid, manifest.photo_hash)

    service = get_time_anchor_service()
    request = TimeAnchorRequest(
        batch_id=f"biz-{uuid.uuid4().hex[:8]}",
        chain_head_hash="a" * 64, chain_tail_hash="b" * 64,
        record_count=1,
        device_reported_start_time="2026-11-23T08:00:00+00:00",
        device_reported_end_time="2026-11-23T08:01:00+00:00")
    service.create_anchor(request)
    receipt = service._store.get_by_batch_id(
        request.batch_id).model_dump()
    receipt["record_count"] = 999          # tamper a SIGNED field

    link_id = _link(env, photo_id, request.batch_id, "time_anchor")
    r = _verify(env, photo_id, link_id, {"receipt": receipt})
    assert r.status_code == 200
    assert r.json()["verification_status"] == "verification_failed"
    assert "anchor verification failed" in r.json()["failure_reason"]


def test_time_anchor_wrong_batch_rejected(env):
    pid = _mk_project(env)
    _, manifest = _full_photo_and_manifest(env)
    photo_id = _register_photo(env, pid, manifest.photo_hash)
    link_id = _link(env, photo_id, "batch-that-was-registered",
                    "time_anchor")
    r = _verify(env, photo_id, link_id,
                {"receipt": {"batch_id": "a-different-batch",
                             "anchor_id": "x", "key_id": "k",
                             "server_received_at": "t", "challenge": "c",
                             "server_signature": "s",
                             "chain_head_hash": "a" * 64,
                             "chain_tail_hash": "b" * 64,
                             "record_count": 1,
                             "device_reported_start_time": "t",
                             "device_reported_end_time": "t",
                             "verification_status": "digest_anchored",
                             "stored_at": "t"}})
    assert r.status_code == 400
    assert "reference_mismatch" in r.text


# --------------------------------------------------------------- security


def test_verify_requires_auth_and_ownership(env, tmp_path, monkeypatch):
    """Unauthenticated verify -> 401; a second device cannot verify
    another's link -> 404 (no oracle)."""
    pid = _mk_project(env)
    _, manifest = _full_photo_and_manifest(env)
    photo_id = _register_photo(env, pid, manifest.photo_hash)
    link_id = _link(env, photo_id, manifest.record_id,
                    "signature_manifest")

    r = env.client.post(
        f"/api/v1/photos/{photo_id}/evidence-links/{link_id}/verify",
        json={"manifest": manifest.model_dump()})
    assert r.status_code == 401

    # second enrolled device
    import app.api.upload_auth as ua
    key_b = ec.generate_private_key(ec.SECP256R1())
    pub_b = base64.b64encode(_encode_pub(key_b.public_key())).decode()
    reg_b = ua.DeviceRegistry(tmp_path / "devices.jsonl")
    token_b = reg_b.enroll(pub_b, "device-BBB")
    r2 = env.client.post(
        f"/api/v1/photos/{photo_id}/evidence-links/{link_id}/verify",
        json={"manifest": manifest.model_dump()},
        headers={"X-Device-Token": token_b})
    assert r2.status_code == 404        # cross-subject: same as not-found


def test_client_still_cannot_submit_a_status(env):
    """Even through the verify endpoint the request body carries
    MATERIAL only: an injected verification_status field never reaches
    the database - the server-computed value is authoritative."""
    pid = _mk_project(env)
    photo, manifest = _full_photo_and_manifest(env)
    photo_id = _register_photo(env, pid, manifest.photo_hash)
    link_id = _link(env, photo_id, manifest.record_id,
                    "signature_manifest")

    body = {"manifest": manifest.model_dump(),
            "verification_status": "server_anchored"}    # forged attempt
    r = _verify(env, photo_id, link_id, body)
    assert r.status_code == 200
    # server computed signature_valid (key unregistered) - NOT the
    # forged server_anchored
    assert r.json()["verification_status"] == "signature_valid"
