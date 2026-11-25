"""TM06: W018 CLI <-> TM07 trust-engine integration tests.

Builds REAL evidence packages (fixed test keys, real photos, real
signatures), runs the B02-W018 CLI in-process, then bridges each
record report to the TM07 ten-dimension ledger and pins:

  1. happy path: L1/L2 pass -> VERIFIED dimensions, environment stated
  2. tampered photo -> file_integrity FAILED + W018 verdict fail
  3. replaced public key (signature substitution) -> signature FAILED
  4. registry with rotated_out status -> device_registration
     UNVERIFIED (historical math keeps, current auth does not) - the
     TM03 semantics survive the offline bridge
  5. revoked key in the registry -> device_registration FAILED
  6. chain.json + checkpoint.json in the package -> chain + external
     dimensions engage (truncation against checkpoint is named)
  7. unknown protocol version -> trust ledger refuses (TM01 rule)
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"
                       / "evidence-verifier"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"
                       / "evidence_chain"))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)

import audit_chain as ac  # noqa: E402

import pytest  # noqa: E402

from trust_bridge import (load_registry_statuses,  # noqa: E402
                          record_report_to_trust)

# load verify_evidence.py as a module (it lives outside backend/)
_spec = importlib.util.spec_from_file_location(
    "verify_evidence",
    Path(__file__).resolve().parents[3] / "tools" / "evidence-verifier"
    / "verify_evidence.py")
ve = importlib.util.module_from_spec(_spec)
sys.modules["verify_evidence"] = ve
_spec.loader.exec_module(ve)


def _sign_canonical(key, text: str) -> str:
    sig = key.sign(text.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(sig).decode()


def _canonical_payload_text(photo_hash: str, device: str,
                            version: str = "1.1") -> str:
    fields = [
        f"WorkCam-Sig-v{version}",
        f"version={version}",
        "record_id=" + "e" * 32,
        "sequence_number=0",
        "previous_record_hash=none",
        f"photo_hash={photo_hash}",
        "capture_timestamp=2026-11-25T09:00:00+00:00",
        "device_reported_time=2026-11-25T09:00:01+00:00",
        f"device_id={device}",
        "location=none",
        "",
    ]
    return "\n".join(fields)


def _photo_bytes(seed: int) -> bytes:
    import io
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (96, 64), (seed % 255, 40, 90))
    ImageDraw.Draw(img).rectangle(
        [10, 10, 80, 50], fill=(seed % 200, 120, 30))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return buf.getvalue()


@pytest.fixture()
def pkg(tmp_path):
    """One valid signed package in tmp_path."""
    key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    photo = _photo_bytes(7)
    import hashlib
    photo_hash = hashlib.sha256(photo).hexdigest()
    text = _canonical_payload_text(photo_hash, "cam-device-1")
    sig = _sign_canonical(key, text)

    d = tmp_path / "pkg"
    d.mkdir()
    (d / "photos").mkdir()
    (d / "manifests").mkdir()
    (d / "photos" / "p1.jpg").write_bytes(photo)
    manifest = {
        "version": "1.1",
        "record_id": "e" * 32,
        "sequence_number": 0,
        "previous_record_hash": None,
        "photo_hash": photo_hash,
        "photo_file": "photos/p1.jpg",
        "capture_timestamp": "2026-11-25T09:00:00+00:00",
        "device_reported_time": "2026-11-25T09:00:01+00:00",
        "device_id": "cam-device-1",
        "location": None,
        "public_key": pub_b64,
        "signature": sig,
        "algorithm": "ECDSA-P256-SHA256",
    }
    (d / "manifests" / "m1.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    (d / "package.json").write_text(json.dumps({
        "package_version": "1.0",
        "created_at": "2026-11-25T10:00:00+00:00",
        "device_id": "cam-device-1",
        "records": [{
            "record_id": manifest["record_id"],
            "photo_file": "photos/p1.jpg",
            "manifest_file": "manifests/m1.json",
            "capture_timestamp": manifest["capture_timestamp"],
        }],
    }), encoding="utf-8")
    return {"dir": d, "key": key, "pub_b64": pub_b64, "photo": photo}


class _Args:
    registry = None
    anchor_pubkey = None
    anchor_key_id = None


def _run_cli(pkg_dir: Path) -> dict:
    return ve.verify_package_dir(pkg_dir, _Args(), kind="dir")


def _trust_for(pkg_dir: Path, registry_path: Path | None = None):
    result = _run_cli(pkg_dir)
    assert result["outcome"] == "pass", result.get("reason",
                                                   result)
    rr = result["records"][0]
    manifest_data = json.loads(
        (pkg_dir / "manifests" / "m1.json").read_text(encoding="utf-8"))
    statuses = (load_registry_statuses(registry_path)
                if registry_path else None)
    return record_report_to_trust(rr, pkg_dir, statuses, manifest_data)


def _dim(trust: dict, name: str) -> dict:
    return next(d for d in trust["dimensions"] if d["name"] == name)


def test_happy_path_dimensions(pkg):
    trust = _trust_for(pkg["dir"])
    assert _dim(trust, "file_integrity")["status"] == "verified"
    assert _dim(trust, "signature_validity")["status"] == "verified"
    assert _dim(trust, "device_registration")["status"] == "unknown"
    assert _dim(trust, "hardware_attestation")["status"] == "not_attested"
    assert _dim(trust, "time_source")["status"] in ("unverified",
                                                    "unknown")
    assert _dim(trust, "location_source")["status"] == "not_attested"
    assert _dim(trust, "verification_environment")["status"] == \
        "unverified"      # desktop CLI, always stated
    assert trust["verdict"] == "pass"


def test_tampered_photo_fails_file_integrity(pkg):
    (pkg["dir"] / "photos" / "p1.jpg").write_bytes(
        pkg["photo"][:-2] + b"\x00\x01")
    result = _run_cli(pkg["dir"])
    assert result["records"][0]["verdict"] == "fail"
    trust = record_report_to_trust(
        result["records"][0], pkg["dir"], None, None)
    assert _dim(trust, "file_integrity")["status"] == "failed"


def test_public_key_substitution_breaks_signature(pkg):
    """Swap the manifest's public key for the attacker's WITHOUT
    re-signing - the substitution attack must surface as L2 failure."""
    attacker = ec.generate_private_key(ec.SECP256R1())
    mfile = pkg["dir"] / "manifests" / "m1.json"
    manifest = json.loads(mfile.read_text(encoding="utf-8"))
    manifest["public_key"] = base64.b64encode(
        attacker.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    mfile.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    result = _run_cli(pkg["dir"])
    assert result["records"][0]["verdict"] == "fail"
    trust = record_report_to_trust(
        result["records"][0], pkg["dir"], None, None)
    assert _dim(trust, "signature_validity")["status"] == "failed"


def test_rotated_out_key_keeps_math_loses_authorization(pkg, tmp_path):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"keys": [{
        "public_key_b64": pkg["pub_b64"],
        "device_id": "cam-device-1",
        "status": "rotated_out",
        "last_event_at": "2026-11-24T00:00:00+00:00"}]}),
        encoding="utf-8")
    trust = _trust_for(pkg["dir"], reg)
    assert _dim(trust, "signature_validity")["status"] == "verified"
    d = _dim(trust, "device_registration")
    assert d["status"] == "unverified"
    assert "historical" in d["reason"]


def test_revoked_key_fails_registration(pkg, tmp_path):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"keys": [{
        "public_key_b64": pkg["pub_b64"],
        "device_id": "cam-device-1",
        "status": "revoked"}]}), encoding="utf-8")
    trust = _trust_for(pkg["dir"], reg)
    assert _dim(trust, "device_registration")["status"] == "failed"


def test_unknown_registry_status_is_never_active(pkg, tmp_path):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"keys": [{
        "public_key_b64": pkg["pub_b64"],
        "device_id": "cam-device-1",
        "status": "something-fishy"}]}), encoding="utf-8")
    trust = _trust_for(pkg["dir"], reg)
    assert _dim(trust, "device_registration")["status"] == "unknown"


def test_chain_and_checkpoint_dimensions_engage(pkg):
    """chain.json + checkpoint.json light up chain_integrity and
    external_timestamp; truncation against the anchor is NAMED."""
    import hashlib
    prev = "none"
    records = []
    for i in range(3):
        h = hashlib.sha256(f"r{i}-{prev}".encode()).hexdigest()
        records.append({"sequence_number": i,
                        "previous_record_hash": prev,
                        "record_hash": h})
        prev = h
    (pkg["dir"] / "chain.json").write_text(
        json.dumps({"records": records}), encoding="utf-8")

    key = ec.generate_private_key(ec.SECP256R1())
    (pkg["dir"] / "checkpoint-key.pem").write_bytes(
        key.public_key().public_bytes(Encoding.PEM,
                                      PublicFormat.SubjectPublicKeyInfo))
    cp = ac.build_checkpoint(
        records[-1]["record_hash"], len(records),
        lambda b: key.sign(b, ec.ECDSA(hashes.SHA256())))
    (pkg["dir"] / "checkpoint.json").write_text(
        json.dumps(cp), encoding="utf-8")

    trust = _trust_for(pkg["dir"])
    assert _dim(trust, "chain_integrity")["status"] == "verified"
    assert _dim(trust, "external_timestamp")["status"] == "verified"

    # attacker truncates the chain but the checkpoint still anchors 3
    truncated = records[:2]
    (pkg["dir"] / "chain.json").write_text(
        json.dumps({"records": truncated}), encoding="utf-8")
    trust2 = _trust_for(pkg["dir"])
    d = _dim(trust2, "external_timestamp")
    assert d["status"] == "failed"
    assert "TRUNCATION DETECTED" in d["reason"]


def test_unknown_protocol_version_refused(pkg):
    mfile = pkg["dir"] / "manifests" / "m1.json"
    manifest = json.loads(mfile.read_text(encoding="utf-8"))
    manifest["version"] = "9.9"
    mfile.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    result = _run_cli(pkg["dir"])
    assert result["records"][0]["verdict"] in ("fail", "cannot_verify")
    # and the TM01 schema validator states the never-trusted rule
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                            / "tools"))
    import evidence_schema as es
    rep = es.validate_record(dict(
        protocol_version="9.9", record_id="a" * 32,
        sequence_number=1, photo_hash="b" * 64,
        capture_timestamp="2026-11-25T09:00:00+00:00",
        device_reported_time="2026-11-25T09:00:00+00:00",
        device_id="x", signature=manifest["signature"],
        public_key_b64=manifest["public_key"]))
    assert rep.unknown_version and not rep.ok
