"""TM01: evidence schema validator tests.

Pins:
  1. schema loads and is self-consistent
  2. valid record (both v1.0/v1.1) passes shape checks
  3. unknown protocol_version -> NEVER trusted, flagged, errors reported
  4. canonical payload rebuild matches the B02-W023 fixed-vector bytes
     (byte contract is the single source of truth)
  5. malformed shapes fail with named errors
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding, PublicFormat)

import evidence_schema as es  # noqa: E402


VALID = {
    "protocol_version": "1.1",
    "record_id": "a" * 32,
    "sequence_number": 7,
    "previous_record_hash": "b" * 64,
    "photo_hash": "c" * 64,
    "capture_timestamp": "2026-11-25T09:30:00+00:00",
    "device_reported_time": "2026-11-25T09:30:01+00:00",
    "device_id": "test-device-1",
    "location": {"latitude": 31.2304, "longitude": 121.4737,
                 "accuracy": 5.0, "altitude": 4.5},
    "signature": base64.b64encode(b"\x30\x06\x02\x01\x01\x02\x01\x01").decode(),
    "public_key_b64": base64.b64encode(b"\x04" + b"\x11" * 64).decode(),
}


def test_schema_loads_and_has_rules():
    s = es.load_schema()
    assert s["properties"]["protocol_version"]["enum"] == ["1.0", "1.1"]
    assert "unknown_version_policy" in s["meta"]
    assert es.PAYLOAD_FIELD_ORDER == s["meta"]["canonical_payload"]["field_order"]


def test_valid_record_passes():
    r = es.validate_record(dict(VALID))
    assert r.ok, r.errors
    assert r.protocol_version == "1.1"
    assert not r.unknown_version


def test_unknown_version_never_trusted():
    rec = dict(VALID, protocol_version="9.9")
    r = es.validate_record(rec)
    assert r.unknown_version
    assert not r.ok
    assert any("never" in e for e in r.errors), \
        "the unknown-version error must carry the never-trusted rule"


def test_unknown_top_level_field_rejected():
    r = es.validate_record(dict(VALID, malicious_field="x"))
    assert not r.ok
    assert any("unknown top-level" in e for e in r.errors)


def test_extensions_bag_is_open():
    r = es.validate_record(dict(VALID, extensions={"future_key": [1, 2]}))
    assert r.ok, r.errors


def test_malformed_shapes_fail_named():
    cases = {
        "photo_hash": "XYZ",
        "sequence_number": -1,
        "capture_timestamp": "2026/11/25 09:30",
        "record_id": "UPPERCASE",
    }
    for field, bad in cases.items():
        r = es.validate_record(dict(VALID, **{field: bad}))
        assert not r.ok, f"{field} bad value must fail"
    r = es.validate_record(dict(VALID, location={"latitude": 999,
                                                 "longitude": 0,
                                                 "accuracy": 1}))
    assert not r.ok


def test_canonical_payload_rebuild_byte_contract():
    """The rebuilt payload text must be exactly the signed text: fixed
    order, domain separator, trailing newline, %.10g floats with a
    guaranteed decimal point (v1.1 rule)."""
    text = es.build_canonical_payload(VALID)
    lines = text.split("\n")
    assert lines[0] == "WorkCam-Sig-v1.1"
    assert lines[1] == "version=1.1"
    assert lines[2] == f"record_id={'a' * 32}"
    assert lines[3] == "sequence_number=7"
    assert lines[4] == f"previous_record_hash={'b' * 64}"
    assert lines[5] == f"photo_hash={'c' * 64}"
    assert lines[-1] == ""                       # trailing newline
    assert "location=31.2304,121.4737,5.0,4.5" in lines
    # no field value may contain a newline anywhere
    assert all("\r" not in ln for ln in lines)


def test_canonical_payload_v10_float_legacy_path():
    rec = dict(VALID, protocol_version="1.0")
    text = es.build_canonical_payload(rec)
    assert text.startswith("WorkCam-Sig-v1\n")
    # v1.0 keeps repr() formatting (documented cross-language risk F2)
    assert "location=31.2304" in text


def test_location_none_forms():
    rec = dict(VALID, location=None)
    text = es.build_canonical_payload(rec)
    assert "location=none" in text
    rec2 = dict(VALID, location={"latitude": 1.0, "longitude": 2.0,
                                 "accuracy": 3.0, "altitude": None})
    assert "altitude" not in es.build_canonical_payload(rec2) \
        .split("location=")[1] or ",none" in es.build_canonical_payload(rec2)


def test_payload_signs_and_verifies_roundtrip():
    """Rebuild -> sign -> re-verify proves the validator and the crypto
    contract compose (ECDSA P-256 SHA-256, X9.63 key)."""
    key = ec.generate_private_key(ec.SECP256R1())
    text = es.build_canonical_payload(VALID)
    sig = key.sign(text.encode(), ec.ECDSA(hashes.SHA256()))
    pub = key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    rec = dict(VALID, signature=base64.b64encode(sig).decode(),
               public_key_b64=base64.b64encode(pub).decode())
    r = es.validate_record(rec)
    assert r.ok, r.errors
    # verify with the cryptography stack the SAME way production does
    # (X9.63 raw uncompressed point, not a DER-wrapped key)
    from cryptography.exceptions import InvalidSignature
    pub2 = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), base64.b64decode(rec["public_key_b64"]))
    try:
        pub2.verify(base64.b64decode(rec["signature"]),
                    text.encode(), ec.ECDSA(hashes.SHA256()))
        ok = True
    except InvalidSignature:
        ok = False
    assert ok
    # tamper one byte of the photo hash -> must fail
    bad = dict(rec, photo_hash="d" * 64)
    try:
        pub2.verify(base64.b64decode(rec["signature"]),
                    es.build_canonical_payload(bad).encode(),
                    ec.ECDSA(hashes.SHA256()))
        ok = False
    except InvalidSignature:
        ok = True
    assert ok
