"""WC-W005-R1 P0 - Secure Enclave signature check (receive-only) tests.

Drives the real CLI as a subprocess for every scenario:
  - valid evidence (both modes) -> exit 0
  - tampered payload / swapped key / tampered photo / tampered signature
    -> exit 1 with the specific FAIL step
  - private-key fields in the evidence -> exit 2 (input refused)
  - missing fields / missing photo -> exit 2

The evidence payloads are v1.1 canonical manifests signed with SOFTWARE
test keys - the exact shape a Mac SE export will produce, minus the
hardware. No test may claim SE verification.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)

from app.models.capture import SignatureManifest
from app.services.serialization import canonical_payload

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "tools/tech-lab/validation/se_signature_check.py"


def _make_evidence(tmp_path: Path, mode: str = "software_test_key",
                   key: ec.EllipticCurvePrivateKey | None = None
                   ) -> tuple[Path, dict]:
    """Build one complete evidence set with a SOFTWARE test key."""
    key = key or ec.generate_private_key(ec.SECP256R1())
    photo = bytes((i * 37) % 256 for i in range(300_000))     # 'JPEG-ish'
    (tmp_path / "IMG_0001.jpg").write_bytes(photo)

    manifest = SignatureManifest(
        version="1.1", record_id=str(uuid.uuid4()), sequence_number=0,
        previous_record_hash=None,
        photo_hash=hashlib.sha256(photo).hexdigest(),
        capture_timestamp=datetime.now(timezone.utc).isoformat(),
        device_reported_time=datetime.now(timezone.utc).isoformat(),
        device_id="iPhone15,3-JD", location=None,
        public_key="", signature="",
    )
    payload = canonical_payload(manifest)
    sig = key.sign(payload, ec.ECDSA(hashes.SHA256()))
    pub_b64 = base64.b64encode(key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()

    ev = {
        "mode": mode,
        "photo_path": str(tmp_path / "IMG_0001.jpg"),
        "photo_sha256": hashlib.sha256(photo).hexdigest(),
        "public_key_b64": pub_b64,
        "signature_b64": base64.b64encode(sig).decode(),
        "payload_hex": payload.hex(),
        "key_id": "com.workcam.se.v1",
        "device_id": "iPhone15,3-JD",
        "claims": {"se_origin": mode == "se_direct"},
    }
    ev_path = tmp_path / "evidence.json"
    ev_path.write_text(json.dumps(ev), encoding="utf-8")
    return ev_path, ev


def _run(evidence: Path) -> subprocess.CompletedProcess:
    import os as _os
    return subprocess.run(
        [sys.executable, str(CLI), str(evidence)],
        capture_output=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT),
        env={**_os.environ, "PYTHONIOENCODING": "utf-8"})


# ------------------------------------------------------------------ valid


def test_valid_software_test_key_evidence(tmp_path):
    ev, _ = _make_evidence(tmp_path, mode="software_test_key")
    proc = _run(ev)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SOFTWARE_TEST_KEY" in proc.stdout          # the loud marker
    assert "photo sha256 match" in proc.stdout
    assert "ECDSA-SHA256 signature over payload" in proc.stdout
    assert "[PASS]" in proc.stdout


def test_valid_se_direct_mode_claims_without_hardware(tmp_path):
    """se_direct mode: the tool VERIFIES THE MATH and prints the SE
    disclaimer - it never claims hardware verification."""
    ev, _ = _make_evidence(tmp_path, mode="se_direct")
    proc = _run(ev)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "不做硬件验证" in proc.stdout                 # honesty line
    assert "签名数学有效 ≠" in proc.stdout


# --------------------------------------------------------------- negatives


def test_tampered_payload_rejected(tmp_path):
    ev, data = _make_evidence(tmp_path)
    payload = bytearray(bytes.fromhex(data["payload_hex"]))
    payload[10] ^= 0x01                                  # one byte changed
    data["payload_hex"] = bytes(payload).hex()
    ev.write_text(json.dumps(data), encoding="utf-8")
    proc = _run(ev)
    assert proc.returncode == 1
    assert "does not verify over the provided payload" in proc.stdout


def test_swapped_public_key_rejected(tmp_path):
    """A DIFFERENT key's public key cannot verify the signature."""
    ev, data = _make_evidence(tmp_path)
    other = ec.generate_private_key(ec.SECP256R1())
    data["public_key_b64"] = base64.b64encode(other.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    ev.write_text(json.dumps(data), encoding="utf-8")
    proc = _run(ev)
    assert proc.returncode == 1
    assert "does not verify" in proc.stdout


def test_tampered_photo_rejected(tmp_path):
    ev, data = _make_evidence(tmp_path)
    photo_path = Path(data["photo_path"])
    raw = bytearray(photo_path.read_bytes())
    raw[-1] ^= 0x55
    photo_path.write_bytes(bytes(raw))
    proc = _run(ev)
    assert proc.returncode == 1
    assert "[FAIL] photo sha256 match" in proc.stdout


def test_tampered_signature_rejected(tmp_path):
    ev, data = _make_evidence(tmp_path)
    sig = bytearray(base64.b64decode(data["signature_b64"]))
    sig[-1] ^= 0x01
    data["signature_b64"] = base64.b64encode(bytes(sig)).decode()
    ev.write_text(json.dumps(data), encoding="utf-8")
    proc = _run(ev)
    assert proc.returncode == 1
    assert "does not verify" in proc.stdout or "[FAIL]" in proc.stdout


def test_garbage_signature_der_rejected(tmp_path):
    ev, data = _make_evidence(tmp_path)
    data["signature_b64"] = base64.b64encode(b"\x31\x06\x02\x01\x01").decode()
    ev.write_text(json.dumps(data), encoding="utf-8")
    proc = _run(ev)
    assert proc.returncode == 1
    assert "[FAIL] signature DER structure" in proc.stdout


# ------------------------------------------------------- input refusals


def test_private_key_field_refused(tmp_path):
    """THE RED LINE: this tool never accepts private keys - an evidence
    file carrying one is rejected as INPUT (exit 2), before any
    verification runs."""
    ev, data = _make_evidence(tmp_path)
    data["private_key"] = (
        "-----BEGIN EC PRIVATE KEY----- (should never have been here)")
    ev.write_text(json.dumps(data), encoding="utf-8")
    proc = _run(ev)
    assert proc.returncode == 2
    assert "FORBIDDEN" in proc.stdout
    assert "private keys" in proc.stdout


def test_missing_field_rejected(tmp_path):
    ev, data = _make_evidence(tmp_path)
    del data["signature_b64"]
    ev.write_text(json.dumps(data), encoding="utf-8")
    proc = _run(ev)
    assert proc.returncode == 2
    assert "missing required field" in proc.stdout


def test_missing_photo_rejected(tmp_path):
    ev, data = _make_evidence(tmp_path)
    Path(data["photo_path"]).unlink()
    proc = _run(ev)
    assert proc.returncode == 2
    assert "photo file not found" in proc.stdout


def test_no_test_may_claim_se_hardware_verification():
    """Red-line meta check: the CLI source must contain the honesty
    disclaimer and must never print 'SE verified'."""
    src = CLI.read_text(encoding="utf-8")
    assert "不做硬件验证" in src
    assert "签名数学有效 ≠" in src
    assert "FORBIDDEN" in src and "private keys" in src
    # the tool must refuse forbidden fields at INPUT level
    assert "return fail_input" in src
