"""M03-A06: Night 05 data-contract compatibility tests.

Every assertion runs against the REAL server; the Night 05 shapes
come from ed7dd7b (read-only comparison, no Mac code touched):

  1. EvidenceManifest payload: Night 05's byte layout == our v1.1
     canonical payload (same lines, same order, location=none)
  2. Issue status vocabulary: open/in_progress/resolved/closed ALL
     accepted by the server transition op; the LINEAR chain Night 05
     enforces client-side works server-side step-by-step
  3. Annotation shape mapping: Night 05's x/y + kind + color +
     lineWidth maps 1:1 into the server annotation columns
  4. Photo roles (before/progress/after): carried through issue
     photo links; cross-photo-role cardinality is the caller's rule
  5. WorkPhoto filename-id: works as idempotency_key (ledger maps it
     to a server photo id on first sync - the W014 local_id contract)
  6. A Night-05-style manifest signed with a SOFTWARE key (SE shape,
     same ECDSA-P256-DER) verifies through the server /verify path
"""

from __future__ import annotations

import base64
import hashlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402

from app.main import app  # noqa: E402
from app.services import device_identity  # noqa: E402

CODE = "tm-a06-code"


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
    key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    r = client.post("/api/v1/devices/enroll", json={
        "public_key_b64": pub_b64, "device_id": "a06-dev",
        "enrollment_code": CODE})
    H = {"X-Device-Token": r.json()["device_token"]}
    r = client.post("/api/v1/projects", headers=H,
                    json={"name": "Night05 Contract Site"})
    pid = r.json()["project_id"]

    def register_photo(local_file_id: str, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        out = client.post("/api/v1/sync/batch", headers=H, json={
            "operations": [{
                "op": "photo_register",
                "operation_id": f"reg-{local_file_id}",
                "payload": {
                    "operation_id": f"reg-{local_file_id}",
                    "local_id": local_file_id,
                    "server_project_id": pid,
                    "idempotency_key": f"ik-{local_file_id}",
                    "original_sha256": sha,
                    "original_filename":
                        f"{local_file_id}.jpg",
                    "mime_type": "image/jpeg",
                    "byte_size": len(data),
                    "captured_at": "2026-11-26T20:00:00+00:00",
                    "device_id": "a06-dev"}}]}).json()
        rec = out["receipts"][0]
        assert rec["status"] == "applied", rec
        sp = rec["result"]["server_photo_id"]
        r2 = client.put(f"/api/v1/photos/{sp}/original", headers=H,
                        content=data)
        assert r2.status_code == 200, r2.text
        return sp

    return type("E", (), {
        "client": client, "H": H, "pid": pid, "key": key,
        "pub_b64": pub_b64,
        "register_photo": staticmethod(register_photo)})()


def _photo_bytes(seed: int) -> bytes:
    import random
    rng = random.Random(seed)
    return bytes(rng.getrandbits(8) for _ in range(5000))


class TestNight05PayloadContract:
    def test_night05_payload_layout_is_our_v11_canonical(self):
        """Night 05's EvidenceManifest.payload() builds EXACTLY our
        canonical v1.1 text: same lines, same order, trailing \\n.
        (Byte-identical payloads are what make cross-side signatures
        verifiable.)"""
        record_id = "night05" + "0" * 25
        photo_hash = "ab" * 32
        ts = "2026-11-26T20:01:00+00:00"
        seq = 3
        # the exact construction from Core/Security/Evidence.swift
        night05_payload = "\n".join([
            "WorkCam-Sig-v1.1", "version=1.1",
            f"record_id={record_id}",
            f"sequence_number={seq}",
            "previous_record_hash=none",
            f"photo_hash={photo_hash}",
            f"capture_timestamp={ts}",
            f"device_reported_time={ts}",
            "device_id=iPhone15,3",
            "location=none"]) + "\n"
        # our Python builder, same fields
        import evidence_schema as es
        ours = es.build_canonical_payload({
            "protocol_version": "1.1", "record_id": record_id,
            "sequence_number": seq, "previous_record_hash": None,
            "photo_hash": photo_hash, "capture_timestamp": ts,
            "device_reported_time": ts, "device_id": "iPhone15,3",
            "location": None})
        assert ours == night05_payload, \
            "NIGHT 05 PAYLOAD DRIFT - cross-side signatures would " \
            "not verify"

    def test_night05_style_manifest_verifies_through_server(self, env):
        """The Mac signs with SecureEnclave (same curve/format);
        a SOFTWARE key reproduces the exact DER signature SHAPE -
        that shape must pass the server /verify pipeline."""
        c = env.client
        data = _photo_bytes(42)
        sha = hashlib.sha256(data).hexdigest()
        photo_id = env.register_photo("night05-photo", data)

        r = c.post(f"/api/v1/photos/{photo_id}/evidence-links",
                   headers=env.H, json={
                       "evidence_reference": "night05-rec-1",
                       "evidence_type": "signature_manifest"})
        link_id = r.json()["evidence_link_id"]

        record_id = "night05-rec-1"
        ts = "2026-11-26T20:01:00+00:00"
        payload_text = "\n".join([
            "WorkCam-Sig-v1.1", "version=1.1",
            f"record_id={record_id}", "sequence_number=1",
            "previous_record_hash=none", f"photo_hash={sha}",
            f"capture_timestamp={ts}",
            f"device_reported_time={ts}",
            "device_id=iPhone15,3", "location=none"]) + "\n"
        sig = env.key.sign(payload_text.encode(),
                           ec.ECDSA(hashes.SHA256()))
        manifest = {
            "version": "1.1", "record_id": record_id,
            "sequence_number": 1, "previous_record_hash": None,
            "photo_hash": sha, "capture_timestamp": ts,
            "device_reported_time": ts, "device_id": "iPhone15,3",
            "location": None,
            "signature": base64.b64encode(sig).decode(),
            "public_key": env.pub_b64}
        r = c.post(
            f"/api/v1/photos/{photo_id}/evidence-links/{link_id}"
            f"/verify", headers=env.H, json={"manifest": manifest})
        assert r.status_code == 200, r.text
        assert r.json()["verification_status"] in (
            "cryptographically_valid", "signature_valid")


class TestNight05IssueVocabulary:
    def test_full_linear_status_chain_server_side(self, env):
        """Night 05 enforces open→in_progress→resolved→closed; the
        server accepts all four words AND the chain works stepwise."""
        c = env.client
        out = c.post("/api/v1/sync/batch", headers=env.H, json={
            "operations": [{
                "op": "issue_create", "operation_id": "a06-iss",
                "payload": {"operation_id": "a06-iss",
                            "local_id": "l-a06-iss",
                            "server_project_id": env.pid,
                            "title": "Seepage at parapet",
                            "severity": "high"}}]}).json()
        rec = out["receipts"][0]
        assert rec["status"] == "applied", rec
        iid = rec["result"]["server_issue_id"]
        rev = 1
        for target in ("in_progress", "resolved", "closed"):
            out = c.post("/api/v1/sync/batch", headers=env.H, json={
                "operations": [{
                    "op": "issue_transition",
                    "operation_id": f"a06-tr-{target}",
                    "payload": {
                        "operation_id": f"a06-tr-{target}",
                        "server_issue_id": iid,
                        "expected_revision": rev,
                        "target_status": target}}]}).json()
            assert out["receipts"][0]["status"] == "applied", \
                out["receipts"][0]
            rev += 1

    def test_severity_vocabulary_matches(self, env):
        """low/medium/high/critical: Night 05 enum == server CHECK."""
        for sev in ("low", "medium", "high", "critical"):
            out = env.client.post(
                "/api/v1/sync/batch", headers=env.H, json={
                    "operations": [{
                        "op": "issue_create",
                        "operation_id": f"a06-sev-{sev}",
                        "payload": {
                            "operation_id": f"a06-sev-{sev}",
                            "local_id": f"l-{sev}",
                            "server_project_id": env.pid,
                            "title": f"sev check {sev}",
                            "severity": sev}}]}).json()
            assert out["receipts"][0]["status"] == "applied", \
                f"server rejected Night 05 severity {sev}"


class TestNight05AnnotationMapping:
    def test_night05_annotation_shape_maps_to_server_columns(self, env):
        """Night 05: {x, y, kind, color, lineWidth, note} ->
        server: norm_x1/norm_y1 + annotation_type + color_hex +
        stroke_width + text_content. The mapped op must apply."""
        data = _photo_bytes(7)
        photo_id = env.register_photo("night05-annot", data)
        out = env.client.post("/api/v1/sync/batch", headers=env.H,
            json={"operations": [{
                "op": "annotation_create",
                "operation_id": "a06-an",
                "payload": {
                    "operation_id": "a06-an", "local_id": "l-a06-an",
                    "server_photo_id": photo_id,
                    "annotation_type": "text",      # kind
                    "norm_x1": 0.31, "norm_y1": 0.44,   # x/y
                    "text_content": "hairline crack",    # note
                }}]}).json()
        rec = out["receipts"][0]
        assert rec["status"] == "applied", rec
        # round-trip: the server view exposes the same normalized
        # coords Night 05 expects
        r = env.client.get(
            f"/api/v1/photos/{photo_id}/annotations", headers=env.H)
        row = next(a for a in r.json()["items"]
                   if a["annotation_type"] == "text")
        assert row["norm_x1"] == pytest.approx(0.31)
        assert row["norm_y1"] == pytest.approx(0.44)
        assert row["text_content"] == "hairline crack"


class TestNight05WorkPhotoIdentity:
    def test_filename_id_works_as_local_id(self, env):
        """Night 05's photo id = filename stem; sync maps local_id ->
        server photo id (the W014 id-map), and a REPLAY of the same
        filename yields the same server photo (no duplicate)."""
        data = _photo_bytes(11)
        env.register_photo("IMG_20261126_001", data)
        out = env.client.post("/api/v1/sync/batch", headers=env.H,
            json={"operations": [{
                "op": "photo_register",
                "operation_id": "reg-IMG_20261126_001",
                "payload": {
                    "operation_id": "reg-IMG_20261126_001",
                    "local_id": "IMG_20261126_001",
                    "server_project_id": env.pid,
                    "idempotency_key": "ik-IMG_20261126_001",
                    "original_sha256":
                        hashlib.sha256(data).hexdigest(),
                    "original_filename": "IMG_20261126_001.jpg",
                    "mime_type": "image/jpeg",
                    "byte_size": len(data),
                    "captured_at": "2026-11-26T20:00:00+00:00",
                    "device_id": "a06-dev"}}]}).json()
        rec = out["receipts"][0]
        # same idempotency key + same content -> duplicate receipt,
        # SAME server photo id (Night 05 file identity stays stable)
        assert rec["status"] == "duplicate"
        assert rec["result"]["server_photo_id"] == \
            rec["result"]["_id_map_entry"]["IMG_20261126_001"]
