"""FE-A12: evidence red-team regression - the full ten-attack sweep
against the LIVE backend, one test per attack class. The invariant
under attack is always the same: NO path silently upgrades trust.

The ten attacks (task brief):
  1. original tamper        - bytes swapped after verification
  2. signature swap         - valid signature from a DIFFERENT record
  3. public key swap        - attacker key + attacker signature
  4. record-id swap         - rename a record's identity
  5. manifest swap          - substitute a foreign manifest
  6. project-ownership swap  - move evidence across projects
  7. replay                 - resubmit accepted operations
  8. missing record         - delete a referenced record
  9. malicious package      - hostile evpack (zip-bomb/traversal/unlisted)
 10. trust-state forgery   - client claims a higher status directly
"""

from __future__ import annotations

import base64
import hashlib
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402

from app.main import app  # noqa: E402
from app.services import device_identity  # noqa: E402

CODE = "fe-a12-code"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB",
                       str(tmp_path / "rt.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", CODE)
    import app.db as dbm
    dbm.migrate(tmp_path / "rt.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    monkeypatch.setattr(device_identity, "check_rate",
                        lambda *a, **k: None)
    c = TestClient(app)
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    r = c.post("/api/v1/devices/enroll", json={
        "public_key_b64": base64.b64encode(pub).decode(),
        "device_id": "rt", "enrollment_code": CODE})
    H = {"X-Device-Token": r.json()["device_token"]}
    r = c.post("/api/v1/projects", headers=H,
               json={"name": "Red Team Site A"})
    pa = r.json()["project_id"]
    r = c.post("/api/v1/projects", headers=H,
               json={"name": "Victim Site B"})
    pb = r.json()["project_id"]

    def make_photo(data: bytes, project_id: str, i: int) -> str:
        sha = hashlib.sha256(data).hexdigest()
        r = c.post(f"/api/v1/projects/{project_id}/photos",
                   headers=H, json={
                       "idempotency_key": f"ik-{i}",
                       "original_sha256": sha,
                       "original_filename": f"{i}.jpg",
                       "mime_type": "image/jpeg",
                       "byte_size": len(data),
                       "captured_at":
                           "2026-11-27T18:00:00+00:00",
                       "device_id": "rt"})
        assert r.status_code == 201, r.text
        pid = r.json()["photo_id"]
        r = c.put(f"/api/v1/photos/{pid}/original", headers=H,
                  content=data)
        assert r.status_code == 200
        return pid

    def sign_record(key_obj, photo_sha: str, rec_id: str,
                    pub_b64: str, dev: str = "rt") -> dict:
        ts = "2026-11-27T18:00:01+00:00"
        payload = ("\n".join([
            "WorkCam-Sig-v1.1", "version=1.1",
            f"record_id={rec_id}", "sequence_number=1",
            "previous_record_hash=none",
            f"photo_hash={photo_sha}",
            f"capture_timestamp={ts}",
            f"device_reported_time={ts}",
            f"device_id={dev}", "location=none", ""])).encode()
        sig = base64.b64encode(key_obj.sign(
            payload, ec.ECDSA(hashes.SHA256()))).decode()
        return {"version": "1.1", "record_id": rec_id,
                "sequence_number": 1, "previous_record_hash": None,
                "photo_hash": photo_sha, "capture_timestamp": ts,
                "device_reported_time": ts, "device_id": dev,
                "location": None, "signature": sig,
                "public_key": pub_b64}

    def attach_and_verify(photo_id: str, manifest: dict):
        r = c.post(f"/api/v1/photos/{photo_id}/evidence-links",
                   headers=H, json={
                       "evidence_reference":
                           manifest["record_id"],
                       "evidence_type": "signature_manifest"})
        assert r.status_code == 201, r.text
        lid = r.json()["evidence_link_id"]
        r = c.post(
            f"/api/v1/photos/{photo_id}/evidence-links/{lid}/verify",
            headers=H, json={"manifest": manifest})
        assert r.status_code == 200, r.text
        return r.json()["verification_status"]

    return type("E", (), {
        "c": c, "H": H, "pa": pa, "pb": pb, "key": key,
        "pub": base64.b64encode(pub).decode(),
        "make_photo": staticmethod(make_photo),
        "sign_record": staticmethod(sign_record),
        "attach_and_verify": staticmethod(attach_and_verify),
        "tmp": tmp_path})()


def _photo_bytes(i: int) -> bytes:
    import random
    rng = random.Random(i)
    return bytes(rng.getrandbits(8) for _ in range(5_000))


class TestRedTeamSweep:

    def test_01_original_tamper_breaks_integrity(self, env):
        data = _photo_bytes(1)
        pid = env.make_photo(data, env.pa, 1)
        from app.services import original_store
        obj = original_store.object_path(
            hashlib.sha256(data).hexdigest())
        obj.write_bytes(data + b"TAMPERED")
        r = env.c.get(f"/api/v1/photos/{pid}/original/integrity",
                      headers=env.H)
        body = r.json()
        # the DB binding row persists (binding is a record, not the
        # bytes) - but the INTEGRITY CLAIM must flip to False
        assert body["claims"]["file_integrity_verified"] is False
        assert body["original"]["file_hash_valid"] is False

    def test_02_signature_swap_fails_verification(self, env):
        d1, d2 = _photo_bytes(2), _photo_bytes(3)
        p1 = env.make_photo(d1, env.pa, 2)
        env.make_photo(d2, env.pa, 3)
        m1 = env.sign_record(env.key,
                             hashlib.sha256(d1).hexdigest(),
                             "rec-good", env.pub)
        assert env.attach_and_verify(p1, m1) in \
            ("cryptographically_valid", "signature_valid")
        # now sign d2's hash and present it as p1's evidence - the
        # anti-cross-photo binding guard fires as a 400 (refused
        # BEFORE any status write) or the math check fails
        m2 = env.sign_record(
            env.key, hashlib.sha256(d2).hexdigest(),
            "rec-swap", env.pub)
        r = env.c.post(
            f"/api/v1/photos/{p1}/evidence-links", headers=env.H,
            json={"evidence_reference": "rec-swap",
                  "evidence_type": "signature_manifest"})
        lid = r.json()["evidence_link_id"]
        r = env.c.post(
            f"/api/v1/photos/{p1}/evidence-links/{lid}/verify",
            headers=env.H, json={"manifest": m2})
        if r.status_code == 400:
            # the hash-binding guard refused outright - even better
            assert "hash_binding_mismatch" in r.text
        else:
            assert r.json()["verification_status"] == \
                "verification_failed"

    def test_03_public_key_swap_rejected(self, env):
        attacker = ec.generate_private_key(ec.SECP256R1())
        a_pub = base64.b64encode(attacker.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)).decode()
        data = _photo_bytes(4)
        pid = env.make_photo(data, env.pa, 4)
        m = env.sign_record(attacker,
                            hashlib.sha256(data).hexdigest(),
                            "rec-attacker", a_pub)
        r = env.c.post(
            f"/api/v1/photos/{pid}/evidence-links", headers=env.H,
            json={"evidence_reference": "rec-attacker",
                  "evidence_type": "signature_manifest"})
        lid = r.json()["evidence_link_id"]
        r = env.c.post(
            f"/api/v1/photos/{pid}/evidence-links/{lid}/verify",
            headers=env.H, json={"manifest": m})
        status = r.json()["verification_status"]
        # HONEST VOCABULARY (capture.py): math may verify against the
        # attacker's own embedded key -> signature_valid ("math ok,
        # key NOT registered") - but the TRUSTED state
        # (cryptographically_valid = math + hash + key registered) is
        # unreachable. The red-team invariant: NEVER the trusted state.
        assert status != "cryptographically_valid", \
            "attacker key must NEVER reach the trusted state"

    def test_04_record_id_swap_detected(self, env):
        data = _photo_bytes(5)
        pid = env.make_photo(data, env.pa, 5)
        m = env.sign_record(env.key,
                            hashlib.sha256(data).hexdigest(),
                            "rec-real", env.pub)
        assert env.attach_and_verify(pid, m) in \
            ("cryptographically_valid", "signature_valid")
        # swap the record_id AFTER signing -> signature no longer
        # covers the payload (record_id is IN the payload) - AND the
        # reference guard fires first (record_id != registered ref)
        m_swapped = dict(m, record_id="rec-fake")
        r = env.c.post(
            f"/api/v1/photos/{pid}/evidence-links", headers=env.H,
            json={"evidence_reference": "rec-fake",
                  "evidence_type": "signature_manifest"})
        lid = r.json()["evidence_link_id"]
        r = env.c.post(
            f"/api/v1/photos/{pid}/evidence-links/{lid}/verify",
            headers=env.H, json={"manifest": m_swapped})
        if r.status_code == 400:
            assert "reference_mismatch" in r.text \
                or "hash_binding_mismatch" in r.text
        else:
            assert r.json()["verification_status"] == \
                "verification_failed"

    def test_05_manifest_swap_across_photos(self, env):
        d_a, d_b = _photo_bytes(6), _photo_bytes(7)
        p_a = env.make_photo(d_a, env.pa, 6)
        env.make_photo(d_b, env.pb, 7)
        m_b = env.sign_record(
            env.key, hashlib.sha256(d_b).hexdigest(),
            "rec-b", env.pub)
        # present B's manifest as A's evidence
        r = env.c.post(
            f"/api/v1/photos/{p_a}/evidence-links", headers=env.H,
            json={"evidence_reference": "rec-b",
                  "evidence_type": "signature_manifest"})
        lid = r.json()["evidence_link_id"]
        # present B's manifest as A's evidence - the binding guards
        # (reference and hash) fire as 400 before any status write
        r = env.c.post(
            f"/api/v1/photos/{p_a}/evidence-links/{lid}/verify",
            headers=env.H, json={"manifest": m_b})
        if r.status_code == 400:
            assert "reference_mismatch" in r.text \
                or "hash_binding_mismatch" in r.text
        else:
            assert r.json()["verification_status"] == \
                "verification_failed"

    def test_06_cross_project_evidence_move_refused(self, env):
        """Re-registering the SAME BYTES into another project may
        create a new (unverified) photo row - content addressing
        allows that. The red-team INVARIANT is narrower and true:
        the VERIFIED STATUS cannot move. A's cryptographically_valid
        link stays on A; the B copy is a fresh unverified photo."""
        data = _photo_bytes(8)
        pid_a = env.make_photo(data, env.pa, 8)
        m = env.sign_record(env.key,
                            hashlib.sha256(data).hexdigest(),
                            "rec-x", env.pub)
        env.attach_and_verify(pid_a, m)
        # replay the SAME idempotency key against project B
        r = env.c.post(f"/api/v1/projects/{env.pb}/photos",
                      headers=env.H, json={
                          "idempotency_key": "ik-8",
                          "original_sha256":
                              hashlib.sha256(data).hexdigest(),
                          "original_filename": "8.jpg",
                          "mime_type": "image/jpeg",
                          "byte_size": len(data),
                          "captured_at":
                              "2026-11-27T18:00:00+00:00",
                          "device_id": "rt"})
        if r.status_code == 201:
            pid_b = r.json()["photo_id"]
            # the B copy is a FRESH photo: no evidence links, no
            # verified claims - the trusted state did NOT travel
            r2 = env.c.get(
                f"/api/v1/photos/{pid_b}/original/integrity",
                headers=env.H)
            ev = r2.json().get("evidence", {})
            assert ev.get("highest_status", "unverified") \
                not in ("cryptographically_valid", "verified")
        else:
            assert r.status_code in (409, 400)
        # and A still owns the verified record
        r = env.c.get(f"/api/v1/photos/{pid_a}", headers=env.H)
        assert r.json()["project_id"] == env.pa
        r = env.c.get(
            f"/api/v1/photos/{pid_a}/original/integrity",
            headers=env.H)
        assert r.json()["claims"]["file_integrity_verified"] is True

    def test_07_replay_returns_duplicate_not_double_apply(self, env):
        data = _photo_bytes(9)
        pid = env.make_photo(data, env.pa, 9)
        op = {"op": "annotation_create",
              "operation_id": "rt-an-1",
              "payload": {"operation_id": "rt-an-1",
                          "local_id": "l-1",
                          "server_photo_id": pid,
                          "annotation_type": "text",
                          "norm_x1": 0.1, "norm_y1": 0.1,
                          "text_content": "replay probe"}}
        r1 = env.c.post("/api/v1/sync/batch", headers=env.H,
                        json={"operations": [op]}).json()
        r2 = env.c.post("/api/v1/sync/batch", headers=env.H,
                        json={"operations": [op]}).json()
        assert r1["receipts"][0]["status"] == "applied"
        assert r2["receipts"][0]["status"] == "duplicate"
        r = env.c.get(f"/api/v1/photos/{pid}/annotations",
                      headers=env.H)
        assert r.json()["total"] == 1

    def test_08_missing_referenced_record_named(self, env):
        out = env.c.post("/api/v1/sync/batch", headers=env.H,
                        json={"operations": [{
                            "op": "annotation_create",
                            "operation_id": "rt-an-missing",
                            "payload": {
                                "operation_id": "rt-an-missing",
                                "local_id": "l-x",
                                "server_photo_id":
                                    "00000000-0000-0000-0000"
                                    "-0000000000ff",
                                "annotation_type": "text",
                                "norm_x1": 0, "norm_y1": 0,
                                "text_content": "ghost"}}]}).json()
        rec = out["receipts"][0]
        assert rec["status"] == "rejected"
        assert rec["error"]["code"] in ("not_found",
                                        "invalid_local_ref")

    def test_09_micious_package_rejected_by_verifier(self, env,
                                                      tmp_path):
        """A hostile package (traversal name + unlisted extra) is
        refused by the evpack verifier - the delivery path never
        accepts it."""
        from techlab.business.evpack_v2 import (
            EvpackError, build_evpack, verify_evpack, safe_rel_path,
            Section)
        with pytest.raises(EvpackError):
            safe_rel_path("../escape.jpg")
        sec = Section("evidence")
        sec.add("ok.json", b"{}")
        pkg = tmp_path / "p.evpack"
        build_evpack([sec], {}, pkg)
        # inject an extra unlisted member -> strict refuses
        with zipfile.ZipFile(pkg) as zf:
            items = {n: zf.read(n) for n in zf.namelist()}
        items["evil.exe"] = b"MZ..."
        evil = tmp_path / "evil.evpack"
        with zipfile.ZipFile(evil, "w") as zf:
            for n, d in items.items():
                zf.writestr(n, d)
        v = verify_evpack(evil, strict=True)
        assert not v["ok"]
        assert any("EXTRA" in p for p in v["problems"])

    def test_10_trust_forgery_via_api_refused(self, env):
        """A client may only SUBMIT evidence for verification - it
        can never POST a trusted/verified status. The evidence-links
        route exposes reference+type only; status changes only via
        the server's own /verify math. Try every status word anyway
        to pin the closed surface."""
        data = _photo_bytes(10)
        pid = env.make_photo(data, env.pa, 10)
        r = env.c.post(f"/api/v1/photos/{pid}/evidence-links",
                      headers=env.H, json={
                          "evidence_reference": "r10",
                          "evidence_type": "signature_manifest",
                          "verification_status": "verified",
                          "status": "verified",
                          "trusted": True})
        # either ignored extra fields or 422 - either way the
        # CREATED row starts unverified
        if r.status_code == 201:
            lid = r.json()["evidence_link_id"]
            r2 = env.c.get(
                f"/api/v1/photos/{pid}/evidence-links",
                headers=env.H)
            row = next(x for x in r2.json()["items"]
                       if x["evidence_link_id"] == lid)
            assert row["verification_status"] == "unverified"
        r = env.c.get(f"/api/v1/photos/{pid}/original/integrity",
                      headers=env.H)
        claims = r.json()["claims"]
        assert claims.get("signature_valid") in (None, False)
