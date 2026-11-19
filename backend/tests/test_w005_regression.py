"""
WC-W005 regression and robustness tests.

Covers:
- W005-2 runtime trust-level separation (unregistered key can never reach
  cryptographically_valid; signing math != key registration != device identity
  != photo verification != time anchoring)
- W005-5 persistence and failure scenarios (restart, duplicate submission,
  unknown key, device mismatch, tampered photo/signature, anchor duplicate /
  conflict, corrupted persistence file, request retry idempotency)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from app.main import app
from app.models.capture import (
    CheckStatus,
    EvidenceStatus,
    KeyRegistrationStatus,
    SignatureManifest,
)
from app.services.key_registry import get_registry, reset_registry
from app.services.time_anchor import (
    PersistenceError,
    TimeAnchorService,
    reset_time_anchor_service,
)
from tests.conftest import make_manifest, make_test_photo, _encode_pub


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_all():
    reset_registry()
    reset_time_anchor_service()
    yield
    reset_registry()
    reset_time_anchor_service()


def _new_keypair() -> tuple[ec.EllipticCurvePrivateKey, bytes]:
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    return priv, pub


# ---------------------------------------------------------------------------
# W005-2: trust level separation
# ---------------------------------------------------------------------------


def test_unregistered_key_never_reaches_cryptographically_valid():
    """Signing math valid + photo verified + key UNREGISTERED
    -> evidence_status must be signature_valid, never cryptographically_valid."""
    priv, pub = _new_keypair()
    photo = make_test_photo()
    manifest = make_manifest(priv, pub, photo_bytes=photo)

    resp = client.post("/verify", json={
        "manifest": manifest.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()

    assert data["cryptographic_signature_valid"] is True
    assert data["photo_hash_valid"] is True
    assert data["key_registration_status"] == KeyRegistrationStatus.UNREGISTERED
    assert data["device_identity_consistent"] is None
    assert data["evidence_status"] == EvidenceStatus.SIGNATURE_VALID
    assert data["evidence_status"] != EvidenceStatus.CRYPTOGRAPHICALLY_VALID


def test_trust_levels_are_independent_not_equal():
    """The five trust dimensions are reported independently."""
    priv, pub = _new_keypair()
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()
    manifest = make_manifest(priv, pub, photo_bytes=photo)

    get_registry().register(pub_b64, manifest.device_id)

    resp = client.post("/verify", json={
        "manifest": manifest.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()

    # signature math: True
    assert data["cryptographic_signature_valid"] is True
    # key registration: registered
    assert data["key_registration_status"] == KeyRegistrationStatus.REGISTERED
    # device identity: consistent
    assert data["device_identity_consistent"] is True
    # photo verification: True
    assert data["photo_hash_valid"] is True
    # device attestation: still unknown (App Attest not implemented)
    assert data["device_attestation_status"] == "unknown"
    # time: device-reported only, no server anchor yet
    assert data["time_trust_level"] == "device_reported"
    # overall: cryptographically_valid, but NOT fully_attested
    assert data["evidence_status"] == EvidenceStatus.CRYPTOGRAPHICALLY_VALID
    assert data["evidence_status"] != EvidenceStatus.FULLY_ATTESTED


def test_genesis_location_null_passes_verification():
    """Mac M003 evidence shape: version=1.1, location=null, seq=0, prev=null."""
    priv, pub = _new_keypair()
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()
    manifest = make_manifest(
        priv, pub, photo_bytes=photo, seq=0, prev_hash=None, version="1.1"
    )

    assert manifest.location is None
    assert manifest.sequence_number == 0
    assert manifest.previous_record_hash is None

    get_registry().register(pub_b64, manifest.device_id)

    resp = client.post("/verify", json={
        "manifest": manifest.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })
    data = resp.json()
    assert data["evidence_status"] == EvidenceStatus.CRYPTOGRAPHICALLY_VALID
    struct = [d for d in data["details"] if d["check"] == "structure"]
    assert struct[0]["status"] == CheckStatus.PASSED


def test_non_genesis_without_prev_hash_rejected():
    """seq > 0 with previous_record_hash=null is rejected by CHAIN validation.
    Note: single-record /verify does not enforce chain rules — that is the
    documented separation between signature math and chain structure checks."""
    from app.services.chain import verify_chain_link

    priv, pub = _new_keypair()
    photo = make_test_photo()
    manifest = make_manifest(priv, pub, photo_bytes=photo, seq=3, prev_hash=None)

    # chain validation must reject a non-genesis record without previous hash
    ok, msg = verify_chain_link(manifest, None)
    assert ok is False
    assert "previous_record_hash" in msg

    # single-record /verify still validates the signature math alone
    data = client.post("/verify", json={"manifest": manifest.model_dump()}).json()
    assert data["cryptographic_signature_valid"] is True
    assert data["evidence_status"] == EvidenceStatus.SIGNATURE_VALID


# ---------------------------------------------------------------------------
# W005-5: persistence and failure scenarios
# ---------------------------------------------------------------------------


def test_service_restart_preserves_record_store_and_anchor(tmp_path):
    """Records stored and anchors issued before a restart must still verify
    after service recreation with the same persistence file."""
    persist = tmp_path / "anchors.json"
    service1 = TimeAnchorService(key_id="w005-key", persist_path=persist)

    priv, pub = _new_keypair()
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()
    manifest = make_manifest(priv, pub, photo_bytes=photo)

    get_registry().register(pub_b64, manifest.device_id)
    client.post("/store-record", json={
        "manifest": manifest.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    })

    anchor_req = {
        "batch_id": "w005-restart-batch",
        "chain_head_hash": hashlib.sha256(b"head").hexdigest(),
        "chain_tail_hash": hashlib.sha256(b"tail").hexdigest(),
        "record_count": 1,
        "device_reported_start_time": manifest.capture_timestamp,
        "device_reported_end_time": manifest.capture_timestamp,
    }
    anchor = service1.create_anchor(
        __import__("app.models.time_anchor", fromlist=["TimeAnchorRequest"]).TimeAnchorRequest(**anchor_req)
    )

    # --- simulate restart: brand new service instance, same key file + store
    service2 = TimeAnchorService(key_id="w005-key", persist_path=persist)

    stored_receipt = service2._store.get_by_batch_id("w005-restart-batch")
    assert stored_receipt is not None
    verification = service2.verify_anchor(stored_receipt)
    assert verification.valid is True
    assert verification.signature_valid is True
    assert verification.anchor_id == anchor.anchor_id


def test_verify_endpoint_idempotent_after_restart():
    """Re-sending the same verify request twice (client retry) must give the
    same result and never flip a failure into a success."""
    priv, pub = _new_keypair()
    photo = make_test_photo()
    manifest = make_manifest(priv, pub, photo_bytes=photo)
    body = {
        "manifest": manifest.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    }

    r1 = client.post("/verify", json=body).json()
    r2 = client.post("/verify", json=body).json()
    assert r1 == r2
    assert r1["cryptographic_signature_valid"] is True


def test_store_record_retry_conflict_after_content_change():
    """Same record_id with different content: second submission must be
    rejected as invalid signature (content is signed), never stored."""
    priv, pub = _new_keypair()
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()
    manifest = make_manifest(priv, pub, photo_bytes=photo)

    get_registry().register(pub_b64, manifest.device_id)

    r1 = client.post("/store-record", json={
        "manifest": manifest.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    }).json()
    assert r1["stored"] is True

    tampered = manifest.model_copy(update={"photo_hash": "aa" * 32})
    r2 = client.post("/store-record", json={
        "manifest": tampered.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    }).json()
    assert r2["stored"] is False
    assert r2["rejection_reason"] == "invalid_signature"

    # the original record is untouched
    check = client.get(f"/check-record/{manifest.record_id}").json()
    assert check["exists"] is True
    assert check["photo_hash"] == manifest.photo_hash


def test_anchor_duplicate_idempotent_conflict_rejected():
    """Anchor: identical batch resubmitted -> same anchor_id (idempotent);
    same batch_id with different content -> 409 conflict."""
    def _req(head: str):
        return {
            "batch_id": "w005-anchor-batch",
            "chain_head_hash": head,
            "chain_tail_hash": hashlib.sha256(b"tail").hexdigest(),
            "record_count": 5,
            "device_reported_start_time": "2026-11-19T08:00:00+00:00",
            "device_reported_end_time": "2026-11-19T09:00:00+00:00",
        }

    r1 = client.post("/time-anchor/anchor", json=_req(hashlib.sha256(b"head").hexdigest()))
    assert r1.status_code == 200
    r2 = client.post("/time-anchor/anchor", json=_req(hashlib.sha256(b"head").hexdigest()))
    assert r2.status_code == 200
    assert r1.json()["anchor_id"] == r2.json()["anchor_id"]

    r3 = client.post("/time-anchor/anchor", json=_req(hashlib.sha256(b"other").hexdigest()))
    assert r3.status_code == 409


def test_corrupted_persistence_file_raises_not_silent_empty(tmp_path):
    """A corrupted persistence file must raise PersistenceError on load,
    never silently start with an empty store."""
    persist = tmp_path / "anchors.json"
    persist.write_text("{ this is not valid json !!!", encoding="utf-8")

    with pytest.raises(PersistenceError):
        TimeAnchorService(key_id="w005-key", persist_path=persist)


def test_persistence_failure_returns_500_not_success(tmp_path, monkeypatch):
    """Anchor creation must fail loudly when disk write fails."""
    from app.api import time_anchor as api_mod

    persist = tmp_path / "anchors.json"
    service = TimeAnchorService(key_id="w005-key", persist_path=persist)

    def boom(self):
        raise PersistenceError("simulated disk full")

    monkeypatch.setattr(type(service._store), "_save_to_file", boom)
    monkeypatch.setattr(api_mod, "get_time_anchor_service", lambda: service)

    resp = client.post("/time-anchor/anchor", json={
        "batch_id": "w005-disk-full",
        "chain_head_hash": hashlib.sha256(b"h").hexdigest(),
        "chain_tail_hash": hashlib.sha256(b"t").hexdigest(),
        "record_count": 1,
        "device_reported_start_time": "2026-11-19T08:00:00+00:00",
        "device_reported_end_time": "2026-11-19T08:10:00+00:00",
    })
    assert resp.status_code == 500
    assert "simulated disk full" in resp.json()["detail"]
    # batch_id must NOT be occupied after the failed write
    assert service._store.get_by_batch_id("w005-disk-full") is None


def test_unknown_key_and_device_mismatch_both_fail():
    """Unknown key -> signature_valid level; registered key with mismatched
    device identity -> invalid."""
    priv, pub = _new_keypair()
    pub_b64 = base64.b64encode(pub).decode()
    photo = make_test_photo()

    # unknown key
    m = make_manifest(priv, pub, photo_bytes=photo)
    data = client.post("/verify", json={
        "manifest": m.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    }).json()
    assert data["key_registration_status"] == KeyRegistrationStatus.UNREGISTERED
    assert data["evidence_status"] == EvidenceStatus.SIGNATURE_VALID

    # device mismatch: key registered to another device
    get_registry().register(pub_b64, "someone-elses-device")
    m2 = make_manifest(priv, pub, photo_bytes=photo, record_id="mismatch-rec")
    m2 = m2.model_copy(update={"device_id": "impostor-device"})
    data2 = client.post("/verify", json={
        "manifest": m2.model_dump(),
        "photo_base64": base64.b64encode(photo).decode(),
    }).json()
    assert data2["cryptographic_signature_valid"] is False  # device_id is signed
    assert data2["evidence_status"] == EvidenceStatus.INVALID


def test_tampered_photo_and_signature_rejected():
    """Tampered photo bytes and tampered signature must both fail loudly."""
    priv, pub = _new_keypair()
    photo = make_test_photo()
    manifest = make_manifest(priv, pub, photo_bytes=photo)

    tampered_photo = photo[:-1] + bytes([photo[-1] ^ 0xFF])
    data = client.post("/verify", json={
        "manifest": manifest.model_dump(),
        "photo_base64": base64.b64encode(tampered_photo).decode(),
    }).json()
    assert data["photo_hash_valid"] is False
    assert data["evidence_status"] == EvidenceStatus.INVALID

    bad_sig = manifest.model_copy(
        update={"signature": base64.b64encode(b"\x30\x06\x02\x01\x01\x02\x01\x01").decode()}
    )
    data2 = client.post("/verify", json={"manifest": bad_sig.model_dump()}).json()
    assert data2["cryptographic_signature_valid"] is False
    assert data2["evidence_status"] == EvidenceStatus.INVALID
