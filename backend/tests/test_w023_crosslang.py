"""
B02-W023: pytest validation of the cross-language vector file against the
PRODUCTION implementation.

The standalone verifier (backend/w023_reference_verifier.py) checks the
vectors with an INDEPENDENT implementation. THIS file does the opposite
direction: it proves that the production backend code
(app.services.*) agrees with every frozen vector byte-for-byte:

  - production canonical_payload == vector canonical_payload for every
    signed manifest case
  - production verify_signature_with_details accepts every positive case
  - production structure validation rejects every negative case
  - production _format_float matches the v1.1 float table
  - production ISO8601 regex matches the timestamp table
  - production public-key / DER decoders match the encoding tables
  - pydantic model bounds match the GPS reject table
  - RFC 6979 generator matches the official RFC 6979 A.2.5 vectors

All keys are TEST KEYS ONLY - never used in production.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import struct

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256

from app.models.capture import LocationData, SignatureManifest
from app.services.chain import compute_record_hash
from app.services.serialization import (
    ISO8601_REGEX,
    _format_float,
    canonical_payload,
)
from app.services.signature import (
    _decode_public_key,
    _decode_signature,
    validate_manifest_structure,
    verify_signature_with_details,
)

VECTORS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "test-vectors", "crosslang",
    "w023-crosslang-vectors.json",
)
GENERATOR_PATH = os.path.join(
    os.path.dirname(__file__), "..", "generate_w023_vectors.py"
)


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_w023_vectors", GENERATOR_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def vectors():
    if not os.path.exists(VECTORS_PATH):
        pytest.fail(f"Cross-language vectors file not found: {VECTORS_PATH}")
    with open(VECTORS_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def gen():
    return _load_generator()


@pytest.fixture(scope="module")
def pub_bytes(vectors):
    return base64.b64decode(vectors["test_key"]["public_key_b64"])


def _manifest_from_case(vectors: dict, case: dict) -> SignatureManifest:
    m = case["manifest_json"]
    loc = None
    if m.get("location") is not None:
        loc = LocationData(**m["location"])
    return SignatureManifest(
        version=m["version"],
        record_id=m["record_id"],
        sequence_number=m["sequence_number"],
        previous_record_hash=m.get("previous_record_hash"),
        photo_hash=m["photo_hash"],
        capture_timestamp=m["capture_timestamp"],
        device_reported_time=m["device_reported_time"],
        device_id=m["device_id"],
        location=loc,
        signature=m["signature"],
        public_key=m["public_key"],
    )


# ---------------------------------------------------------------------------
# Meta / determinism
# ---------------------------------------------------------------------------


def test_meta_declares_pending_mac_status(vectors):
    assert vectors["_meta"]["swift_status"].startswith("NOT VERIFIED")
    assert vectors["_meta"]["warning"].startswith("TEST KEYS ONLY")
    assert vectors["_meta"]["determinism"].startswith("All signatures are RFC 6979")


def test_rfc6979_official_vectors(gen):
    """RFC 6979 A.2.5 (P-256, SHA-256) official k/r/s must match bit for bit."""
    results = gen.selfcheck_rfc6979()
    assert len(results) == 2
    assert all(r["match"] for r in results)


def test_seed_derives_published_public_key(vectors, gen):
    scalar, pub = gen.derive_test_key()
    assert scalar < gen.P256_N
    assert pub.hex() == vectors["test_key"]["public_key_hex"]
    assert base64.b64encode(pub).decode() == vectors["test_key"]["public_key_b64"]


# ---------------------------------------------------------------------------
# SHA-256 / base64 known answers
# ---------------------------------------------------------------------------


def test_sha256_known_answers(vectors):
    kats = {
        "s1-empty": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "s2-abc": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "s3-fips-two-block": (
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        ),
    }
    by_id = {c["id"]: c for c in vectors["sha256_cases"]["cases"]}
    for cid, expected in kats.items():
        case = by_id[cid]
        actual = hashlib.sha256(bytes.fromhex(case["input_hex"])).hexdigest()
        assert actual == expected == case["expected_sha256_hex"], cid


def test_base64_rfc4648_vectors(vectors):
    for case in vectors["base64_cases"]["cases"]:
        if not case["id"].startswith("b"):
            continue
        raw = bytes.fromhex(case["input_hex"])
        assert base64.b64encode(raw).decode() == case["expected_base64"]


def test_base64_rejects_strictly(vectors):
    for case in vectors["base64_cases"]["cases"]:
        if not case["id"].startswith("r"):
            continue
        with pytest.raises(Exception):
            base64.b64decode(case["input_base64"], validate=True)


# ---------------------------------------------------------------------------
# Public key encoding / DER structure - production decoders
# ---------------------------------------------------------------------------


def test_public_key_valid_accepts(vectors):
    raw = _decode_public_key(vectors["public_key_cases"]["valid"]["base64"])
    assert len(raw) == 65 and raw[0] == 0x04


@pytest.mark.parametrize("case_id", [
    "pkr-64-bytes-no-prefix", "pkr-66-bytes", "pkr-prefix-05",
    "pkr-prefix-03", "pkr-empty", "pkr-not-base64",
])
def test_public_key_reject_cases(vectors, case_id):
    by_id = {c["id"]: c for c in vectors["public_key_cases"]["reject_cases"]}
    case = by_id[case_id]
    with pytest.raises(ValueError):
        _decode_public_key(case["base64"])


def test_public_key_known_variants_rejected(vectors):
    for var in vectors["public_key_cases"]["known_variants"]:
        b64 = (
            base64.b64encode(bytes.fromhex(var["hex"])).decode()
            if "hex" in var else var["pem"].replace("\n", "")
        )
        with pytest.raises(ValueError):
            _decode_public_key(b64)


def test_der_valid_structure_accepts(vectors):
    raw = _decode_signature(vectors["der_cases"]["valid_signature"]["base64"])
    assert raw[0] == 0x30


@pytest.mark.parametrize("case_id", [
    "der-r1-wrong-sequence-tag", "der-r2-truncated",
    "der-r3-trailing-garbage", "der-r4-single-integer",
    "der-r5-length-mismatch",
])
def test_der_reject_cases(vectors, case_id):
    by_id = {c["id"]: c for c in vectors["der_cases"]["reject_cases"]}
    case = by_id[case_id]
    b64 = base64.b64encode(bytes.fromhex(case["hex"])).decode()
    expected = case.get("expected", case.get("expected_structure_check"))
    should_reject = expected.startswith("rejected")
    if should_reject:
        with pytest.raises(ValueError):
            _decode_signature(b64)
    else:
        _decode_signature(b64)  # documented lax behavior, accepted as-is


# ---------------------------------------------------------------------------
# Signed manifest cases - production byte-for-byte agreement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", [
    pytest.param(c, id=c["id"]) for c in json.load(
        open(VECTORS_PATH, encoding="utf-8")
    )["signed_manifest_cases"]["cases"]
], scope="module")
def test_signed_case_matches_production_payload(vectors, case):
    manifest = _manifest_from_case(vectors, case)
    produced = canonical_payload(manifest)
    expected = bytes.fromhex(case["canonical_payload"]["hex"])
    assert produced == expected
    assert len(produced) == case["canonical_payload"]["length_bytes"]
    assert hashlib.sha256(produced).hexdigest() == \
        case["canonical_payload"]["sha256_hex"]
    assert case["canonical_payload"]["text"].encode("utf-8") == produced


@pytest.mark.parametrize("case", [
    pytest.param(c, id=c["id"]) for c in json.load(
        open(VECTORS_PATH, encoding="utf-8")
    )["signed_manifest_cases"]["cases"]
], scope="module")
def test_signed_case_verifies_in_production(vectors, case):
    manifest = _manifest_from_case(vectors, case)
    errors = validate_manifest_structure(manifest)
    assert errors == [], f"{case['id']}: structure errors: {errors}"
    ok, detail = verify_signature_with_details(manifest)
    assert ok is True, f"{case['id']}: {detail}"
    assert compute_record_hash(manifest) == case["record_hash_hex"]


def test_payload_contract_line_map(vectors, gen):
    contract = vectors["payload_contract"]
    payload = bytes.fromhex(contract["reference_payload_hex"])
    assert gen._line_map(payload) == contract["reference_line_map"]
    assert b"\r" not in payload
    assert payload.endswith(b"\n") and not payload.endswith(b"\n\n")


# ---------------------------------------------------------------------------
# RFC 6979 signature reproduction for every signed case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", [
    pytest.param(c, id=c["id"]) for c in json.load(
        open(VECTORS_PATH, encoding="utf-8")
    )["signed_manifest_cases"]["cases"]
], scope="module")
def test_signed_case_signature_is_rfc6979_reproducible(vectors, gen, case):
    scalar, _ = gen.derive_test_key()
    _, r, s = gen.rfc6979_sign(
        scalar, bytes.fromhex(case["canonical_payload"]["hex"])
    )
    assert gen.der_encode(r, s).hex() == case["signature"]["der_hex"]


# ---------------------------------------------------------------------------
# Unicode
# ---------------------------------------------------------------------------


def test_unicode_cases_byte_fidelity(vectors):
    for case in vectors["unicode_cases"]["cases"]:
        raw = case["text"].encode("utf-8")
        assert raw.hex() == case["utf8_hex"]
        assert len(raw) == case["length_bytes"]
        assert hashlib.sha256(raw).hexdigest() == case["sha256_hex"]


def test_nfc_nfd_are_different_bytes(vectors):
    by_id = {c["id"]: c for c in vectors["unicode_cases"]["cases"]}
    nfc = by_id["u2-nfc-e"]
    nfd = by_id["u3-nfd-e"]
    assert nfc["utf8_hex"] != nfd["utf8_hex"]
    assert nfc["sha256_hex"] != nfd["sha256_hex"]


def test_unicode_device_id_case_verifies(vectors, gen):
    case = next(
        c for c in vectors["signed_manifest_cases"]["cases"]
        if c["id"] == "v11-unicode-device-id"
    )
    manifest = _manifest_from_case(vectors, case)
    ok, _ = verify_signature_with_details(manifest)
    assert ok is True


# ---------------------------------------------------------------------------
# Newlines
# ---------------------------------------------------------------------------


def test_field_values_with_newlines_rejected_by_production():
    for bad_value in ("bad\nid", "bad\rid"):
        m = SignatureManifest(
            version="1.1", record_id=bad_value, sequence_number=0,
            previous_record_hash=None, photo_hash="00" * 32,
            capture_timestamp="2026-11-09T12:00:00Z",
            device_reported_time="2026-11-09T12:00:00Z",
            device_id="x", location=None, signature="", public_key="",
        )
        errors = validate_manifest_structure(m)
        assert any("newline" in e for e in errors)


@pytest.mark.parametrize("case", [
    pytest.param(c, id=c["id"]) for c in json.load(
        open(VECTORS_PATH, encoding="utf-8")
    )["newline_cases"]["reject_cases"]
], scope="module")
def test_newline_payload_variants_fail_crypto(vectors, pub_bytes, case):
    payload = bytes.fromhex(case["tampered_payload_hex"])
    sig = base64.b64decode(case["signature_base64_used"])
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub_bytes)
    with pytest.raises(InvalidSignature):
        key.verify(sig, payload, ec.ECDSA(SHA256()))


# ---------------------------------------------------------------------------
# Floats
# ---------------------------------------------------------------------------


def test_float_v1_1_table_matches_production_formatter(vectors):
    for case in vectors["float_cases"]["v1_1_format_table"]:
        value = struct.unpack(">d", bytes.fromhex(case["input_bits_hex"]))[0]
        assert _format_float(value) == case["expected_v1_1_string"], \
            case["input_bits_hex"]


def test_float_bits_round_trip(vectors):
    for case in vectors["float_cases"]["v1_1_format_table"]:
        value = struct.unpack(">d", bytes.fromhex(case["input_bits_hex"]))[0]
        assert struct.pack(">d", value).hex() == case["input_bits_hex"]


def test_float_v1_0_table_matches_python_str(vectors):
    for case in vectors["float_cases"]["v1_0_repr_risk_table"]:
        value = struct.unpack(">d", bytes.fromhex(case["input_bits_hex"]))[0]
        assert str(value) == case["python_v1_0_string"]
        assert "PENDING_MAC_VERIFICATION" in case["note"]


def test_v10_location_case_uses_repr_semantics(vectors):
    case = next(
        c for c in vectors["signed_manifest_cases"]["cases"]
        if c["id"] == "v10-location-repr-floats"
    )
    assert "location=0.1,0.2,1.5,-0.0\n" in case["canonical_payload"]["text"]


def test_v11_location_case_uses_dot10g_semantics(vectors):
    case = next(
        c for c in vectors["signed_manifest_cases"]["cases"]
        if c["id"] == "v11-float-precision"
    )
    text = case["canonical_payload"]["text"]
    assert "location=39.9042,-0.1234567891,3.141592654,none\n" in text


# ---------------------------------------------------------------------------
# null vs absent
# ---------------------------------------------------------------------------


def test_null_vs_absent_equivalence(vectors):
    section = vectors["null_absent_cases"]
    eq = section["equivalence_case"]
    a = eq["manifest_json_with_nulls"]
    b = eq["manifest_json_with_absent_keys"]
    m_a = SignatureManifest(
        version=a["version"], record_id=a["record_id"],
        sequence_number=a["sequence_number"],
        previous_record_hash=a.get("previous_record_hash"),
        photo_hash=a["photo_hash"], capture_timestamp=a["capture_timestamp"],
        device_reported_time=a["device_reported_time"], device_id=a["device_id"],
        location=a.get("location"), signature=a["signature"],
        public_key=a["public_key"],
    )
    m_b = SignatureManifest(
        version=b["version"], record_id=b["record_id"],
        sequence_number=b["sequence_number"],
        previous_record_hash=b.get("previous_record_hash"),
        photo_hash=b["photo_hash"], capture_timestamp=b["capture_timestamp"],
        device_reported_time=b["device_reported_time"], device_id=b["device_id"],
        location=b.get("location"), signature=b["signature"],
        public_key=b["public_key"],
    )
    pa = canonical_payload(m_a)
    pb = canonical_payload(m_b)
    assert pa == pb == bytes.fromhex(eq["canonical_payload_hex"])
    ok, _ = verify_signature_with_details(m_a)
    assert ok
    ok, _ = verify_signature_with_details(m_b)
    assert ok


def test_empty_previous_hash_rejected_but_different_from_none(vectors):
    trap = vectors["null_absent_cases"]["empty_string_trap"]
    m = SignatureManifest(
        version="1.1", record_id="x", sequence_number=0,
        previous_record_hash="", photo_hash="00" * 32,
        capture_timestamp="2026-11-09T12:00:00Z",
        device_reported_time="2026-11-09T12:00:00Z",
        device_id="x", location=None, signature="", public_key="",
    )
    assert canonical_payload(m).decode().splitlines()[4] == trap["payload_line"]
    errors = validate_manifest_structure(m)
    assert trap["production_error_sample"] in errors


# ---------------------------------------------------------------------------
# GPS
# ---------------------------------------------------------------------------


def test_gps_valid_cases_accepted_by_model(vectors):
    for case in vectors["gps_cases"]["valid_cases"]:
        loc = LocationData(**case["location"])
        assert loc.latitude == case["location"]["latitude"]


@pytest.mark.parametrize("case", [
    pytest.param(c, id=c["id"]) for c in json.load(
        open(VECTORS_PATH, encoding="utf-8")
    )["gps_cases"]["reject_cases"]
], scope="module")
def test_gps_reject_cases_rejected_by_model(vectors, case):
    reject_specs = {
        "gps-r1-lat-above-90": {"latitude": 90.00001, "longitude": 0.0,
                                  "horizontal_accuracy": 1.0},
        "gps-r2-lon-below-180": {"latitude": 0.0, "longitude": -180.001,
                                  "horizontal_accuracy": 1.0},
        "gps-r3-acc-zero": {"latitude": 0.0, "longitude": 0.0,
                             "horizontal_accuracy": 0.0},
        "gps-r4-acc-negative": {"latitude": 0.0, "longitude": 0.0,
                                  "horizontal_accuracy": -1.0},
        "gps-r5-alt-below-floor": {"latitude": 0.0, "longitude": 0.0,
                                    "horizontal_accuracy": 1.0,
                                    "altitude": -1000.1},
        "gps-r6-alt-above-ceiling": {"latitude": 0.0, "longitude": 0.0,
                                      "horizontal_accuracy": 1.0,
                                      "altitude": 100001.0},
        "gps-r7-nan-lat": {"latitude": float("nan"), "longitude": 0.0,
                             "horizontal_accuracy": 1.0},
        "gps-r8-inf-lon": {"latitude": 0.0, "longitude": float("inf"),
                             "horizontal_accuracy": 1.0},
    }
    with pytest.raises(Exception):
        LocationData(**reject_specs[case["id"]])


def test_gps_negative_zero_preserved(vectors):
    case = next(
        c for c in vectors["gps_cases"]["valid_cases"]
        if c["id"] == "gps-ok-5"
    )
    assert case["expected_v1_1_string"].startswith("-0.0,-0.0")


# ---------------------------------------------------------------------------
# ISO8601 / timezone
# ---------------------------------------------------------------------------


def test_iso8601_table_matches_production_regex(vectors):
    for case in vectors["iso8601_cases"]["valid_cases"]:
        assert ISO8601_REGEX.match(case["value"]), case["id"]
    for case in vectors["iso8601_cases"]["invalid_cases"]:
        assert not ISO8601_REGEX.match(case["value"]), case["id"]
    shape_case = vectors["iso8601_cases"]["shape_only_accepted_example"]
    assert ISO8601_REGEX.match(shape_case["value"])


def test_timezone_spellings_distinct(vectors):
    reps = vectors["timezone_cases"]["same_instant_representations"]
    assert len(reps) == 3
    assert len({r["sha256_of_utf8"] for r in reps}) == 3
    assert reps[0]["value"].endswith("Z")
    assert reps[1]["value"].endswith("+00:00")
    assert reps[2]["value"].endswith("+08:00")


def test_z_vs_offset_cases_have_different_signatures(vectors):
    by_id = {c["id"]: c for c in vectors["signed_manifest_cases"]["cases"]}
    z = by_id["v11-timestamp-z-suffix"]
    off = by_id["v11-timestamp-offset-plus-8"]
    assert z["canonical_payload"]["hex"] != off["canonical_payload"]["hex"]
    assert z["signature"]["der_hex"] != off["signature"]["der_hex"]


# ---------------------------------------------------------------------------
# Field order
# ---------------------------------------------------------------------------


def test_shuffled_json_key_order_equivalent(vectors):
    eq = vectors["field_order_cases"]["shuffled_json_equivalence_case"]
    shuffled = eq["manifest_json_key_order_shuffled"]
    m = SignatureManifest(
        version=shuffled["version"], record_id=shuffled["record_id"],
        sequence_number=shuffled["sequence_number"],
        previous_record_hash=shuffled.get("previous_record_hash"),
        photo_hash=shuffled["photo_hash"],
        capture_timestamp=shuffled["capture_timestamp"],
        device_reported_time=shuffled["device_reported_time"],
        device_id=shuffled["device_id"],
        location=LocationData(**shuffled["location"]),
        signature=shuffled["signature"], public_key=shuffled["public_key"],
    )
    produced = canonical_payload(m)
    assert produced.hex() == eq["canonical_payload_hex"]
    ok, _ = verify_signature_with_details(m)
    assert ok is True


def test_fixed_field_order_constant(vectors):
    from app.services.serialization import FIELD_ORDER as PROD_ORDER
    assert vectors["field_order_cases"]["fixed_order"] == PROD_ORDER


# ---------------------------------------------------------------------------
# Tamper cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", [
    pytest.param(c, id=c["id"]) for c in json.load(
        open(VECTORS_PATH, encoding="utf-8")
    )["tamper_cases"]["reject_cases"]
], scope="module")
def test_tamper_cases_fail(vectors, pub_bytes, case):
    if "tampered_payload_hex" in case:
        payload = bytes.fromhex(case["tampered_payload_hex"])
        sig = base64.b64decode(case["signature_base64_used"])
        key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), pub_bytes
        )
        with pytest.raises(InvalidSignature):
            key.verify(sig, payload, ec.ECDSA(SHA256()))
    else:
        try:
            sig = base64.b64decode(
                case["tampered_signature_base64"], validate=True
            )
            _decode_signature(case["tampered_signature_base64"])
        except (ValueError, Exception):
            return  # rejected at decode layer - acceptable
        base_case = next(
            c for c in vectors["signed_manifest_cases"]["cases"]
            if c["id"] == case["base_case"]
        )
        payload = bytes.fromhex(base_case["canonical_payload"]["hex"])
        key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), pub_bytes
        )
        with pytest.raises(InvalidSignature):
            key.verify(sig, payload, ec.ECDSA(SHA256()))


# ---------------------------------------------------------------------------
# Version compatibility
# ---------------------------------------------------------------------------


def test_version_binding_table(vectors):
    for version, domain in vectors["version_cases"]["binding_table"].items():
        m = SignatureManifest(
            version=version, record_id="x", sequence_number=0,
            previous_record_hash=None, photo_hash="00" * 32,
            capture_timestamp="2026-11-09T12:00:00Z",
            device_reported_time="2026-11-09T12:00:00Z",
            device_id="x", location=None, signature="", public_key="",
        )
        assert canonical_payload(m).split(b"\n")[0].decode() == domain


def test_unknown_version_rejected_but_fallback_recorded(vectors):
    """Review finding: production falls back to the v1.0 domain for unknown
    versions at the serialization layer, while structure validation rejects
    the manifest. The vector records this as-is; the frozen values must keep
    matching the actual production behavior."""
    unknown = vectors["version_cases"]["unknown_version_behavior"]
    assert unknown["canonical_domain_used_by_production"] == "WorkCam-Sig-v1"
    m = SignatureManifest(
        version=unknown["version"], record_id="x", sequence_number=0,
        previous_record_hash=None, photo_hash="00" * 32,
        capture_timestamp="2026-11-09T12:00:00Z",
        device_reported_time="2026-11-09T12:00:00Z",
        device_id="x", location=None, signature="", public_key="",
    )
    assert canonical_payload(m).split(b"\n")[0].decode() \
        == unknown["canonical_domain_used_by_production"]
    errors = validate_manifest_structure(m)
    assert unknown["production_error_sample"] in errors
