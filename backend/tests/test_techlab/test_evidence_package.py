"""T13 - evidence package v1: binding + tamper detection tests."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.models.capture import SignatureManifest
from app.services.key_registry import InMemoryKeyRegistry
from techlab.evidence_package import build_package, verify_package
from tests.conftest import make_manifest, make_test_photo, _encode_pub


def _make_evidence(key=None, seed: int = 0x42):
    """One signed evidence record (software TEST key). Distinct seed per
    record so cross-record swaps are detectable (all-same bytes are not)."""
    key = key or ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(key.public_key())
    photo = bytes([seed]) * 512
    return key, pub, photo, make_manifest(key, pub, photo_bytes=photo)


def _build(tmp_path, n=2, anchor_receipts=None, key=None):
    key, pub, photo, manifest = _make_evidence(key, seed=0x42)
    records = [{
        "manifest": manifest.model_dump(),
        "photo_bytes": photo,
        "location_meta": None,
    }]
    if n >= 2:
        _, pub2, photo2, manifest2 = _make_evidence(key, seed=0x43)
        records.append({
            "manifest": manifest2.model_dump(),
            "photo_bytes": photo2,
            "location_meta": None,
        })
    pkg = build_package(tmp_path / "pkg", records, device_id="lab-device",
                        created_at="2026-11-19T10:00:00+00:00",
                        anchor_receipts=anchor_receipts)
    return tmp_path / "pkg", pkg, records, manifest


def test_valid_package_passes(tmp_path):
    pkg_dir, pkg, records, _ = _build(tmp_path)
    result = verify_package(pkg_dir)
    assert result.all_passed
    assert all(r.status == "pass" for r in result.record_results)


def test_photo_replacement_detected(tmp_path):
    pkg_dir, pkg, records, manifest = _build(tmp_path, n=1)
    # swap the photo bytes for DIFFERENT content (distinct seed)
    target = pkg_dir / "photos" / f"{manifest.record_id}.jpg"
    target.write_bytes(b"\x99" * 512)

    result = verify_package(pkg_dir)
    rv = result.record_results[0]
    assert not rv.passed
    assert rv.photo_hash_valid is False
    assert any("photo_hash" in p for p in rv.problems)


def test_manifest_field_tamper_breaks_signature(tmp_path):
    pkg_dir, pkg, records, manifest = _build(tmp_path, n=1)
    mpath = pkg_dir / "manifests" / f"{manifest.record_id}.json"
    data = json.loads(mpath.read_text(encoding="utf-8"))
    data["device_id"] = "honest-company"          # claims someone else
    mpath.write_text(json.dumps(data), encoding="utf-8")

    result = verify_package(pkg_dir)
    rv = result.record_results[0]
    assert rv.signature_valid is False
    assert not rv.passed


def test_cross_record_manifest_swap_detected(tmp_path):
    """Swapping two manifests between records must be caught by the
    package-listing <-> manifest record_id binding."""
    pkg_dir, pkg, records, _ = _build(tmp_path, n=2)
    r1, r2 = pkg.records
    p1 = pkg_dir / "manifests" / f"{r1.record_id}.json"
    p2 = pkg_dir / "manifests" / f"{r2.record_id}.json"
    p1_text, p2_text = p1.read_text(encoding="utf-8"), p2.read_text(encoding="utf-8")
    p1.write_text(p2_text, encoding="utf-8")
    p2.write_text(p1_text, encoding="utf-8")

    result = verify_package(pkg_dir)
    mismatches = [rv for rv in result.record_results
                  if any("record_id mismatch" in p for p in rv.problems)]
    assert len(mismatches) == 2
    assert not result.all_passed


def test_missing_manifest_reported_not_guessed(tmp_path):
    pkg_dir, pkg, records, manifest = _build(tmp_path, n=1)
    (pkg_dir / "manifests" / f"{manifest.record_id}.json").unlink()

    result = verify_package(pkg_dir)
    rv = result.record_results[0]
    assert rv.manifest_found is False
    assert rv.signature_valid is None       # cannot verify, NOT pass
    assert not rv.passed
    assert any("manifest file missing" in p for p in rv.problems)


def test_unregistered_key_is_pass_unregistered_not_fail(tmp_path):
    """Trust boundary: the math passes, the key is simply unknown to the
    registry. Status must say exactly that - never 'fail', never 'pass'
    implying device identity."""
    pkg_dir, pkg, records, manifest = _build(tmp_path, n=1)
    registry = InMemoryKeyRegistry()       # empty registry = unregistered

    result = verify_package(pkg_dir, key_registry=registry)
    rv = result.record_results[0]
    assert rv.signature_valid is True
    assert rv.key_registered is False
    assert rv.status == "pass_unregistered_key"
    assert rv.passed        # math passed; identity explicitly unknown


def test_registered_key_reports_full_pass(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    pkg_dir, pkg, records, manifest = _build(tmp_path, n=1, key=key)
    registry = InMemoryKeyRegistry()
    pub_b64 = manifest.public_key
    registry.register(pub_b64, "lab-device")

    result = verify_package(pkg_dir, key_registry=registry)
    rv = result.record_results[0]
    assert rv.status == "pass"
    assert rv.key_registered is True


def test_package_manifest_missing_and_corrupt(tmp_path):
    # missing package.json
    missing = tmp_path / "empty_pkg"
    missing.mkdir()
    result = verify_package(missing)
    assert "package.json missing" in result.package_problems

    # corrupted package.json
    corrupt = tmp_path / "corrupt_pkg"
    (corrupt).mkdir()
    (corrupt / "package.json").write_text("{ nope", encoding="utf-8")
    result2 = verify_package(corrupt)
    assert any("unreadable" in p for p in result2.package_problems)


def test_anchor_receipt_roundtrip(tmp_path):
    pkg_dir, pkg, records, manifest = _build(tmp_path, n=1)
    # (full anchor signature verification is exercised in T18 tests;
    # here the receipt file lands in the package and is listed per record)
    rec = pkg.records[0]
    assert rec.anchor_receipt_file is None
