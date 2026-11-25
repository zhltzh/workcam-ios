"""TM02: REAL-protocol cross-language vectors - generator + OpenSSL
cross-verification tests.

Pins:
  1. the hand-implemented RFC 6979 matches the RFC's OWN published
     A.2.5 P-256/SHA-256 vectors (k, r, s) - the math is not homemade
  2. generated vectors: payload hash matches canonical text, signature
     verifies in Python AND in OpenSSL (independent implementation)
  3. hostile inputs (CRLF device_id, string latitude) are rejected
  4. v1.0 vs v1.1 float formatting produce the documented byte forms
  5. the Swift test file exists and references the same public key
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)

import pytest  # noqa: E402

import tm02_gen_vectors as tv  # noqa: E402

VEC_PATH = (Path(__file__).resolve().parents[3] / "test-vectors"
            / "crosslang" / "tm02-protocol-vectors.json")


# ------------------------------------------------ RFC 6979 correctness

def _rfc6979_key_and_msg():
    """RFC 6979 A.2.5 first P-256/SHA-256 vector:
    key x = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
    message = 'sample'"""
    key = ec.derive_private_key(
        0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721,
        ec.SECP256R1())
    return key, b"sample"


def test_rfc6979_official_vector_k():
    """RFC 6979 A.2.5 P-256/SHA-256 OFFICIAL vectors (values copied
    from the RFC text, rfc-editor.org/rfc6979.txt - not from memory):
      message "sample": r = EFD48B2A..F3716, s = F7CB1C94..ACDA8
      message "test":   r = F1ABB023..8367, s = 019F4113..0083"""
    key, msg = _rfc6979_key_and_msg()
    from cryptography.hazmat.primitives.asymmetric.utils import (
        decode_dss_signature)
    sig = tv._rfc6979_sign(key, msg)
    r, s = decode_dss_signature(sig)
    assert r == 0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716
    assert s == 0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8
    # second official vector: message = "test"
    sig2 = tv._rfc6979_sign(key, b"test")
    r2, s2 = decode_dss_signature(sig2)
    assert r2 == 0xF1ABB023518351CD71D881567B1EA663ED3EFCF6C5132B354F28D3B0B7D38367
    assert s2 == 0x019F4113742A2B14BD25926B49C649155F267E60D3814B4C0CC84250E46F0083


def test_rfc6979_determinism():
    key, msg = _rfc6979_key_and_msg()
    assert tv._rfc6979_sign(key, msg) == tv._rfc6979_sign(key, msg)


def test_rfc6979_signature_verifies_in_cryptography():
    key, msg = _rfc6979_key_and_msg()
    sig = tv._rfc6979_sign(key, msg)
    key.public_key().verify(sig, msg, ec.ECDSA(hashes.SHA256()))


# ------------------------------------------------ generated vectors

@pytest.fixture(scope="module")
def vectors():
    if not VEC_PATH.exists():
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[3]
                                 / "tools" / "tm02_gen_vectors.py")],
            check=True, capture_output=True, timeout=600)
    return json.loads(VEC_PATH.read_text(encoding="utf-8"))


def test_at_least_100_cases(vectors):
    assert len(vectors["cases"]) >= 100


def test_all_case_names_unique(vectors):
    names = [c["name"] for c in vectors["cases"]]
    assert len(names) == len(set(names))


def test_payload_hash_matches_canonical_text(vectors):
    import hashlib
    for c in vectors["cases"]:
        if "canonical_payload_text" not in c:
            continue
        expect = hashlib.sha256(
            c["canonical_payload_text"].encode("utf-8")).hexdigest()
        assert c["payload_sha256"] == expect, c["name"]


def test_every_signature_verifies_in_python(vectors):
    from cryptography.exceptions import InvalidSignature
    raw = base64.b64decode(vectors["test_public_key_b64"])
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    checked = 0
    for c in vectors["cases"]:
        if "canonical_payload_text" not in c:
            continue
        try:
            pub.verify(base64.b64decode(c["signature_der_b64"]),
                       c["canonical_payload_text"].encode("utf-8"),
                       ec.ECDSA(hashes.SHA256()))
            checked += 1
        except InvalidSignature:
            pytest.fail(f"python verify failed: {c['name']}")
    assert checked >= 90


def test_openssl_cross_verification_or_recorded_gap(vectors):
    """OpenSSL is the INDEPENDENT second implementation. If openssl is
    not on PATH the gap must be RECORDED in the vector file's meta -
    never silently skipped."""
    import shutil
    exe = shutil.which("openssl")
    if exe is None:
        for cand in (r"C:\Program Files\Git\mingw64\bin\openssl.exe",
                     r"C:\Program Files\Git\usr\bin\openssl.exe"):
            if Path(cand).exists():
                exe = cand
                break
    meta_note = vectors["_meta"]["openssl_note"]
    if exe is None:
        assert "SKIPPED" in meta_note or "not found" in meta_note
        pytest.skip(f"openssl unavailable: {meta_note}")
    assert "cross-verified" in meta_note
    # spot-check five vectors through openssl directly
    raw = base64.b64decode(vectors["test_public_key_b64"])
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    import hashlib
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        pub_pem = td / "pub.pem"
        pub_pem.write_bytes(pub.public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
        sample = [c for c in vectors["cases"]
                  if "canonical_payload_text" in c][:5]
        for c in sample:
            dg = td / "d"
            dg.write_bytes(hashlib.sha256(
                c["canonical_payload_text"].encode("utf-8")).digest())
            sig = td / "s.der"
            sig.write_bytes(base64.b64decode(c["signature_der_b64"]))
            r = subprocess.run(
                [exe, "pkeyutl", "-verify", "-pubin", "-inkey",
                 str(pub_pem), "-in", str(dg), "-sigfile", str(sig)],
                capture_output=True, text=True, timeout=60)
            assert "Signature Verified Successfully" in r.stdout, \
                f"openssl rejected {c['name']}: {r.stdout} {r.stderr}"


def test_hostile_cases_rejected(vectors):
    hostile = [c for c in vectors["cases"]
               if "expect_build_error" in c
               or "expect_validation_error" in c]
    assert len(hostile) >= 2
    for c in hostile:
        if "expect_build_error" in c:
            try:
                import evidence_schema as es
                es.build_canonical_payload(c["record"])
                pytest.fail(f"builder accepted hostile input {c['name']}")
            except (ValueError, KeyError):
                pass
    assert vectors["_meta"]["hostile_guards"] == \
        "all hostile cases rejected"


def test_float_formatting_contract(vectors):
    by_name = {c["name"]: c for c in vectors["cases"]}
    v11 = by_name["1.1-loc-exp-notation"]["canonical_payload_text"]
    assert "location=" in v11
    loc_line = [ln for ln in v11.split("\n") if ln.startswith("location=")][0]
    # %.10g guarantees a decimal point or exponent marker
    assert ("." in loc_line) or ("e" in loc_line.lower())
    v10 = by_name["1.0-loc-exp-notation"]["canonical_payload_text"]
    assert v10.startswith("WorkCam-Sig-v1\n")
    assert v11.startswith("WorkCam-Sig-v1.1\n")


def test_swift_file_exists_with_same_key(vectors):
    swift_path = VEC_PATH.parent / "TM02ProtocolVectorTests.swift"
    assert swift_path.exists()
    text = swift_path.read_text(encoding="utf-8")
    assert vectors["test_public_key_b64"] in text
    assert "CROSS_PLATFORM_PENDING" in text
    # every python-side vector name appears in the Swift suite
    for c in vectors["cases"]:
        if "canonical_payload_text" in c:
            assert f'"{c["name"]}"' in text, c["name"]


def test_python_and_openssl_notes_are_clean(vectors):
    assert vectors["_meta"]["python_problems"] == []
    assert vectors["_meta"]["openssl_problems"] == []
    assert "CROSS_PLATFORM_PENDING" in vectors["_meta"]["swift_status"]
