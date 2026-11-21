"""T18 - standalone offline verifier tests.

Runs the real CLI as a subprocess (repo-root invocation, as documented)
against: a valid package, tampered packages, and an anchor receipt.
Also pins anchor sign-data reconstruction against the REAL service.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, PrivateFormat, NoEncryption,
)

from app.services.time_anchor import TimeAnchorService
from app.models.time_anchor import TimeAnchorRequest
from techlab.anchor_verifier import (
    reconstruct_anchor_sign_data,
    verify_anchor_receipt_standalone,
)
from techlab.evidence_package import build_package
from tests.conftest import make_manifest, _encode_pub

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "tools" / "tech-lab" / "validation" / "evidence_verifier.py"


def _build_valid_package(tmp_path: Path) -> Path:
    key = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(key.public_key())
    photo = b"\x42" * 512
    manifest = make_manifest(key, pub, photo_bytes=photo)
    pkg_dir = tmp_path / "pkg"
    build_package(
        pkg_dir,
        [{"manifest": manifest.model_dump(), "photo_bytes": photo, "location_meta": None}],
        device_id="lab-device",
        created_at="2026-11-19T10:00:00+00:00",
    )
    return pkg_dir


def _run_cli(pkg_dir: Path, *extra) -> subprocess.CompletedProcess:
    """Run the CLI as a real subprocess, repo-root cwd.

    Cross-platform note (W-09): force UTF-8 on BOTH sides - the child gets
    PYTHONIOENCODING=utf-8, the parent decodes with encoding='utf-8'.
    Without this, Windows pipe defaults (GBK) crash the reader thread and
    silently yield stdout=None."""
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(CLI), str(pkg_dir), *extra],
        capture_output=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), env=env,
    )


def test_cli_valid_package_exit_zero(tmp_path):
    pkg = _build_valid_package(tmp_path)
    out = _run_cli(pkg, "--json", str(tmp_path / "r.json"))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "1 通过" in out.stdout
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["overall"] is True
    assert report["records"][0]["status"].startswith("pass")


def test_cli_tampered_photo_exit_one(tmp_path):
    pkg = _build_valid_package(tmp_path)
    photo_files = list((pkg / "photos").glob("*.jpg"))
    photo_files[0].write_bytes(b"\x99" * 512)
    out = _run_cli(pkg)
    assert out.returncode == 1
    assert "1 失败" in out.stdout
    assert "photo_hash" in out.stdout


def test_cli_missing_directory_exit_one(tmp_path):
    out = _run_cli(tmp_path / "nope")
    assert out.returncode == 1
    assert "does not exist" in out.stdout


def test_cli_missing_manifest_is_cannot_verify_not_pass(tmp_path):
    pkg = _build_valid_package(tmp_path)
    for m in (pkg / "manifests").glob("*.json"):
        m.unlink()
    out = _run_cli(pkg)
    assert out.returncode == 1
    assert "无法验证" in out.stdout      # cannot_verify counted honestly
    assert "通过 | 0 失败 | 1 无法验证" in out.stdout.replace("\n", " ") or \
           "1 无法验证" in out.stdout


def test_cli_unregistered_key_reports_identity_warning(tmp_path):
    pkg = _build_valid_package(tmp_path)
    out = _run_cli(pkg)
    # no trust registry on the CLI: identity must be reported as NOT
    # CHECKED - a bare PASS would overstate what was actually verified
    assert "identity not checked" in out.stdout
    assert "NOT proof that the capture scene was genuine" in out.stdout


# ---------------------------------------------------------------------------
# anchor receipt path
# ---------------------------------------------------------------------------


def _issue_receipt():
    service = TimeAnchorService(key_id="t18-key")
    request = TimeAnchorRequest(
        batch_id=f"batch-{uuid.uuid4().hex[:8]}",
        chain_head_hash="a" * 64, chain_tail_hash="b" * 64, record_count=1,
        device_reported_start_time="2026-11-19T10:00:00+00:00",
        device_reported_end_time="2026-11-19T10:01:00+00:00",
    )
    response = service.create_anchor(request)
    receipt = service._store.get_by_batch_id(request.batch_id).model_dump()
    pem = service.get_public_key_pem()
    return receipt, pem, service


def test_sign_data_reconstruction_matches_real_service():
    """BYTE-FOR-BYTE pin: standalone reconstruction must equal the real
    service's _create_sign_data for the same receipt."""
    from app.models.time_anchor import VerificationStatus

    receipt, _pem, service = _issue_receipt()
    standalone = reconstruct_anchor_sign_data(receipt)
    original = service._create_sign_data(
        anchor_id=receipt["anchor_id"], key_id=receipt["key_id"],
        batch_id=receipt["batch_id"],
        server_received_at=receipt["server_received_at"],
        challenge=receipt["challenge"],
        chain_head_hash=receipt["chain_head_hash"],
        chain_tail_hash=receipt["chain_tail_hash"],
        record_count=receipt["record_count"],
        verification_status=VerificationStatus(receipt["verification_status"]),
    )
    assert standalone == original


def test_standalone_anchor_verification_valid(tmp_path):
    receipt, pem, _ = _issue_receipt()
    outcome = verify_anchor_receipt_standalone(receipt, pem, key_id_expected="t18-key")
    assert outcome["valid"] is True
    assert outcome["signature_valid"] is True


def test_standalone_anchor_verification_wrong_key_id_rejected(tmp_path):
    receipt, pem, _ = _issue_receipt()
    outcome = verify_anchor_receipt_standalone(receipt, pem, key_id_expected="OTHER-key")
    assert outcome["valid"] is False
    assert "unknown_key_id" in outcome["reason"]


def test_standalone_anchor_verification_tampered_receipt(tmp_path):
    receipt, pem, _ = _issue_receipt()
    receipt = dict(receipt)
    receipt["record_count"] = 999            # tamper a SIGNED field
    outcome = verify_anchor_receipt_standalone(receipt, pem, key_id_expected="t18-key")
    assert outcome["valid"] is False
    assert "signature_invalid" in outcome["reason"]


def test_cli_with_anchor_receipt(tmp_path):
    """CLI end-to-end: package + anchor receipt + trusted key PEM."""
    receipt, pem, _ = _issue_receipt()
    key = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(key.public_key())
    photo = b"\x42" * 512
    manifest = make_manifest(key, pub, photo_bytes=photo)
    pkg_dir = tmp_path / "pkg_anchor"
    build_package(
        pkg_dir,
        [{"manifest": manifest.model_dump(), "photo_bytes": photo, "location_meta": None}],
        device_id="lab-device",
        created_at="2026-11-19T10:00:00+00:00",
        anchor_receipts={manifest.record_id: receipt},
    )
    pem_path = tmp_path / "server_key.pem"
    pem_path.write_text(pem, encoding="utf-8")

    out = _run_cli(pkg_dir, "--anchor-pubkey", str(pem_path), "--anchor-key-id", "t18-key")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "[anchor:" in out.stdout
    assert "PASS -" in out.stdout
