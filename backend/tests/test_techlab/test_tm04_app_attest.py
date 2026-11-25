"""TM04: App Attest server verification - synthetic-vector tests.

Everything here runs against SYNTHETIC CA material (synth_vectors.py);
real-Apple cross-verification stays DEVICE_PENDING. The tests prove
the VERIFIER LOGIC: parsing, chain checks, nonce binding, app-id
binding, signature checks and counter replay protection.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"
                        / "appattest"))

import cbor_min  # noqa: E402
import synth_vectors as sv  # noqa: E402
from app_attest_verify import (  # noqa: E402
    AttestError, verify_assertion, verify_attestation)

import pytest  # noqa: E402

APP_ID = "TEAM1234.com.example.workcam"
RP_ID = "com.example.workcam"


@pytest.fixture(scope="module")
def tree():
    return sv.build_tree(APP_ID, b"challenge-abc-123")


@pytest.fixture(scope="module")
def att(tree):
    root, leaf, inter, key, cose = tree
    obj = sv.build_attestation(leaf, inter, RP_ID, cose)
    return {"obj": obj, "root": root, "key": key, "cose": cose}


def test_cbor_roundtrip_min_types():
    cases = [1, 23, 24, 255, 256, 65536, 2 ** 40,
             -1, -25, "hello", "中文测试", b"\x00\x01\x02",
             [1, "two", b"3"], {"a": 1, 2: "b"}, True, False, None,
             3.5, -0.25]
    for v in cases:
        assert cbor_min.loads(cbor_min.dumps(v)) == v


def test_cbor_rejects_garbage():
    for bad in [b"", b"\xff", b"\x1c", b"\x5f\x01",   # break / reserved
                b"\x18",                               # truncated uint8
                b"\x58\x05ab",                         # truncated bytes
                b"\x63ab",                             # truncated text
                b"\x7b\x01\x02"]:                      # indefinite map
        with pytest.raises(cbor_min.CBORDecodeError):
            cbor_min.loads(bad)


def test_cbor_rejects_duplicate_map_keys_and_trailing_bytes():
    with pytest.raises(cbor_min.CBORDecodeError):
        cbor_min.loads(cbor_min.dumps({1: 1}) + b"\x01")
    dup = bytes([0xa2, 0x01, 0x02, 0x01, 0x03])          # {1:2, 1:3}
    with pytest.raises(cbor_min.CBORDecodeError):
        cbor_min.loads(dup)


def test_attestation_verifies_against_synthetic_anchor(att):
    r = verify_attestation(att["obj"], b"challenge-abc-123", APP_ID,
                           att["root"], environment_hint="development")
    assert r.verified, r.failures
    assert r.app_id == APP_ID
    assert "does NOT prove" in r.key_binding_note, \
        "the photo-key binding boundary must be stated, always"


def test_attestation_nonce_binding_enforced(att, tree):
    # replay with the WRONG server challenge -> nonce extension
    # mismatch (this is the anti-replay core of attestation)
    r = verify_attestation(att["obj"], b"challenge-OTHER", APP_ID,
                           att["root"])
    assert not r.verified
    assert any("nonce" in f for f in r.failures)


def test_attestation_rejects_wrong_app_id(att):
    r = verify_attestation(att["obj"], b"challenge-abc-123",
                           "OTHER.team.com.wrongapp", att["root"])
    assert not r.verified
    assert any("app identity" in f for f in r.failures)


def test_attestation_rejects_untrusted_chain(att):
    # a DIFFERENT synthetic root that never signed this tree
    other_root = sv.build_tree("x.y.z", b"other")[0]
    r = verify_attestation(att["obj"], b"challenge-abc-123", APP_ID,
                           other_root)
    assert not r.verified
    assert any("chain" in f for f in r.failures)


def test_attestation_rejects_bad_cbor_and_fmt():
    with pytest.raises(AttestError):
        verify_attestation(b"\x9f\xff", b"c", APP_ID, b"\x00")
    import cbor_min as cm
    wrong_fmt = cm.dumps({"fmt": "webauthn", "attStmt": b"x",
                          "authData": b"y"})
    with pytest.raises(AttestError):
        verify_attestation(wrong_fmt, b"c", APP_ID, b"\x00")


def test_assertion_full_cycle_and_counter_replay(att):
    key = att["key"]
    from cryptography.hazmat.primitives.serialization import Encoding, \
        PublicFormat
    point = key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    cdh = hashlib.sha256(b'{"challenge":"challenge-abc-123"}').digest()
    rp_hash = hashlib.sha256(RP_ID.encode()).digest()
    a1 = sv.build_assertion(key, RP_ID, counter=1, client_data_hash=cdh)
    r1 = verify_assertion(a1, cdh, point, rp_hash, last_counter=None)
    assert r1["verified"], r1
    # counter must strictly increase
    a1_replay = sv.build_assertion(key, RP_ID, counter=1,
                                    client_data_hash=cdh)
    r2 = verify_assertion(a1_replay, cdh, point, rp_hash, last_counter=1)
    assert not r2["verified"] and r2["error"] == "counter_replay"
    # next counter passes
    a2 = sv.build_assertion(key, RP_ID, counter=2, client_data_hash=cdh)
    r3 = verify_assertion(a2, cdh, point, rp_hash, last_counter=1)
    assert r3["verified"]


def test_assertion_rejects_wrong_client_hash_and_rp(att):
    from cryptography.hazmat.primitives.serialization import Encoding, \
        PublicFormat
    point = att["key"].public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    cdh = hashlib.sha256(b"client-data").digest()
    rp_hash = hashlib.sha256(RP_ID.encode()).digest()
    a = sv.build_assertion(att["key"], RP_ID, counter=5,
                           client_data_hash=cdh)
    ok = verify_assertion(a, cdh, point, rp_hash, None)
    assert ok["verified"]
    bad_cdh = verify_assertion(a, hashlib.sha256(b"TAMPERED").digest(),
                               point, rp_hash, None)
    assert not bad_cdh["verified"] and bad_cdh["error"] == "bad_signature"
    bad_rp = verify_assertion(a, cdh, point,
                              hashlib.sha256(b"other.rp").digest(), None)
    assert not bad_rp["verified"] and bad_rp["error"] == "rp_id_mismatch"


def test_assertion_rejects_bad_cbor():
    with pytest.raises(AttestError):
        verify_assertion(b"\x00\x01\x02", b"c", b"\x04" + b"\x00" * 64,
                         b"\x00" * 32, None)


def test_honesty_boundary_constant():
    """The verifier must NEVER auto-claim photo-key provenance."""
    import app_attest_verify as av
    note = av.AttestationResult.__dataclass_fields__[
        "key_binding_note"].default
    assert "does NOT prove" in note
