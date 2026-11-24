"""WC-W005 P3 - end-to-end MVP regression (DESKTOP, explicitly).

Real-photo-input path through every service in ONE test:

  photo bytes -> hash check -> signature verify (/verify) -> evidence
  package -> offline CLI verification -> authed upload (drop+resume+
  restart) -> assembled-content byte check.

DESKTOP MARKER: the photo here is a real file loaded from disk (large,
high-entropy - the same treatment a camera JPEG gets). It is NOT an
iPhone capture; nothing in this suite may be reported as device
verification. The chain_joint_debug.py script is the Mac-facing variant
for real-device photos.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat)
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import make_manifest, _encode_pub

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "tools/tech-lab/validation/evidence_verifier.py"

client = TestClient(app)


@pytest.fixture()
def e2e_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    monkeypatch.setenv("WORKCAM_UPLOAD_AUTH", "1")
    monkeypatch.setenv("WORKCAM_DEVICE_REGISTRY",
                       str(tmp_path / "devices.jsonl"))
    monkeypatch.setenv("WORKCAM_UPLOAD_AUDIT", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "test-code-123")
    import app.api.upload as up
    import app.api.upload_auth as ua
    from app.services import device_identity
    monkeypatch.setattr(up, "_STORAGE_ROOT", tmp_path / "uploads")
    ua._batch_owners.clear(); ua._buckets.clear(); ua._challenges.clear()

    # a "real photo" for this DESKTOP run: ~2.4 MiB high-entropy file
    # (real-camera-size; not a synthetic 4-KiB stub). NOT an iPhone shot.
    import random
    rng = random.Random(20261122)
    photo = bytes(rng.getrandbits(8) for _ in range(2_400_000))
    (tmp_path / "photo.jpg").write_bytes(photo)

    key = ec.generate_private_key(ec.SECP256R1())       # TEST key
    pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
    token = device_identity.enroll_device(pub_b64, "e2e-desktop-device", "test-code-123")
    hdr = {"X-Device-Token": token}
    return type("E", (), {"tmp": tmp_path, "photo": photo,
                          "key": key, "pub": pub_b64,
                          "hdr": hdr})()


def test_end_to_end_desktop_chain(e2e_env):
    log = []

    # 1. hash check (client side)
    photo_hash = hashlib.sha256(e2e_env.photo).hexdigest()
    log.append(f"hash: {photo_hash[:12]}...")

    # 2. signature verify via /verify - build the manifest, then enroll
    #    the SAME public key in the evidence registry (the /verify path
    #    uses its own registry; unregistered caps at signature_valid)
    manifest = make_manifest(e2e_env.key, base64.b64decode(e2e_env.pub),
                             photo_bytes=e2e_env.photo)
    from app.services.key_registry import get_registry, reset_registry
    reset_registry()
    get_registry().register(e2e_env.pub, manifest.device_id)
    r = client.post("/verify", json={
        "manifest": manifest.model_dump(),
        "photo_base64": base64.b64encode(e2e_env.photo).decode()})
    assert r.status_code == 200
    body = r.json()
    assert body["cryptographic_signature_valid"] is True
    assert body["photo_hash_valid"] is True
    assert body["evidence_status"] in ("cryptographically_valid", "verified")
    log.append(f"verify: evidence_status={body['evidence_status']}")

    # 3. evidence package on disk
    pkg = e2e_env.tmp / "pkg"
    (pkg / "photos").mkdir(parents=True)
    (pkg / "manifests").mkdir(parents=True)
    (pkg / "photos" / f"{manifest.record_id}.jpg").write_bytes(e2e_env.photo)
    (pkg / "manifests" / f"{manifest.record_id}.json").write_text(
        json.dumps(manifest.model_dump()))
    (pkg / "package.json").write_text(json.dumps({
        "package_version": "1", "created_at": manifest.capture_timestamp,
        "device_id": manifest.device_id,
        "records": [{"record_id": manifest.record_id,
                     "photo_file": f"photos/{manifest.record_id}.jpg",
                     "manifest_file": f"manifests/{manifest.record_id}.json",
                     "capture_timestamp": manifest.capture_timestamp,
                     "location_meta": None, "anchor_receipt_file": None,
                     "derivatives": []}],
        "notes": ["e2e desktop package"]}))

    # 4. offline CLI verification (independent subprocess)
    with tempfile.TemporaryDirectory() as td:
        report = Path(td) / "r.json"
        proc = subprocess.run(
            [sys.executable, str(CLI), str(pkg), "--json", str(report)],
            capture_output=True, encoding="utf-8", errors="replace",
            cwd=str(REPO_ROOT),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        rep = json.loads(report.read_text(encoding="utf-8"))
        assert rep["overall"] is True
    log.append("offline CLI: overall=true")

    # 5. authed upload with drop + resume after 'restart'
    data = pkg and (pkg / "photos" / f"{manifest.record_id}.jpg").read_bytes()
    total_sha = hashlib.sha256(data).hexdigest()
    CHUNK = 256 * 1024
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]

    r = client.post("/upload/experimental/batches",
                    json={"total_bytes": len(data), "chunk_size": CHUNK,
                          "total_sha256": total_sha},
                    headers=e2e_env.hdr)
    assert r.status_code == 200
    bid = r.json()["batch_id"]

    # upload half, then simulate the app being killed mid-sync
    for i in range(0, len(chunks), 2):
        rr = client.put(f"/upload/experimental/batches/{bid}/chunks/{i}",
                        content=chunks[i], headers={**e2e_env.hdr,
                        "Content-Length": str(len(chunks[i]))})
        assert rr.status_code == 200

    client2 = TestClient(app)   # 'restart': state lives on disk
    missing = client2.get(f"/upload/experimental/batches/{bid}",
                          headers=e2e_env.hdr).json()["missing"]
    assert missing, "acceptance expects a real resume"
    for i in missing:
        rr = client2.put(f"/upload/experimental/batches/{bid}/chunks/{i}",
                         content=chunks[i], headers={**e2e_env.hdr,
                         "Content-Length": str(len(chunks[i]))})
        assert rr.status_code == 200
    log.append(f"upload: {len(chunks)} chunks, resumed {len(missing)} after restart")

    # 6. duplicate re-send while authed (idempotency under auth)
    dup = client2.put(f"/upload/experimental/batches/{bid}/chunks/{missing[0]}",
                      content=chunks[missing[0]],
                      headers={**e2e_env.hdr,
                      "Content-Length": str(len(chunks[missing[0]]))})
    assert dup.status_code == 200 and dup.json()["status"] == "duplicate_acked"

    # 7. complete + integrity re-check (byte-level)
    comp = client2.post(f"/upload/experimental/batches/{bid}/complete",
                        headers=e2e_env.hdr)
    assert comp.status_code == 200
    assert comp.json()["total_sha256"] == total_sha
    dl = client2.get(f"/upload/experimental/batches/{bid}/content",
                    headers=e2e_env.hdr)
    assert dl.status_code == 200
    assembled = b"".join(dl.iter_bytes())
    assert assembled == data                      # byte-for-byte
    assert hashlib.sha256(assembled).hexdigest() == total_sha
    log.append(f"complete: {len(assembled)} B byte-identical")

    print("\n[E2E DESKTOP CHAIN - NOT iPhone verification]")
    for line in log:
        print(f"  {line}")


def test_e2e_malicious_chunk_under_auth(e2e_env):
    """P3 malicious-input re-check under the auth layer. ENDPOINT SEMANTICS
    (W003, frozen): the chunk plan carries SIZES only (hashes unknown at
    registration), so a wrong-bytes chunk of the right SIZE is accepted
    at put-time and caught by the WHOLE-BATCH sha256 at complete - the
    acknowledged hash never matches. This test pins that real contract:
    tampered bytes can never reach a completed state."""
    data = b"K" * 500_000
    total_sha = hashlib.sha256(data).hexdigest()
    r = client.post("/upload/experimental/batches",
                    json={"total_bytes": len(data), "chunk_size": 256 * 1024,
                          "total_sha256": total_sha},
                    headers=e2e_env.hdr)
    bid = r.json()["batch_id"]
    chunks = [data[:256 * 1024], data[256 * 1024:]]

    bad = bytearray(chunks[0]); bad[0] ^= 0xFF
    rr = client.put(f"/upload/experimental/batches/{bid}/chunks/0",
                    content=bytes(bad),
                    headers={**e2e_env.hdr, "Content-Length": str(len(bad))})
    assert rr.status_code == 200               # size matches plan -> stored
    client.put(f"/upload/experimental/batches/{bid}/chunks/1",
               content=chunks[1],
               headers={**e2e_env.hdr, "Content-Length": str(len(chunks[1]))})
    comp = client.post(f"/upload/experimental/batches/{bid}/complete",
                       headers=e2e_env.hdr)
    # whole-batch check FAILS: the tampered batch can never complete
    assert comp.status_code == 409
    assert "mismatch" in comp.json()["detail"]

    # the honest retry: re-put chunk 0 with correct bytes (same slot) -
    # duplicate-with-DIFFERENT-bytes is a 409, so the slot keeps bad
    # bytes; the DOCUMENTED recovery is a fresh batch. Pin that too:
    rr2 = client.put(f"/upload/experimental/batches/{bid}/chunks/0",
                     content=chunks[0],
                     headers={**e2e_env.hdr, "Content-Length": str(len(chunks[0]))})
    assert rr2.status_code == 409              # same slot, different bytes

    # fresh batch with honest bytes completes fine
    r2 = client.post("/upload/experimental/batches",
                     json={"total_bytes": len(data), "chunk_size": 256 * 1024,
                           "total_sha256": total_sha},
                     headers=e2e_env.hdr)
    bid2 = r2.json()["batch_id"]
    for i, c in enumerate(chunks):
        client.put(f"/upload/experimental/batches/{bid2}/chunks/{i}",
                   content=c,
                   headers={**e2e_env.hdr, "Content-Length": str(len(c))})
    comp2 = client.post(f"/upload/experimental/batches/{bid2}/complete",
                        headers=e2e_env.hdr)
    assert comp2.status_code == 200 and comp2.json()["total_sha256"] == total_sha
