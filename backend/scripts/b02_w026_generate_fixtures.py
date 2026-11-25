"""B02-W026: generate the fixed mock-response set by REAL API calls.

Every request/response recorded in the output JSON was produced by an
actual in-process HTTP call against app.main:app (FastAPI TestClient)
on a throwaway business DB - nothing in this file is hand-written JSON.

The output serves two purposes:
  1. Mac-side offline fixture: decode/replay data with real shapes,
     real IDs, real headers (docs/api/b02-w026-mock-data.json).
  2. Contract-test oracle: backend/tests/test_b02_w026_contract.py
     replays the same request sequences on a fresh DB and asserts the
     response STRUCTURE (key sets, enums, literal notes) matches the
     recorded fixtures - i.e. the docs cannot drift from the code.

Usage (from backend/):
    python scripts/b02_w026_generate_fixtures.py \
        ../docs/api/b02-w026-mock-data.json
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding, PublicFormat)
from PIL import Image, ImageDraw  # noqa: E402

from app.models.capture import SignatureManifest  # noqa: E402
from app.services.serialization import canonical_payload  # noqa: E402

BASELINE = "31917e3"

_SCENARIOS: list[dict] = []
_client = None  # injected by run_all(); never a module-level TestClient


def _record(scenario: str, step: str, method: str, path: str,
            request_body=None, request_headers=None, note: str = "") -> dict:
    """Fire a REAL request and record the actual request + response."""
    headers = dict(request_headers or {})
    kwargs: dict = {"headers": headers}
    if request_body is not None:
        if isinstance(request_body, bytes):
            kwargs["content"] = request_body
        else:
            kwargs["json"] = request_body
    r = _client.request(method, path, **kwargs)
    ctype = r.headers.get("content-type", "")
    if r.status_code == 204 or not r.content:
        body: object = None
    elif "json" in ctype:
        body = r.json()
    else:
        body = {
            "__binary__": True,
            "media_type": ctype,
            "bytes": len(r.content),
            "sha256": hashlib.sha256(r.content).hexdigest(),
        }
    interesting_headers = {k: v for k, v in r.headers.items()
                           if k.lower().startswith("x-")
                           or k.lower() == "content-disposition"}
    rec = {"step": step, "request": {"method": method, "path": path,
                                     "body": request_body
                                     if not isinstance(request_body, bytes)
                                     else f"<{len(request_body)} raw bytes>",
                                     "headers": {k: v for k, v in
                                                 (request_headers or {}).items()
                                                 if k != "X-Device-Token"}},
          "response": {"status": r.status_code,
                       "headers": interesting_headers,
                       "body": body}}
    if note:
        rec["note"] = note
    sc = _SCENARIOS[-1]
    sc["steps"].append(rec)
    return r


def _scenario(scenario_id: str, name: str, note: str = "") -> None:
    _SCENARIOS.append({"id": scenario_id, "name": name,
                       "steps": [], **({"note": note} if note else {})})


def _jpeg(label: str, shade: int) -> bytes:
    img = Image.new("RGB", (320, 240), (shade, shade, min(255, shade + 20)))
    d = ImageDraw.Draw(img)
    d.text((10, 10), label, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def run_all(client) -> dict:
    """Replay the full F1-F6 + ERRORS sequence against `client` (a live
    TestClient on an ISOLATED business DB) and return the fixture dict.
    Importable from tests: the caller owns the environment."""
    global _client
    _client = client
    ENROLL_CODE = os.environ["WORKCAM_ENROLLMENT_CODE"]
    _SCENARIOS.clear()

    # ---- shared identity material (real P-256 keypair) ---------------
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(priv.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()

    # ============================ F1 ====================================
    _scenario("F1", "Device enrollment & authentication",
              "Out-of-band enrollment code; random server-issued token; "
              "challenge-response proof of possession.")
    r = _record("F1", "enroll", "POST", "/api/v1/devices/enroll",
                {"public_key_b64": pub_b64, "device_id": "mac-dev-001",
                 "enrollment_code": ENROLL_CODE})
    token = r.json()["device_token"]
    H = {"X-Device-Token": token}

    r = _record("F1", "challenge", "POST", "/api/v1/devices/challenge",
                None, H)
    nonce = r.json()["nonce"]
    sig = base64.b64encode(priv.sign(nonce.encode("ascii"),
                                     ec.ECDSA(hashes.SHA256()))).decode()
    _record("F1", "challenge-verify", "POST", "/api/v1/devices/challenge/verify",
            {"device_token": token, "nonce": nonce, "signature_b64": sig}, H)
    _record("F1", "status-admin", "GET",
            f"/api/v1/devices/{token}",
            None, {"X-Admin-Code": ENROLL_CODE})
    _record("F1", "status-without-admin", "GET",
            f"/api/v1/devices/{token}", None, H)

    # ============================ F2 ====================================
    _scenario("F2", "Projects, photo metadata & original bytes",
              "Metadata-first registration then raw-byte upload with hash "
              "binding; integrity view separates storage from evidence.")
    r = _record("F2", "create-project", "POST", "/api/v1/projects",
                {"name": "Tower B - Level 3 Retrofit",
                 "description": "Structural retrofit, north facade",
                 "site_address": "Nanjing Jiangbei"}, H)
    project_id = r.json()["project_id"]
    _record("F2", "list-projects", "GET",
            "/api/v1/projects?limit=10&offset=0&status=active", None, H)
    _record("F2", "get-project", "GET", f"/api/v1/projects/{project_id}",
            None, H)

    photo1_bytes = _jpeg("BEFORE-N3-01", 90)
    sha1 = hashlib.sha256(photo1_bytes).hexdigest()
    r = _record("F2", "register-photo-1", "POST",
                f"/api/v1/projects/{project_id}/photos",
                {"idempotency_key": "fixture-photo-1",
                 "original_sha256": sha1,
                 "original_filename": "BEFORE_N3_01.jpg",
                 "mime_type": "image/jpeg",
                 "byte_size": len(photo1_bytes),
                 "captured_at": "2026-11-25T09:15:00+00:00",
                 "device_id": "mac-dev-001"}, H)
    photo1_id = r.json()["photo_id"]
    _record("F2", "photo-replay-same-content", "POST",
            f"/api/v1/projects/{project_id}/photos",
            {"idempotency_key": "fixture-photo-1",
             "original_sha256": sha1,
             "original_filename": "BEFORE_N3_01.jpg",
             "mime_type": "image/jpeg",
             "byte_size": len(photo1_bytes),
             "captured_at": "2026-11-25T09:15:00+00:00",
             "device_id": "mac-dev-001"}, H,
            note="idempotent replay: 201 + _idempotent_replay=true")
    _record("F2", "photo-conflict-same-key-different-content", "POST",
            f"/api/v1/projects/{project_id}/photos",
            {"idempotency_key": "fixture-photo-1",
             "original_sha256": "bb" * 32,
             "original_filename": "OTHER.jpg",
             "mime_type": "image/jpeg",
             "byte_size": 7,
             "captured_at": "2026-11-25T09:15:00+00:00",
             "device_id": "mac-dev-001"}, H,
            note="409 idempotency_conflict")

    photo2_bytes = _jpeg("AFTER-N3-01", 150)
    sha2 = hashlib.sha256(photo2_bytes).hexdigest()
    r = _record("F2", "register-photo-2", "POST",
                f"/api/v1/projects/{project_id}/photos",
                {"idempotency_key": "fixture-photo-2",
                 "original_sha256": sha2,
                 "original_filename": "AFTER_N3_01.jpg",
                 "mime_type": "image/jpeg",
                 "byte_size": len(photo2_bytes),
                 "captured_at": "2026-11-25T15:40:00+00:00",
                 "device_id": "mac-dev-001"}, H)
    photo2_id = r.json()["photo_id"]
    _record("F2", "list-photos", "GET",
            f"/api/v1/projects/{project_id}/photos?limit=5&offset=0",
            None, H)
    _record("F2", "get-photo", "GET", f"/api/v1/photos/{photo1_id}",
            None, H)

    _record("F2", "upload-original-1", "PUT",
            f"/api/v1/photos/{photo1_id}/original",
            photo1_bytes, H)
    _record("F2", "upload-original-1-replay", "PUT",
            f"/api/v1/photos/{photo1_id}/original",
            photo1_bytes, H, note="idempotent replay")
    _record("F2", "upload-original-2", "PUT",
            f"/api/v1/photos/{photo2_id}/original", photo2_bytes, H)
    _record("F2", "upload-original-hash-mismatch", "PUT",
            f"/api/v1/photos/{photo1_id}/original", b"tampered-bytes",
            H, note="400 hash_mismatch - body does not hash to the "
                    "registered original_sha256")
    _record("F2", "download-original", "GET",
            f"/api/v1/photos/{photo1_id}/original", None, H)
    _record("F2", "integrity-view", "GET",
            f"/api/v1/photos/{photo1_id}/original/integrity", None, H)

    # ============================ F3 ====================================
    _scenario("F3", "Evidence links & server-side verification",
              "Trusted statuses are only ever written by the server's own "
              "verification runs (W002 signature core, W003 anchors).")
    from urllib.parse import quote
    _record("F3", "register-key-test", "POST",
            "/register-key?public_key_b64=" + quote(pub_b64, safe="")
            + "&device_id=mac-dev-001&allow_test_registration=true", None,
            None, note="POST /register-key takes QUERY parameters (not a "
                       "JSON body) - implementation fact of the legacy "
                       "verify surface.")
    now = datetime.now(timezone.utc).isoformat()
    manifest = SignatureManifest(
        version="1.0", record_id=str(uuid4()), sequence_number=0,
        previous_record_hash=None, photo_hash=sha1,
        capture_timestamp=now, device_reported_time=now,
        device_id="mac-dev-001",
        signature="", public_key=pub_b64)
    sig = base64.b64encode(priv.sign(canonical_payload(manifest),
                                      ec.ECDSA(hashes.SHA256()))).decode()
    manifest_json = json.loads(manifest.model_dump_json())
    manifest_json["signature"] = sig
    photo1_b64 = base64.b64encode(photo1_bytes).decode()
    _record("F3", "verify-manifest", "POST", "/verify",
            {"manifest": manifest_json, "photo_base64": photo1_b64}, None,
            note="key registered -> cryptographically_valid")
    _record("F3", "store-record", "POST", "/store-record",
            {"manifest": manifest_json, "photo_base64": photo1_b64}, None)
    record_id = manifest_json["record_id"]

    r = _record("F3", "create-evidence-link", "POST",
                f"/api/v1/photos/{photo1_id}/evidence-links",
                {"evidence_reference": record_id,
                 "evidence_type": "signature_manifest"}, H)
    link_id = r.json()["evidence_link_id"]
    _record("F3", "list-evidence-links", "GET",
            f"/api/v1/photos/{photo1_id}/evidence-links", None, H)
    _record("F3", "verify-evidence-link", "POST",
            f"/api/v1/photos/{photo1_id}/evidence-links/{link_id}/verify",
            {"manifest": manifest_json, "photo_base64": photo1_b64}, H,
            note="server-side verification write-back")

    # time anchor path
    r = _record("F3", "anchor-batch", "POST", "/time-anchor/anchor",
                {"batch_id": "fixture-batch-1",
                 "chain_head_hash": "cc" * 32, "chain_tail_hash": "dd" * 32,
                 "record_count": 12,
                 "device_reported_start_time": "2026-11-25T08:00:00+00:00",
                 "device_reported_end_time": "2026-11-25T16:00:00+00:00"},
                None, note="EXPERIMENTAL time-anchor surface")
    receipt = r.json()
    # The anchor RESPONSE is not a complete RECEIPT: the client must add
    # back its own unsigned device-reported times (+ stored_at) before
    # asking for verification. Submitting the raw anchor response yields
    # 422 (missing required receipt fields) - kept in the ERRORS scenario.
    full_receipt = {**receipt,
                    "device_reported_start_time":
                        "2026-11-25T08:00:00+00:00",
                    "device_reported_end_time":
                        "2026-11-25T16:00:00+00:00",
                    "stored_at": receipt["server_received_at"]}
    _record("F3", "anchor-verify", "POST",
            "/time-anchor/verify?check_storage=true", full_receipt, None,
            note="the client assembles the full receipt (adds unsigned "
                 "device-reported times + stored_at) before verifying")
    r3 = _record("F3", "create-anchor-evidence-link", "POST",
                 f"/api/v1/photos/{photo2_id}/evidence-links",
                 {"evidence_reference": receipt["batch_id"],
                  "evidence_type": "time_anchor"}, H)
    anchor_link_id = r3.json()["evidence_link_id"]
    _record("F3", "verify-anchor-evidence-link", "POST",
            f"/api/v1/photos/{photo2_id}/evidence-links/"
            f"{anchor_link_id}/verify",
            {"receipt": full_receipt}, H,
            note="valid anchor receipt -> server_anchored")

    # ============================ F4 ====================================
    _scenario("F4", "Watermark derivatives",
              "Derivatives render from the REAL original bytes; the "
              "evidence status printed is resolved server-side.")
    r = _record("F4", "watermark-render", "POST",
                f"/api/v1/photos/{photo1_id}/derivatives/watermark",
                {"template": "construction",
                 "fields": {"show_project_name": True,
                            "project_name": "Tower B",
                            "show_captured_time": True,
                            "captured_time": "2026-11-25T09:15:00+00:00",
                            "captured_time_trust": "device",
                            "show_evidence_status": True,
                            "show_photo_id": True,
                            "photo_id": photo1_id}}, H)
    derivative_id = r.json()["derivative_id"]
    _record("F4", "watermark-replay", "POST",
            f"/api/v1/photos/{photo1_id}/derivatives/watermark",
            {"template": "construction",
             "fields": {"show_project_name": True,
                        "project_name": "Tower B",
                        "show_captured_time": True,
                        "captured_time": "2026-11-25T09:15:00+00:00",
                        "captured_time_trust": "device",
                        "show_evidence_status": True,
                        "show_photo_id": True,
                        "photo_id": photo1_id}}, H,
            note="idempotent_replay=true")
    _record("F4", "list-derivatives", "GET",
            f"/api/v1/photos/{photo1_id}/derivatives", None, H)
    _record("F4", "download-derivative", "GET",
            f"/api/v1/derivatives/{derivative_id}/download", None, H)

    # ============================ F5 ====================================
    _scenario("F5", "Annotations, inspection issues & comparison",
              "Human-added annotations and human-recorded issues; "
              "optimistic revisions; soft deletes keep history.")
    r = _record("F5", "create-annotation", "POST",
                f"/api/v1/photos/{photo1_id}/annotations",
                {"annotation_type": "rectangle", "norm_x1": 0.2,
                 "norm_y1": 0.2, "norm_x2": 0.7, "norm_y2": 0.6,
                 "color_hex": "#FF3B30", "stroke_width": 4,
                 "z_order": 0}, H)
    ann_id = r.json()["annotation_id"]
    ann_rev = r.json()["revision"]
    _record("F5", "list-annotations", "GET",
            f"/api/v1/photos/{photo1_id}/annotations", None, H)
    _record("F5", "patch-annotation", "PATCH",
            f"/api/v1/annotations/{ann_id}",
            {"color_hex": "#00FF00", "expected_revision": ann_rev}, H)
    _record("F5", "annotation-revision-conflict", "PATCH",
            f"/api/v1/annotations/{ann_id}",
            {"color_hex": "#0000FF", "expected_revision": ann_rev}, H,
            note="409 revision_conflict")
    _record("F5", "delete-annotation-soft", "DELETE",
            f"/api/v1/annotations/{ann_id}", None, H,
            note="soft delete; include_inactive=true still shows it")

    r = _record("F5", "create-issue", "POST",
                f"/api/v1/projects/{project_id}/issues",
                {"title": "Honeycomb on column C-12",
                 "description": "Exposed aggregate after formwork removal",
                 "severity": "high",
                 "location_description": "Grid C-12, level 3",
                 "assigned_to": "foreman-zhang"}, H)
    issue_id = r.json()["issue_id"]
    issue_rev = r.json()["revision"]
    _record("F5", "list-issues", "GET",
            f"/api/v1/projects/{project_id}/issues?status=open", None, H)
    _record("F5", "issue-detail", "GET", f"/api/v1/issues/{issue_id}",
            None, H)
    _record("F5", "patch-issue", "PATCH", f"/api/v1/issues/{issue_id}",
            {"severity": "critical", "expected_revision": issue_rev}, H)
    _record("F5", "issue-invalid-transition", "PATCH",
            f"/api/v1/issues/{issue_id}",
            {"status": "closed", "expected_revision": issue_rev + 1}, H,
            note="409 invalid_status_transition: open -> closed is illegal")
    _record("F5", "link-before-photo", "POST",
            f"/api/v1/issues/{issue_id}/photos",
            {"photo_id": photo1_id, "role": "before"}, H)
    _record("F5", "link-after-photo", "POST",
            f"/api/v1/issues/{issue_id}/photos",
            {"photo_id": photo2_id, "role": "after"}, H)
    _record("F5", "issue-photos-view", "GET",
            f"/api/v1/issues/{issue_id}/photos", None, H)
    _record("F5", "comparison-image", "POST",
            f"/api/v1/issues/{issue_id}/comparison-image",
            {"before_photo_id": photo1_id, "after_photo_id": photo2_id},
            H, note="JPEG bytes; headers carry the derivative sha256")

    # ============================ F6 ====================================
    _scenario("F6", "Reports, delivery packages & offline sync",
              "Immutable snapshot reports; batch sync with per-op "
              "receipts; incremental event pull.")
    r = _record("F6", "generate-report", "POST",
                f"/api/v1/projects/{project_id}/reports",
                {"report_type": "inspection", "page_size": "a4",
                 "per_page": 2, "photo_ids": [photo1_id, photo2_id],
                 "issue_ids": [issue_id], "operator": "inspector-li",
                 "company_name": "CMS Engineering"}, H)
    report_id = r.json()["report_id"]
    _record("F6", "list-reports", "GET",
            f"/api/v1/projects/{project_id}/reports", None, H)
    _record("F6", "report-detail", "GET", f"/api/v1/reports/{report_id}",
            None, H)
    _record("F6", "report-download", "GET",
            f"/api/v1/reports/{report_id}/download", None, H,
            note="application/pdf bytes")
    _record("F6", "delivery-package", "POST",
            f"/api/v1/reports/{report_id}/delivery-package",
            {"include_originals": True, "include_derivatives": True,
             "include_evidence": True}, H, note="application/zip bytes")

    # cross-op dependency: create the project first, take the server id
    # from the RECEIPT (id_map), then register the photo against it.
    r = _record("F6", "sync-batch-project", "POST", "/api/v1/sync/batch",
                {"operations": [
                    {"op": "project_create", "operation_id": "fixture-op-1",
                     "payload": {"operation_id": "fixture-op-1",
                                 "local_id": "loc-proj-9",
                                 "name": "Offline-created project"}}]},
                H)
    new_project_id = r.json()["id_map"]["loc-proj-9"]
    _record("F6", "sync-batch-photo", "POST", "/api/v1/sync/batch",
            {"operations": [
                {"op": "photo_register", "operation_id": "fixture-op-2",
                 "payload": {"operation_id": "fixture-op-2",
                             "local_id": "loc-photo-9",
                             "server_project_id": new_project_id,
                             "idempotency_key": "fixture-sync-photo",
                             "original_sha256": sha1,
                             "original_filename": "OFFLINE_9.jpg",
                             "mime_type": "image/jpeg",
                             "byte_size": len(photo1_bytes),
                             "captured_at": "2026-11-25T18:00:00+00:00",
                             "device_id": "mac-dev-001"}}]},
            H, note="cross-op dependency via server id from receipts")
    _record("F6", "sync-batch-replay", "POST", "/api/v1/sync/batch",
            {"operations": [
                {"op": "photo_register", "operation_id": "fixture-op-2",
                 "payload": {"operation_id": "fixture-op-2",
                             "local_id": "loc-photo-9",
                             "server_project_id": new_project_id,
                             "idempotency_key": "fixture-sync-photo",
                             "original_sha256": sha1,
                             "original_filename": "OFFLINE_9.jpg",
                             "mime_type": "image/jpeg",
                             "byte_size": len(photo1_bytes),
                             "captured_at": "2026-11-25T18:00:00+00:00",
                             "device_id": "mac-dev-001"}}]},
            H, note="duplicate receipt from the ledger, zero re-execution")
    _record("F6", "sync-events-pull", "GET",
            "/api/v1/sync/events?cursor=0&limit=100", None, H)
    _record("F6", "sync-events-filter", "GET",
            "/api/v1/sync/events?cursor=0&limit=50&entity_type=project",
            None, H)

    # ==================== ERRORS (real calls) ==========================
    _scenario("ERRORS", "Typed error envelope catalogue",
              "Every body below is a REAL response of the running server.")
    _record("ERRORS", "missing-token", "GET", "/api/v1/projects", None, None)
    _record("ERRORS", "unregistered-token", "GET", "/api/v1/projects",
            None, {"X-Device-Token": "ff" * 32})
    # legacy derived token: pre-W012 devices.jsonl carrying a token
    # that equals sha256(public_key_b64) - the derived-credential design
    # W013-R1 refuses as a bearer secret (must re-enroll)
    derived = hashlib.sha256(pub_b64.encode("ascii")).hexdigest()
    legacy_reg = Path(os.environ["WORKCAM_DEVICE_REGISTRY"])
    legacy_reg.parent.mkdir(parents=True, exist_ok=True)
    with open(legacy_reg, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "device_id": "legacy-pre-w012-device", "token": derived,
            "public_key_b64": pub_b64}) + "\n")
    _record("ERRORS", "legacy-derived-token-refused", "GET",
            "/api/v1/projects", None,
            {"X-Device-Token": derived},
            note="W013-R1 P0: derived credential from public material "
                 "-> refused, must re-enroll for a random token "
                 "(requires a legacy devices.jsonl record to reach this "
                 "branch; an unknown token is plain unauthorized)")
    # a legacy NON-derived (already random) token keeps working
    legacy_random = "ab" * 32
    with open(legacy_reg, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "device_id": "legacy-random-device", "token": legacy_random,
            "public_key_b64": pub_b64}) + "\n")
    _record("ERRORS", "legacy-random-token-still-valid", "GET",
            "/api/v1/projects", None,
            {"X-Device-Token": legacy_random},
            note="read-compat: pre-W012 random legacy tokens stay valid")
    _record("ERRORS", "invalid-uuid", "GET", "/api/v1/projects/not-a-uuid",
            None, H)
    _record("ERRORS", "cross-owner-404", "GET",
            f"/api/v1/projects/{uuid4()}", None, H)
    _record("ERRORS", "empty-patch", "PATCH",
            f"/api/v1/projects/{project_id}", {}, H)
    _record("ERRORS", "upload-experimental-default-closed", "POST",
            "/upload/experimental/batches",
            {"total_bytes": 10, "total_sha256": "ee" * 32, "chunk_size": 5},
            None, note="503 without WORKCAM_UPLOAD_EXPERIMENTAL=1")
    _record("ERRORS", "time-anchor-invalid-batch-id", "POST",
            "/time-anchor/anchor",
            {"batch_id": "bad=batch-id", "chain_head_hash": "cc" * 32,
             "chain_tail_hash": "dd" * 32, "record_count": 1,
             "device_reported_start_time": now,
             "device_reported_end_time": now}, None,
            note="real 400 - the signing-field guard rejects '=' (field "
                 "injection) and control characters ONLY; spaces and "
                 "punctuation are ACCEPTED in batch_id (verified by "
                 "real call)")
    r = _client.post("/time-anchor/anchor", json={
        "batch_id": "err-anchor-1", "chain_head_hash": "cc" * 32,
        "chain_tail_hash": "dd" * 32, "record_count": 2,
        "device_reported_start_time": now, "device_reported_end_time": now})
    _record("ERRORS", "anchor-response-is-not-a-receipt", "POST",
            "/time-anchor/verify?check_storage=true", r.json(), None,
            note="real 422: the raw anchor response lacks the receipt's "
                 "required unsigned fields (device_reported_start_time/"
                 "end_time, stored_at); the client must assemble them")
    _record("ERRORS", "validation-422-oversize-page", "GET",
            "/api/v1/projects?limit=99999", None, H,
            note="limit clamped to 100 - this returns 200, not 422 "
                 "(documented clamp behavior)")

    # rate limit: 61 rapid requests with the same token
    _record("ERRORS", "rate-limited-429", "GET",
            "/api/v1/projects?limit=1", None, H)
    extra = 0
    for _ in range(70):
        r = _client.get("/api/v1/projects?limit=1", headers=H)
        if r.status_code == 429:
            _SCENARIOS[-1]["steps"][-1]["response"] = {
                "status": r.status_code, "headers": {},
                "body": r.json()}
            extra += 1
            break
    assert extra == 1, "rate limiter never fired - unexpected"

    import copy
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "backend/scripts/b02_w026_generate_fixtures.py",
        "baseline": BASELINE,
        "how_produced": ("Every request below was actually sent to "
                         "app.main:app (FastAPI TestClient) on a throwaway "
                         "business DB; responses are the real JSON/binary "
                         "outputs, unedited. Nothing is hand-written."),
        "mock_enrollment_code": ENROLL_CODE,
        "device_token_used_in_fixtures": token,
        "binary_note": ("__binary__ responses are recorded as {bytes, "
                        "sha256, media_type}; the byte payloads themselves "
                        "are not embedded in this JSON."),
        "scenarios": copy.deepcopy(_SCENARIOS),
    }


def main() -> None:
    # isolate the generator run from any live deployment
    _tmp = tempfile.mkdtemp(prefix="b02w026-fixtures-")
    os.environ["WORKCAM_BUSINESS_DB"] = str(Path(_tmp) / "biz.db")
    os.environ["WORKCAM_ORIGINAL_STORE"] = str(Path(_tmp) / "originals")
    os.environ["WORKCAM_ENROLLMENT_CODE"] = "MOCK-ENROLL-CODE-2626"
    os.environ["WORKCAM_DEVICE_REGISTRY"] = str(Path(_tmp) / "devices.jsonl")
    os.environ["WORKCAM_KEY_REGISTRY"] = str(Path(_tmp) / "keys.jsonl")
    os.environ["WORKCAM_RECORD_STORE"] = str(Path(_tmp) / "records.jsonl")
    os.environ["WORKCAM_TIME_ANCHOR_STORE"] = str(Path(_tmp) / "anchors")
    from fastapi.testclient import TestClient
    from app.main import app
    out = run_all(TestClient(app))
    target = Path(sys.argv[1] if len(sys.argv) > 1
                  else "b02_w026_mock_data.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    n = sum(len(s["steps"]) for s in out["scenarios"])
    print(f"wrote {target}: {len(out['scenarios'])} scenarios, {n} real calls")


if __name__ == "__main__":
    main()
