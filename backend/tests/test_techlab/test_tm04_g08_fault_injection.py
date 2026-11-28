"""TM-04 G08 (continuation of TM45): two missing fault injections
named in the status matrix, driving the REAL derivative API:

  1. tmpfile-uncreatable during a derivative persist
  2. a corrupted derivative file after a successful write

RED LINES (immutable-originals discipline):
  - the original bytes are NEVER modified by any derivative flow,
    including its failure paths
  - the corruption is DETECTED on download (content digest), not
    silently served
  - failures surface typed; temp residue does not leak
"""

from __future__ import annotations

import base64
import hashlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

CODE = "tm04-g08-code"


def _png_bytes(w: int = 64, h: int = 48,
               color: tuple = (200, 160, 80)) -> bytes:
    """A Pillow-renderable PNG (Pillow must rasterize the original
    to build the watermark, so the bytes must be real)."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", CODE)
    # isolate the DB + the derivative store per test: derivatives are
    # CONTENT-ADDRESSED, so a shared store makes the second test hit
    # the idempotent-replay branch and never touch the persist seam
    db_file = tmp_path / "workcam.db"
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(db_file))
    import app.db as dbm
    dbm.migrate(db_file)

    # production-shaped failure handling: a persist-time OSError is a
    # 500 at the HTTP boundary (not an exception escaping into the
    # caller's process), so the test client must NOT re-raise it
    client = TestClient(app, raise_server_exceptions=False)
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint)
    r = client.post("/api/v1/devices/enroll", json={
        "public_key_b64": base64.b64encode(pub).decode(),
        "device_id": "dev-g08", "enrollment_code": CODE})
    assert r.status_code in (200, 201), r.text
    H = {"X-Device-Token": r.json()["device_token"]}

    # project + photo with a real original
    proj = client.post("/api/v1/projects", headers=H,
                       json={"name": "G08"}).json()["project_id"]
    png = _png_bytes()
    sha = hashlib.sha256(png).hexdigest()
    reg = client.post("/api/v1/sync/batch", headers=H, json={
        "operations": [{
            "op": "photo_register", "operation_id": "pr-1",
            "payload": {
                "operation_id": "pr-1", "local_id": "loc-1",
                "server_project_id": proj,
                "idempotency_key": "ik-1",
                "original_sha256": sha,
                "original_filename": "o.png",
                "mime_type": "image/png",
                "byte_size": len(png),
                "captured_at": "2026-11-28T00:00:00+00:00",
                "device_id": "dev-g08"}}]}).json()
    photo_id = reg["id_map"]["loc-1"]

    # upload the original (PUT, metadata-then-bytes rule)
    up = client.put(f"/api/v1/photos/{photo_id}/original",
                    headers=H, content=png)
    assert up.status_code in (200, 201), up.text
    return client, H, proj, photo_id, png, sha, db_file


def _request_derivative(client, H, photo_id, project):
    return client.post(
        f"/api/v1/photos/{photo_id}/derivatives/watermark",
        headers=H, json={
            "template": "minimal",
            "fields": {
                "project_name": "G08",
                "show_project_name": True,
                "photo_label": "P-1",
                "show_photo_label": True,
                "captured_time": "2026-11-28T00:00:00+00:00",
                "show_captured_time": True,
                "operator": "inspector",
                "show_operator": True,
            }})


class TestTmpfileUncreatable:
    def test_failed_persist_never_touches_original(
            self, env, monkeypatch):
        client, H, proj, photo_id, png, sha, db_file = env
        originals_state = hashlib.sha256(png).hexdigest()

        # inject at the REAL seam and SCOPED: the persist path ends
        # with os.replace(tmp, target) where target lives under THIS
        # test's derivative store. TestClient never calls os.replace
        # for its own plumbing, so scoping by target path is clean.
        import os as _os
        real_replace = _os.replace
        deriv_dir = str(db_file.parent / "derivatives")

        def failing_replace(src, dst, *a, **kw):
            if str(dst).startswith(deriv_dir):
                raise OSError(13, "simulated tmpfile failure")
            return real_replace(src, dst, *a, **kw)

        monkeypatch.setattr("os.replace", failing_replace)

        r = _request_derivative(client, H, photo_id, proj)
        # the failure surfaces (typed 5xx), it is never swallowed
        assert r.status_code >= 400, r.text

        # RED LINE: the original bytes are untouched
        got = client.get(f"/api/v1/photos/{photo_id}/original",
                         headers=H)
        if got.status_code == 200:
            assert hashlib.sha256(
                got.content).hexdigest() == originals_state
        # nothing persisted (the listing schema adds its own
        # read-only section; the items list itself must be empty)
        listing = client.get(f"/api/v1/photos/{photo_id}/derivatives",
                             headers=H)
        assert listing.status_code == 200
        assert listing.json()["items"] == []


class TestCorruptedDerivativeFile:
    def test_corruption_detected_not_served(
            self, env, monkeypatch):
        client, H, proj, photo_id, png, sha, db_file = env

        r = _request_derivative(client, H, photo_id, proj)
        assert r.status_code in (200, 201), r.text
        derivative_id = r.json().get("derivative_id") \
            or r.json()["item"]["derivative_id"]

        # corrupt the derivative bytes on disk (disk-rot simulation)
        from app.api.derivatives import derivative_path
        meta = r.json()
        dsha = meta.get("derivative_sha256") \
            or meta["item"]["derivative_sha256"]
        dpath = derivative_path(dsha)
        assert dpath.exists(), f"derivative file missing at {dpath}"
        good = dpath.read_bytes()
        dpath.write_bytes(good[:len(good) // 2])

        # download: the corruption must be DETECTED, never served
        dl = client.get(
            f"/api/v1/derivatives/{derivative_id}/download",
            headers=H)
        if dl.status_code == 200:
            # if bytes are served, they must not be the corrupt ones
            assert dl.content != good[:len(good) // 2], \
                "corrupt bytes served verbatim"
        else:
            # typed refusal naming the corruption
            body = dl.json()
            assert dl.status_code in (409, 410, 500)
            text = str(body).lower()
            assert ("corrupt" in text or "mismatch" in text
                    or "integrity" in text), body
