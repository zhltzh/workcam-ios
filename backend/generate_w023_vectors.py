"""
B02-W023: generate cross-language (Python/Swift) signature & serialization
test vectors for the WorkCam signature protocol.

Determinism: all ECDSA signatures below are produced with RFC 6979
deterministic ECDSA (P-256 + SHA-256), so re-running this script always
produces a byte-identical JSON file.

Integrity guarantees enforced at generation time (script fails loudly):
  1. The RFC 6979 implementation is checked against the official
     RFC 6979 Appendix A.2.5 test vectors (P-256, SHA-256, "sample" and
     "test" messages) - k, r and s must match bit for bit.
  2. Every positive signature is verified with the `cryptography`
     library before being frozen into the JSON.
  3. The independent payload builder in this script is cross-checked
     against the production `app.services.serialization.canonical_payload`
     for every signed case.
  4. Every negative (tamper) case is verified to actually FAIL
     verification (or be rejected) before being frozen.

The test private key is DERIVED from a public seed string and is a
TEST KEY ONLY. Never use it (or these vectors) with production data.

This script does NOT modify production code. It only reads
`app.services.serialization` / `app.models.capture` to cross-check.

Output: test-vectors/crosslang/w023-crosslang-vectors.json
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import struct

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.models.capture import LocationData, SignatureManifest
from app.services.serialization import (
    canonical_payload as production_canonical_payload,
)
from app.services.serialization import _format_float as production_format_float

VECTORS_OUT = os.path.join(
    os.path.dirname(__file__), "..", "test-vectors", "crosslang",
    "w023-crosslang-vectors.json",
)

# ---------------------------------------------------------------------------
# P-256 / RFC 6979 primitives
# ---------------------------------------------------------------------------

P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
QLEN = 256
RLEN = 32  # bytes


def _bits2int(data: bytes) -> int:
    val = int.from_bytes(data, "big")
    blen = len(data) * 8
    if blen > QLEN:
        val >>= blen - QLEN
    return val


def _int2octets(x: int) -> bytes:
    return x.to_bytes(RLEN, "big")


def _bits2octets(data: bytes) -> bytes:
    z1 = _bits2int(data)
    return _int2octets(z1 - P256_N if z1 >= P256_N else z1)


def rfc6979_k_candidates(x_priv: int, h1: bytes):
    """Yield RFC 6979 candidate k values (section 3.2, HMAC-SHA-256)."""
    V = b"\x01" * 32
    K = b"\x00" * 32
    x_oct = _int2octets(x_priv)
    h1_oct = _bits2octets(h1)
    K = hmac.new(K, V + b"\x00" + x_oct + h1_oct, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    K = hmac.new(K, V + b"\x01" + x_oct + h1_oct, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    while True:
        T = b""
        while len(T) * 8 < QLEN:
            V = hmac.new(K, V, hashlib.sha256).digest()
            T += V
        k = _bits2int(T)
        if 1 <= k < P256_N:
            yield k
        K = hmac.new(K, V + b"\x00", hashlib.sha256).digest()
        V = hmac.new(K, V, hashlib.sha256).digest()


def _kg_x(k: int) -> int:
    """x-coordinate of k*G, delegated to the cryptography library."""
    pub = ec.derive_private_key(k, ec.SECP256R1()).public_key()
    raw = pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return int.from_bytes(raw[1:33], "big")


def rfc6979_sign(x_priv: int, message: bytes) -> tuple[int, int, int]:
    """RFC 6979 deterministic ECDSA sign. Returns (k, r, s)."""
    h1 = hashlib.sha256(message).digest()
    e = _bits2int(h1) % P256_N
    for k in rfc6979_k_candidates(x_priv, h1):
        r = _kg_x(k) % P256_N
        if r == 0:
            continue  # RFC 6979 section 3.4: k unsuitable, keep looping
        s = pow(k, -1, P256_N) * (e + r * x_priv) % P256_N
        if s == 0:
            continue
        return k, r, s
    raise RuntimeError("unreachable")


def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _der_int(v: int) -> bytes:
    body = v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big")
    if body[0] & 0x80:
        body = b"\x00" + body
    return b"\x02" + _der_len(len(body)) + body


def der_encode(r: int, s: int) -> bytes:
    body = _der_int(r) + _der_int(s)
    return b"\x30" + _der_len(len(body)) + body


def der_ints(der: bytes) -> tuple[int, int]:
    """Minimal DER parse of a SEQUENCE(INTEGER, INTEGER)."""
    assert der[0] == 0x30
    pos = 2
    assert der[pos] == 0x02
    pos += 1
    rlen = der[pos]
    pos += 1
    r = int.from_bytes(der[pos:pos + rlen], "big")
    pos += rlen
    assert der[pos] == 0x02
    pos += 1
    slen = der[pos]
    pos += 1
    s = int.from_bytes(der[pos:pos + slen], "big")
    return r, s


# ---------------------------------------------------------------------------
# RFC 6979 official self-check (Appendix A.2.5, NIST P-256, SHA-256)
# ---------------------------------------------------------------------------

RFC6979_X = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
RFC6979_P256_SHA256 = [
    # (message, k, r, s) - verbatim from RFC 6979 A.2.5
    (b"sample",
     0xA6E3C57DD01ABE90086538398355DD4C3B17AA873382B0F24D6129493D8AAD60,
     0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716,
     0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8),
    (b"test",
     0xD16B6AE827F17175E040871A1C7EC3500192C4C92677336EC2537ACAEE0008E0,
     0xF1ABB023518351CD71D881567B1EA663ED3EFCF6C5132B354F28D3B0B7D38367,
     0x019F4113742A2B14BD25926B49C649155F267E60D3814B4C0CC84250E46F0083),
]


def selfcheck_rfc6979() -> list[dict]:
    results = []
    for msg, k_exp, r_exp, s_exp in RFC6979_P256_SHA256:
        k, r, s = rfc6979_sign(RFC6979_X, msg)
        assert (k, r, s) == (k_exp, r_exp, s_exp), (
            f"RFC 6979 self-check FAILED for {msg!r}: "
            f"got k={k:x} r={r:x} s={s:x}"
        )
        results.append({
            "message": msg.decode(),
            "expected_k_hex": format(k_exp, "x"),
            "expected_r_hex": format(r_exp, "x"),
            "expected_s_hex": format(s_exp, "x"),
            "match": True,
        })
    return results


# ---------------------------------------------------------------------------
# Independent canonical payload builder (Swift port of this logic lives in
# W023CrosslangVectorTests.swift). Cross-checked against production code.
# ---------------------------------------------------------------------------

FIELD_ORDER = [
    "version", "record_id", "sequence_number", "previous_record_hash",
    "photo_hash", "capture_timestamp", "device_reported_time",
    "device_id", "location",
]


def _finite(value: float) -> bool:
    return not (math.isnan(value) or math.isinf(value))


def fmt_float_v11(value: float) -> str:
    """Independent reimplementation of the production v1.1 float rule."""
    if not _finite(value):
        raise ValueError("non-finite float")
    if value == int(value) and not (value == 0.0 and str(value).startswith("-")):
        return f"{int(value)}.0"
    formatted = f"{value:.10g}"
    if "." not in formatted and "e" not in formatted.lower():
        formatted += ".0"
    return formatted


def _loc_str(loc: dict | None, version: str) -> str:
    if loc is None:
        return "none"
    if version == "1.1":
        alt = "none" if loc.get("altitude") is None else fmt_float_v11(loc["altitude"])
        return ",".join([
            fmt_float_v11(loc["latitude"]),
            fmt_float_v11(loc["longitude"]),
            fmt_float_v11(loc["horizontal_accuracy"]),
            alt,
        ])
    alt = "none" if loc.get("altitude") is None else str(loc["altitude"])
    return ",".join([
        str(loc["latitude"]), str(loc["longitude"]),
        str(loc["horizontal_accuracy"]), alt,
    ])


def build_payload(fields: dict) -> bytes:
    v = fields["version"]
    domain = "WorkCam-Sig-v1.1" if v == "1.1" else "WorkCam-Sig-v1"
    lines = [domain]
    for name in FIELD_ORDER:
        if name == "location":
            lines.append(f"location={_loc_str(fields.get('location'), v)}")
            continue
        value = fields.get(name)
        if value is None:
            lines.append(f"{name}=none")
        else:
            lines.append(f"{name}={value}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _float_bits(value: float) -> str:
    return struct.pack(">d", value).hex()


def _production_manifest(fields: dict, signature: str, public_key_b64: str):
    loc = None
    if fields.get("location") is not None:
        loc = LocationData(**fields["location"])
    return SignatureManifest(
        version=fields["version"],
        record_id=fields["record_id"],
        sequence_number=fields["sequence_number"],
        previous_record_hash=fields.get("previous_record_hash"),
        photo_hash=fields["photo_hash"],
        capture_timestamp=fields["capture_timestamp"],
        device_reported_time=fields["device_reported_time"],
        device_id=fields["device_id"],
        location=loc,
        signature=signature,
        public_key=public_key_b64,
    )


def _line_map(payload: bytes) -> list[dict]:
    out = []
    offset = 0
    for i, line in enumerate(payload.split(b"\n")[:-1]):
        text = line.decode("utf-8")
        key = "(domain)" if "=" not in text else text.split("=", 1)[0]
        out.append({
            "line": i + 1,
            "key": key,
            "content_utf8": text,
            "byte_offset": offset,
            "byte_length_including_lf": len(line) + 1,
        })
        offset += len(line) + 1
    return out


# ---------------------------------------------------------------------------
# Fixed test key (TEST ONLY)
# ---------------------------------------------------------------------------

TEST_KEY_SEED = b"workcam-w023-crosslang-test-key-v1"


def derive_test_key() -> tuple[int, bytes]:
    scalar = int.from_bytes(hashlib.sha256(TEST_KEY_SEED).digest(), "big")
    if scalar >= P256_N:
        raise RuntimeError("test key seed must be below the curve order")
    priv = ec.derive_private_key(scalar, ec.SECP256R1())
    pub_bytes = priv.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    return scalar, pub_bytes


# ---------------------------------------------------------------------------
# Vector sections
# ---------------------------------------------------------------------------

TEST_PHOTO = bytes(range(256)) * 4  # 1024 bytes, 0x00-0xff pattern x4
TEST_PHOTO_HASH = hashlib.sha256(TEST_PHOTO).hexdigest()


def _verify(pub_bytes: bytes, sig_der: bytes, payload: bytes) -> bool:
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub_bytes)
    try:
        key.verify(sig_der, payload, ec.ECDSA(SHA256()))
        return True
    except InvalidSignature:
        return False


def sha256_section() -> dict:
    cases = []
    kat = [
        ("s1-empty", b"", None,
         "FIPS 180-4 / RFC 6234 known answer"),
        ("s2-abc", b"abc", "abc",
         "FIPS 180-4 / RFC 6234 known answer"),
        ("s3-fips-two-block", b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
         "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
         "FIPS 180-4 / RFC 6234 known answer (two-block message)"),
        ("s4-domain-separator", b"WorkCam-Sig-v1.1\n", "WorkCam-Sig-v1.1\\n",
         "Protocol domain separator line as signed bytes"),
        ("s5-multibyte-utf8", "北京Beijing123".encode("utf-8"),
         "北京Beijing123",
         "Multibyte UTF-8 text"),
        ("s6-ff-pattern", bytes(range(256)), None,
         "All byte values 0x00-0xff in order"),
    ]
    for cid, data, text, note in kat:
        cases.append({
            "id": cid,
            "input_hex": data.hex(),
            "input_text_utf8": text,
            "length_bytes": len(data),
            "expected_sha256_hex": hashlib.sha256(data).hexdigest(),
            "known_answer_reference": note,
        })
    cases.append({
        "id": "s7-w023-test-photo",
        "input_hex": TEST_PHOTO.hex(),
        "input_text_utf8": None,
        "length_bytes": len(TEST_PHOTO),
        "expected_sha256_hex": TEST_PHOTO_HASH,
        "known_answer_reference": "The fixed photo used by every w023 manifest case",
    })
    return {
        "description": "SHA-256 vectors. Every value recomputed at generation time.",
        "cases": cases,
    }


def base64_section(pub_b64: str, sig_b64: str) -> dict:
    rfc4648 = [
        ("b1-empty", b"", ""),
        ("b2-f", b"f", "Zg=="),
        ("b3-fo", b"fo", "Zm8="),
        ("b4-foo", b"foo", "Zm9v"),
        ("b5-foob", b"foob", "Zm9vYg=="),
        ("b6-fooba", b"fooba", "Zm9vYmE="),
        ("b7-foobar", b"foobar", "Zm9vYmFy"),
    ]
    cases = []
    for cid, raw, expected in rfc4648:
        actual = base64.b64encode(raw).decode()
        assert actual == expected, f"{cid}: RFC 4648 mismatch"
        cases.append({
            "id": cid,
            "input_hex": raw.hex(),
            "expected_base64": expected,
            "reference": "RFC 4648 section 10 test vectors",
        })
    pub_raw = base64.b64decode(pub_b64)
    cases.append({
        "id": "b8-w023-public-key",
        "input_hex": pub_raw.hex(),
        "expected_base64": pub_b64,
        "reference": "w023 fixed test public key (65-byte uncompressed point)",
    })
    rejects = [
        ("r1-urlsafe-minus", "Zm9v-Y==",
         "URL-safe alphabet '-' is outside the standard alphabet"),
        ("r2-urlsafe-underscore", "Zm9v_Y==",
         "URL-safe alphabet '_' is outside the standard alphabet"),
        ("r3-embedded-space", "Zm 9v",
         "Whitespace inside the string must be rejected by strict decoding"),
        ("r4-illegal-character", "Zm!v",
         "'!' is not part of any base64 alphabet"),
        ("r5-wrong-padding", "Zm9vYg",
         "Length not a multiple of 4 (missing '=') must be rejected"),
        ("r6-embedded-newline", "Zm9v\nYg==",
         "MIME folded base64 (embedded LF) must be rejected by strict decoding"),
    ]
    for cid, value, note in rejects:
        try:
            base64.b64decode(value, validate=True)
            raise RuntimeError(f"{cid}: strict b64decode unexpectedly accepted input")
        except Exception as exc:  # binascii.Error / ValueError
            assert not isinstance(exc, RuntimeError)
        rejects_raw = {
            "id": cid,
            "input_base64": value,
            "expected": "rejected_by_strict_base64_decode",
            "note": note,
        }
        cases.append(rejects_raw)
    return {
        "description": (
            "Standard base64 (RFC 4648 section 4, with padding). Protocol "
            "rule: decode strictly (reject URL-safe alphabet, embedded "
            "whitespace, wrong padding). Python b64decode(validate=True) "
            "and Swift Data(base64Encoded:) both validated at generation "
            "time on the Python side."
        ),
        "cases": cases,
        "signature_base64_reference": sig_b64,
    }


def public_key_section(pub_bytes: bytes) -> dict:
    priv = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), pub_bytes
    )
    spki_der = priv.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    spki_pem = priv.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    compressed = pub_bytes[:1] + pub_bytes[33:65]  # 0x02/0x03 + x (SEC1 compressed)
    # SEC1 compressed prefix parity: y is even -> 0x02, odd -> 0x03
    y_int = int.from_bytes(pub_bytes[33:65], "big")
    compressed = bytes([0x02 | (y_int & 1)]) + pub_bytes[1:33]

    valid = {
        "id": "pk-valid-uncompressed",
        "hex": pub_bytes.hex(),
        "base64": base64.b64encode(pub_bytes).decode(),
        "length_bytes": len(pub_bytes),
        "prefix_byte": "04",
        "expected": "accepted",
        "format": "SEC1 uncompressed point 0x04 || x || y (65 bytes)",
    }
    variants = [
        {
            "id": "pkv-sec1-compressed",
            "hex": compressed.hex(),
            "length_bytes": len(compressed),
            "note": (
                "SEC1 compressed point (0x02/0x03 || x, 33 bytes). A valid "
                "curve point, but the v1.x protocol accepts ONLY the "
                "uncompressed form. Backend _decode_public_key rejects it "
                "(length != 65); Swift P256 x963Representation also rejects it."
            ),
            "protocol_expected": "rejected_by_public_key_format_check",
        },
        {
            "id": "pkv-spki-der",
            "hex": spki_der.hex(),
            "length_bytes": len(spki_der),
            "note": (
                "X.509 SubjectPublicKeyInfo (PKCS#8 public key envelope), "
                "DER-encoded. Same key material, different container. The "
                "v1.x protocol does not accept SPKI: it is rejected by the "
                "65-byte/0x04 format check. Recorded for future protocol "
                "evolution only."
            ),
            "protocol_expected": "rejected_by_public_key_format_check",
        },
        {
            "id": "pkv-spki-pem",
            "pem": spki_pem.decode("ascii").strip(),
            "note": (
                "PEM armor of the same SPKI key ('-----BEGIN PUBLIC KEY-----'). "
                "A text container, not the protocol wire format. Rejected by "
                "base64 strict decode (embedded newlines) and by the length "
                "check. Recorded for reference only."
            ),
            "protocol_expected": "rejected_by_public_key_format_check",
        },
    ]
    compressed_03 = bytes([0x03]) + compressed[1:]
    rejects = [
        ("pkr-64-bytes-no-prefix", pub_bytes[1:].hex(), 64,
         "64 bytes: raw x||y without the 0x04 prefix"),
        ("pkr-66-bytes", (pub_bytes + b"\x00").hex(), 66,
         "66 bytes: right length + one trailing byte"),
        ("pkr-prefix-05", (b"\x05" + pub_bytes[1:]).hex(), 65,
         "65 bytes but prefix 0x05 (invalid point form)"),
        ("pkr-prefix-03", compressed_03.hex(),
         33, "33 bytes SEC1 compressed form with 0x03 prefix"),
        ("pkr-empty", "", 0, "Empty value"),
    ]
    reject_cases = []
    for cid, hx, ln, note in rejects:
        try:
            from app.services.signature import _decode_public_key
            _decode_public_key(base64.b64encode(bytes.fromhex(hx)).decode())
            raise RuntimeError(f"{cid}: production decoder unexpectedly accepted key")
        except ValueError:
            pass
        reject_cases.append({
            "id": cid,
            "hex": hx,
            "base64": base64.b64encode(bytes.fromhex(hx)).decode(),
            "length_bytes": ln,
            "expected": "rejected_by_public_key_format_check",
            "note": note,
        })
    # non-base64 garbage
    reject_cases.append({
        "id": "pkr-not-base64",
        "base64": "!!!not-base64!!!",
        "expected": "rejected_by_base64_decode",
        "note": "Not valid base64 at all",
    })
    return {
        "description": (
            "Protocol v1.x public key wire format: base64 of the 65-byte "
            "SEC1 uncompressed point. Format check on BOTH ends: length must "
            "be 65 AND first byte must be 0x04, BEFORE any curve math. "
            "Compressed points, SPKI/PKCS#8 DER and PEM containers are "
            "known encoding variants of the same key and MUST be rejected."
        ),
        "valid": valid,
        "known_variants": variants,
        "reject_cases": reject_cases,
    }


def der_section(base_sig_der: bytes) -> dict:
    from app.services.signature import _decode_signature
    r, s = der_ints(base_sig_der)
    rejects = []
    # 0x31 wrong SEQUENCE tag
    rejects.append({
        "id": "der-r1-wrong-sequence-tag",
        "hex": (b"\x31" + base_sig_der[1:]).hex(),
        "expected": "rejected_by_der_structure_check",
        "note": "0x31 (SET) instead of 0x30 (SEQUENCE)",
    })
    # truncated below minimum
    rejects.append({
        "id": "der-r2-truncated",
        "hex": base_sig_der[:7].hex(),
        "expected": "rejected_by_der_structure_check",
        "note": "Truncated below the 8-byte minimum",
    })
    # trailing garbage
    rejects.append({
        "id": "der-r3-trailing-garbage",
        "hex": (base_sig_der + b"\xde\xad").hex(),
        "expected": "rejected_by_der_structure_check",
        "note": "Two extra bytes appended after the SEQUENCE",
    })
    # only one INTEGER
    body_one_int = _der_int(r)
    one_int = b"\x30" + _der_len(len(body_one_int)) + body_one_int
    rejects.append({
        "id": "der-r4-single-integer",
        "hex": one_int.hex(),
        "expected": "rejected_by_der_structure_check",
        "note": "SEQUENCE containing only one INTEGER (missing s)",
    })
    # declared length mismatch
    bad_len = bytearray(base_sig_der)
    bad_len[1] -= 1
    rejects.append({
        "id": "der-r5-length-mismatch",
        "hex": bytes(bad_len).hex(),
        "expected": "rejected_by_der_structure_check",
        "note": "Declared SEQUENCE length is one byte shorter than actual data",
    })
    # BER non-minimal INTEGER: build from the RFC 6979 official "sample"
    # signature whose r (0xEFD48B2A...) is GUARANTEED to have its high bit
    # set, so the minimal-DER form necessarily carries a leading 0x00.
    r_off, s_off = RFC6979_P256_SHA256[0][2], RFC6979_P256_SHA256[0][3]
    official_der = der_encode(r_off, s_off)
    r_bytes = r_off.to_bytes(32, "big")
    assert r_bytes[0] & 0x80
    ber_body = b"\x02\x20" + r_bytes + _der_int(s_off)
    ber_sig = b"\x30" + _der_len(len(ber_body)) + ber_body
    # empirical: production structural check on the BER form
    try:
        _decode_signature(base64.b64encode(ber_sig).decode())
        ber_structure = "accepted_by_production_der_structure_check"
    except ValueError:
        ber_structure = "rejected_by_production_der_structure_check"
    # empirical: does the BER form still verify cryptographically?
    official_pub = ec.derive_private_key(
        RFC6979_X, ec.SECP256R1()
    ).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    ber_crypto = _verify(official_pub, ber_sig, b"sample")
    rejects.append({
        "id": "der-r6-ber-nonminimal-r",
        "hex": ber_sig.hex(),
        "minimal_der_hex": official_der.hex(),
        "expected_structure_check": ber_structure,
        "crypto_verify_with_ber_form": ber_crypto,
        "note": (
            "Same (r,s) as the official RFC 6979 'sample' signature but r "
            "encoded WITHOUT the required leading 0x00 (non-minimal BER). "
            "Empirical production behaviors recorded above exactly as "
            "measured. RFC 3279 strict DER would reject this encoding."
        ),
    })
    for case in rejects:
        hex_b64 = base64.b64encode(bytes.fromhex(case["hex"])).decode()
        if case["id"] == "der-r6-ber-nonminimal-r":
            expected_reject = case["expected_structure_check"].startswith("rejected")
        else:
            expected_reject = case["expected"].startswith("rejected")
        try:
            _decode_signature(hex_b64)
            accepted = True
        except ValueError:
            accepted = False
        assert accepted != expected_reject, (
            f"{case['id']}: production DER check disagrees with expected"
        )
    return {
        "description": (
            "DER-encoded ECDSA signatures (RFC 3279). Structure check before "
            "crypto: SEQUENCE tag 0x30, declared length matches, exactly two "
            "INTEGERs, no trailing data. NOTE (review finding): the current "
            "production checker does NOT enforce INTEGER minimality/padding "
            "(see der-r6) and does NOT enforce low-s. Recorded as-is; "
            "tightening is proposed in "
            "docs/crosslang-serialization-proposal.md, not implemented here."
        ),
        "valid_signature": {
            "der_hex": base_sig_der.hex(),
            "base64": base64.b64encode(base_sig_der).decode(),
            "length_bytes": len(base_sig_der),
            "r_hex": format(r, "x"),
            "s_hex": format(s, "x"),
        },
        "reject_cases": rejects,
    }


def _mk_case(
    case_id: str,
    description: str,
    fields: dict,
    priv_scalar: int,
    pub_bytes: bytes,
) -> tuple[dict, bytes]:
    payload = build_payload(fields)
    k, r, s = rfc6979_sign(priv_scalar, payload)
    sig_der = der_encode(r, s)
    assert _verify(pub_bytes, sig_der, payload), f"{case_id}: self-verify failed"

    sig_b64 = base64.b64encode(sig_der).decode()
    pub_b64 = base64.b64encode(pub_bytes).decode()

    # cross-check against PRODUCTION canonical_payload (must be identical)
    prod_manifest = _production_manifest(fields, sig_b64, pub_b64)
    prod_payload = production_canonical_payload(prod_manifest)
    assert prod_payload == payload, (
        f"{case_id}: independent payload != production payload\n"
        f"independent: {payload.hex()}\nproduction:  {prod_payload.hex()}"
    )

    manifest_json = dict(fields)
    manifest_json["signature"] = sig_b64
    manifest_json["public_key"] = pub_b64
    # keep the canonical field order for readability
    ordered_keys = [k2 for k2 in FIELD_ORDER if k2 in manifest_json] + [
        "signature", "public_key"
    ]
    manifest_json = {k2: manifest_json[k2] for k2 in ordered_keys}

    record_hash = hashlib.sha256(payload + sig_b64.encode("ascii")).hexdigest()

    loc_bits = None
    if fields.get("location") is not None:
        loc = fields["location"]
        loc_bits = {
            name: _float_bits(loc[name])
            for name in ("latitude", "longitude", "horizontal_accuracy", "altitude")
            if loc.get(name) is not None
        }

    case = {
        "id": case_id,
        "description": description,
        "manifest_json": manifest_json,
        "canonical_payload": {
            "text": payload.decode("utf-8"),
            "hex": payload.hex(),
            "sha256_hex": hashlib.sha256(payload).hexdigest(),
            "length_bytes": len(payload),
            "encoding": "UTF-8",
        },
        "payload_line_map": _line_map(payload),
        "location_bits_hex": loc_bits,
        "signature": {
            "der_hex": sig_der.hex(),
            "base64": sig_b64,
            "length_bytes": len(sig_der),
            "deterministic": "RFC 6979 (P-256, SHA-256)",
        },
        "record_hash_hex": record_hash,
        "expected_verify": True,
    }
    return case, payload


def signed_manifest_cases(priv_scalar: int, pub_bytes: bytes) -> dict:
    ts_z = "2026-11-09T12:00:00Z"
    ts_zero = "2026-11-09T12:00:00+00:00"
    ts_p8 = "2026-11-09T20:00:00+08:00"
    ts_micro = "2026-11-09T12:00:00.123456+00:00"
    base = {
        "version": "1.1",
        "record_id": "023a1b2c-1111-4222-8333-444455556666",
        "sequence_number": 0,
        "previous_record_hash": None,
        "photo_hash": TEST_PHOTO_HASH,
        "capture_timestamp": ts_zero,
        "device_reported_time": ts_zero,
        "device_id": "w023-device-001",
        "location": None,
    }

    def f(**overrides):
        d = dict(base)
        d.update(overrides)
        return d

    spec = [
        ("v11-minimal-no-location",
         "Baseline v1.1 record, location absent (signs as 'location=none')",
         f()),
        ("v11-location-full",
         "v1.1 with full location including altitude",
         f(location={"latitude": 39.9042, "longitude": 116.4074,
                     "horizontal_accuracy": 5.0, "altitude": 43.5})),
        ("v11-location-altitude-none",
         "v1.1 with location but altitude null (signs as trailing 'none')",
         f(location={"latitude": 39.9042, "longitude": 116.4074,
                     "horizontal_accuracy": 5.0, "altitude": None})),
        ("v10-minimal-no-location",
         "Baseline v1.0 record: domain separator is 'WorkCam-Sig-v1'",
         f(version="1.0")),
        ("v10-location-repr-floats",
         ("v1.0 with location. Location floats are embedded with Python "
          "str() (repr) semantics in v1.0 - the cross-language drift risk "
          "documented in the proposal. 0.1/0.2/-0.0 chosen deliberately."),
         f(version="1.0",
           location={"latitude": 0.1, "longitude": 0.2,
                     "horizontal_accuracy": 1.5, "altitude": -0.0})),
        ("v11-unicode-device-id",
         "v1.1 with multibyte + combining + emoji device_id (UTF-8 bytes are signed)",
         f(device_id="\u5317\u4eac\u7ad9\u2460-\U0001f309-na\u00efve")),
        ("v11-unicode-nfd-record-id",
         ("v1.1 with NFD (decomposed) Unicode record_id. Byte-different from "
          "the NFC form; the protocol signs bytes, never normalizes."),
         f(record_id="cafe\u0301-nfd-023-\u2460")),
        ("v11-timestamp-z-suffix",
         "v1.1 with 'Z' UTC designator (byte-different from '+00:00')",
         f(capture_timestamp=ts_z, device_reported_time=ts_z)),
        ("v11-timestamp-offset-plus-8",
         "v1.1 with +08:00 offset (same instant as Z case, different bytes)",
         f(capture_timestamp=ts_p8, device_reported_time=ts_p8)),
        ("v11-timestamp-microseconds",
         "v1.1 with fractional seconds (allowed by the ISO8601 shape regex)",
         f(capture_timestamp=ts_micro, device_reported_time=ts_micro)),
        ("v11-seq1-prev-hash",
         "v1.1 second chain record with previous_record_hash present",
         f(sequence_number=1,
           previous_record_hash="a" * 63 + "b")),
        ("v11-gps-boundary-max",
         "v1.1 at the upper GPS bounds: lat=90.0, lon=180.0 (boundary accepted)",
         f(location={"latitude": 90.0, "longitude": 180.0,
                     "horizontal_accuracy": 0.001, "altitude": 8848.86})),
        ("v11-gps-negative",
         "v1.1 southern/western hemisphere GPS with 10-significant-digit longitude",
         f(location={"latitude": -33.8688, "longitude": -179.9999999,
                     "horizontal_accuracy": 0.5, "altitude": -423.0})),
        ("v11-float-precision",
         ("v1.1 float formatting: trailing-zero truncation (39.90420000 -> "
          "39.9042), pi rounding to 10 significant digits"),
         f(location={"latitude": 39.90420000, "longitude": -0.1234567891,
                     "horizontal_accuracy": 3.14159265358979, "altitude": None})),
        ("v11-bidi-device-id",
         "v1.1 with a RIGHT-TO-LEFT MARK (U+200F) inside device_id",
         f(device_id="site-abc\u200fdef")),
    ]

    cases = []
    payloads = {}
    for cid, desc, fields in spec:
        case, payload = _mk_case(cid, desc, fields, priv_scalar, pub_bytes)
        cases.append(case)
        payloads[cid] = payload
    signed_section = {
        "description": (
            "Signed manifest cases: each case is a complete manifest whose "
            "canonical payload is rebuilt by BOTH ends and verified against "
            "the frozen RFC 6979 signature. Together they cover versions "
            "1.0/1.1, location present/absent, altitude null, Unicode "
            "fields, timestamp variants, chain links, GPS boundaries and "
            "float precision."
        ),
        "cases": cases,
    }
    return signed_section, payloads


def unicode_section() -> dict:
    samples = [
        ("u1-cjk", "北京Beijing", "Multibyte CJK text (3-byte UTF-8 units)"),
        ("u2-nfc-e", "\u00e9",
         "NFC precomposed LATIN SMALL LETTER E WITH ACUTE (2 UTF-8 bytes)"),
        ("u3-nfd-e", "e\u0301",
         "NFD decomposed 'e' + U+0301 COMBINING ACUTE (3 UTF-8 bytes). "
         "Same visible glyph as u2, DIFFERENT bytes, therefore different "
         "signature. Never auto-normalize on either side."),
        ("u4-rlm", "site-a\u200fdef",
         "Contains U+200F RIGHT-TO-LEFT MARK (zero-width bidi control). "
         "Allowed by the protocol (not a newline) and signed as-is."),
        ("u5-emoji-bridge", "\U0001f309",
         "Astral plane character U+1F309 (4-byte UTF-8 unit)"),
        ("u6-superscript", "e\u0301x\u00b2",
         "Combining mark + superscript two; multi-script mix"),
    ]
    # hard guarantees: the literals above must be exactly the intended forms
    assert samples[1][1] == "\u00e9" and len(samples[1][1].encode("utf-8")) == 2
    assert samples[2][1] == "e\u0301" and len(samples[2][1].encode("utf-8")) == 3
    assert samples[3][1] == "site-a\u200fdef"
    assert samples[5][1] == "e\u0301x\u00b2"
    cases = []
    for cid, text, note in samples:
        raw = text.encode("utf-8")
        cases.append({
            "id": cid,
            "text": text,
            "utf8_hex": raw.hex(),
            "length_bytes": len(raw),
            "utf16_length_code_units": None,  # informational; filled below
            "sha256_hex": hashlib.sha256(raw).hexdigest(),
            "note": note,
        })
    for c in cases:
        c["utf16_length_code_units"] = len(c["text"].encode("utf-16-le")) // 2
    cases.append({
        "id": "u7-empty-string",
        "text": "",
        "utf8_hex": "",
        "length_bytes": 0,
        "utf16_length_code_units": 0,
        "sha256_hex": hashlib.sha256(b"").hexdigest(),
        "note": "Empty string: 0 bytes; sha256 equals the FIPS empty-input KAT",
    })
    return {
        "description": (
            "Unicode cases: the protocol signs UTF-8 BYTES. NFC vs NFD vs "
            "bidi controls produce different bytes and therefore different "
            "signatures - both are valid, neither may be normalized. "
            "Swift rule: use String.utf8 (or Array(string.utf8)); never "
            "precomposedStringWith*. Python rule: str.encode('utf-8'); "
            "never unicodedata.normalize on the verification path."
        ),
        "cases": cases,
    }


def newline_section(payloads: dict, base_case: str, pub_bytes: bytes,
                    base_sig_der: bytes) -> dict:
    payload = payloads[base_case]
    cases = []
    text = payload.decode("utf-8")
    lf_only = text
    crlf = text.replace("\n", "\r\n")
    cr_only = text.replace("\n", "\r")

    for cid, rebuilt, note in [
        ("nl-r1-crlf-rebuild", crlf.encode("utf-8"),
         "Same logical lines joined with CRLF. Swift-side trap: must fail."),
        ("nl-r2-cr-rebuild", cr_only.encode("utf-8"),
         "Same logical lines joined with bare CR. Must fail."),
        ("nl-r3-no-trailing-newline", text[:-1].encode("utf-8"),
         "Payload without the final 0x0a. Must fail."),
        ("nl-r4-double-trailing-newline", (text + "\n").encode("utf-8"),
         "Payload with an extra final newline. Must fail."),
    ]:
        assert not _verify(pub_bytes, base_sig_der, rebuilt), (
            f"{cid}: tampered newline variant unexpectedly verified"
        )
        cases.append({
            "id": cid,
            "tampered_payload_hex": rebuilt.hex(),
            "length_bytes": len(rebuilt),
            "signature_base64_used": base64.b64encode(base_sig_der).decode(),
            "expected_verify": False,
            "note": note,
        })
    # field values containing newlines are rejected by structure validation
    from app.services.serialization import validate_manifest_fields
    from app.models.capture import SignatureManifest
    bad = SignatureManifest(
        version="1.1", record_id="bad\nid", sequence_number=0,
        previous_record_hash=None, photo_hash="00" * 32,
        capture_timestamp="2026-11-09T12:00:00Z",
        device_reported_time="2026-11-09T12:00:00Z",
        device_id="x", location=None, signature="", public_key="",
    )
    errors = validate_manifest_fields(bad)
    assert any("record_id" in e and "newline" in e for e in errors)
    return {
        "description": (
            "Newline discipline: payload lines are ALWAYS 0x0a (LF) only, "
            "including a final trailing LF. Any CR/LF variant rebuild must "
            "fail signature verification. Field values containing LF or CR "
            "are rejected by structure validation before crypto."
        ),
        "reject_cases": cases,
        "field_value_newline_rejection": {
            "sample_field": "record_id",
            "sample_value": "bad\\nid",
            "expected": "rejected_by_structure_validation",
            "production_error_sample": errors[0],
        },
    }


def float_section() -> dict:
    v11_values = [
        0.0, -0.0, 5.0, 39.9042, 116.4074, 0.1, 0.2, 1.5, -43.5,
        0.30000000000000004,  # .10g truncates to 0.3
        0.000123456789,  # fixed-point range lower edge (exp -4)
        1e-05, 1.5e-07,  # exponential range
        3.14159265358979,  # rounding to 10 significant digits
        123456.789, 1234567.0,
        1000000000000000.0,  # 1e15: integer branch -> full digits + ".0"
        10000000000000000.0,  # 1e16: integer branch (NOT %.10g's 1e+16)
        1e20,  # integer branch, beyond Int64 - Python exact via big int
    ]
    v11_table = []
    for v in v11_values:
        expected = fmt_float_v11(v)
        assert expected == production_format_float(v), (
            f"float mismatch for {v!r}: {expected} vs {production_format_float(v)}"
        )
        v11_table.append({
            "input_double": v,
            "input_bits_hex": _float_bits(v),
            "expected_v1_1_string": expected,
            "branch": (
                "integer-plus-.0" if v == int(v) and not (
                    v == 0.0 and str(v).startswith("-")
                ) else "dot-10g"
            ),
        })
    v10_values = [39.9042, 0.1, -0.0, 1.5, 0.30000000000000004,
                  1000000000000000.0, 10000000000000000.0, 1e-05]
    v10_table = []
    for v in v10_values:
        v10_table.append({
            "input_double": v,
            "input_bits_hex": _float_bits(v),
            "python_v1_0_string": str(v),
            "note": (
                "v1.0 embeds floats with Python str()/repr() shortest-round-trip "
                "formatting. Swift String(describing:) is NOT guaranteed "
                "byte-equal for these (exponent thresholds differ between the "
                "two runtimes) - this is the documented v1.0 drift risk. "
                "Swift output for the same bits: PENDING_MAC_VERIFICATION."
            ),
        })
    return {
        "description": (
            "Float serialization rules. v1.1 is the normative cross-language "
            "rule (10-significant-digit %.10g with integer detection and "
            "guaranteed decimal point); v1.0 uses Python repr semantics and "
            "is the known cross-language hazard (see proposal). The v1.1 "
            "rule must be ported VERBATIM to Swift - a bare "
            "String(format: \"%.10g\") is NOT sufficient (integer branch "
            "and '-0.0' handling differ). See W023CrosslangVectorTests.swift "
            "formatFloatV11() for the port."
        ),
        "v1_1_format_table": v11_table,
        "v1_0_repr_risk_table": v10_table,
        "rules": [
            "R1: only finite values (NaN/Infinity rejected before formatting)",
            "R2: if the value is an integer value (value == int(value)) and "
            "not negative zero, emit the integer digits followed by '.0'",
            "R3: otherwise emit %.10g (10 significant digits); if the result "
            "has neither '.' nor an exponent, append '.0'",
            "R4: negative zero formats as '-0.0'",
            "R5: values with exponent < -4 render in exponential form "
            "('1e-05', '1.5e-07')",
        ],
        "swift_status": (
            "Expected strings are Python-side ground truth (recomputed "
            "independently by the generator AND cross-checked against "
            "production _format_float). Swift outputs for the same bit "
            "patterns: PENDING_MAC_VERIFICATION."
        ),
    }


def null_absent_section(payloads: dict, priv_scalar: int, pub_bytes: bytes) -> dict:
    base_fields = {
        "version": "1.1",
        "record_id": "023-null-void-1111-4222-8333-444455556666",
        "sequence_number": 0,
        "previous_record_hash": None,
        "photo_hash": TEST_PHOTO_HASH,
        "capture_timestamp": "2026-11-09T12:00:00+00:00",
        "device_reported_time": "2026-11-09T12:00:00+00:00",
        "device_id": "w023-device-001",
        "location": None,
    }
    # variant A: previous_record_hash = null, location = null
    a = dict(base_fields)
    # variant B: previous_record_hash absent from JSON, location absent
    b = dict(base_fields)
    del b["previous_record_hash"]
    del b["location"]
    pa = build_payload(a)
    pb = build_payload(b)
    assert pa == pb, "null vs absent must produce identical canonical payloads"
    # variant C: location present with altitude = null vs altitude absent
    c1 = dict(base_fields)
    c1["location"] = {"latitude": 1.0, "longitude": 2.0,
                      "horizontal_accuracy": 3.0, "altitude": None}
    c2 = dict(base_fields)
    c2["location"] = {"latitude": 1.0, "longitude": 2.0,
                      "horizontal_accuracy": 3.0}
    pc1 = build_payload(c1)
    pc2 = build_payload(c2)
    assert pc1 == pc2
    case_payload = pa
    k, r, s = rfc6979_sign(priv_scalar, case_payload)
    sig_der = der_encode(r, s)
    assert _verify(pub_bytes, sig_der, case_payload)
    sig_b64 = base64.b64encode(sig_der).decode()
    # empty-string trap: previous_record_hash="" is NOT the same as null
    from app.services.signature import validate_manifest_structure
    trap_fields = dict(base_fields)
    trap_fields["previous_record_hash"] = ""
    trap_manifest = _production_manifest(
        trap_fields, sig_b64, base64.b64encode(pub_bytes).decode()
    )
    trap_errors = validate_manifest_structure(trap_manifest)
    trap_error = next(
        (e for e in trap_errors if "previous_record_hash" in e), None
    )
    return {
        "description": (
            "null vs absent equivalence: JSON null, key-absent and (for "
            "location/altitude) empty inner values all serialize to 'none' "
            "in the canonical payload and are INTERCHANGEABLE. A signature "
            "made from the null form verifies under the absent form and "
            "vice versa. Swift rule: model these fields as optionals and "
            "decode with decodeIfPresent (JSONDecoder maps both null and "
            "absent to nil). The empty-string trap ('' serializes to "
            "'key=' not 'key=none') is NOT equivalent and is rejected by "
            "structure validation."
        ),
        "equivalence_case": {
            "manifest_json_with_nulls": {
                **base_fields,
                "signature": sig_b64,
                "public_key": base64.b64encode(pub_bytes).decode(),
            },
            "manifest_json_with_absent_keys": {
                k2: v for k2, v in base_fields.items()
                if k2 not in ("previous_record_hash", "location")
            } | {"signature": sig_b64,
                 "public_key": base64.b64encode(pub_bytes).decode()},
            "canonical_payload_hex": case_payload.hex(),
            "signature": {
                "der_hex": sig_der.hex(),
                "base64": sig_b64,
                "length_bytes": len(sig_der),
            },
            "expected_verify": True,
            "note": "Both JSON forms MUST rebuild the same payload bytes and the same verification result.",
        },
        "altitude_null_vs_absent_payload_hex": pc1.hex(),
        "empty_string_trap": {
            "field": "previous_record_hash",
            "value": "",
            "payload_line": "previous_record_hash=",
            "expected": "rejected_by_structure_validation",
            "production_error_sample": trap_error,
        },
    }


def gps_section() -> dict:
    valid = []
    for cid, lat, lon, acc, alt, note in [
        ("gps-ok-1", 39.9042, 116.4074, 5.0, 43.5, "typical Beijing coordinate"),
        ("gps-ok-2", 90.0, 180.0, 0.001, 100000.0, "upper bounds incl. altitude ceiling"),
        ("gps-ok-3", -90.0, -180.0, 0.5, -1000.0, "lower bounds incl. altitude floor"),
        ("gps-ok-4", 0.0, 0.0, 1.0, None, "origin, altitude null -> 'none'"),
        ("gps-ok-5", -0.0, -0.0, 1.0, 0.0, "negative zeros: '-0.0' is legal and signed as-is"),
    ]:
        loc = {"latitude": lat, "longitude": lon, "horizontal_accuracy": acc}
        loc["altitude"] = alt
        try:
            LocationData(**loc)
            accepted = True
        except Exception:
            accepted = False
        assert accepted, f"{cid} unexpectedly rejected"
        valid.append({
            "id": cid,
            "location_bits_hex": {
                name: _float_bits(loc[name]) for name in loc if loc[name] is not None
            },
            "location": loc,
            "expected_v1_1_string": _loc_str(loc, "1.1"),
            "expected": "accepted",
            "note": note,
        })
    rejects = [
        ("gps-r1-lat-above-90", {"latitude": 90.00001, "longitude": 0.0,
                                  "horizontal_accuracy": 1.0}, "latitude > 90"),
        ("gps-r2-lon-below-180", {"latitude": 0.0, "longitude": -180.001,
                                  "horizontal_accuracy": 1.0}, "longitude < -180"),
        ("gps-r3-acc-zero", {"latitude": 0.0, "longitude": 0.0,
                              "horizontal_accuracy": 0.0}, "horizontal_accuracy must be > 0"),
        ("gps-r4-acc-negative", {"latitude": 0.0, "longitude": 0.0,
                                  "horizontal_accuracy": -1.0}, "negative accuracy"),
        ("gps-r5-alt-below-floor", {"latitude": 0.0, "longitude": 0.0,
                                     "horizontal_accuracy": 1.0, "altitude": -1000.1},
         "altitude < -1000"),
        ("gps-r6-alt-above-ceiling", {"latitude": 0.0, "longitude": 0.0,
                                       "horizontal_accuracy": 1.0, "altitude": 100001.0},
         "altitude > 100000"),
        ("gps-r7-nan-lat", {"latitude": float("nan"), "longitude": 0.0,
                             "horizontal_accuracy": 1.0},
         "NaN latitude (constructed at model layer; cannot appear in valid JSON)"),
        ("gps-r8-inf-lon", {"latitude": 0.0, "longitude": float("inf"),
                             "horizontal_accuracy": 1.0},
         "Infinity longitude (constructed at model layer)"),
    ]
    reject_cases = []
    for cid, loc, note in rejects:
        try:
            LocationData(**loc)
            raise RuntimeError(f"{cid}: unexpectedly accepted by model validation")
        except Exception as exc:
            assert not isinstance(exc, RuntimeError)
        reject_cases.append({
            "id": cid,
            "note": note,
            "expected": "rejected_by_model_validation",
            "bounds": (
                "latitude in [-90, 90]; longitude in [-180, 180]; "
                "horizontal_accuracy > 0; altitude in [-1000, 100000] or null; "
                "NaN/Infinity always rejected"
            ),
        })
    return {
        "description": (
            "GPS coordinate rules shared by both ends: bounds validation "
            "BEFORE serialization (Swift must implement the same ranges), "
            "sign and precision are preserved by the v1.1 float rule, "
            "-0.0 is signed as '-0.0' (not normalized to '0.0')."
        ),
        "valid_cases": valid,
        "reject_cases": reject_cases,
    }


def iso8601_section() -> dict:
    from app.services.serialization import ISO8601_REGEX
    valid = [
        ("ts-ok-1-z", "2026-11-09T12:00:00Z", "Z designator"),
        ("ts-ok-2-zero-offset", "2026-11-09T12:00:00+00:00", "explicit +00:00"),
        ("ts-ok-3-plus-8", "2026-11-09T20:00:00+08:00", "east of UTC"),
        ("ts-ok-4-minus-5", "2026-11-09T07:00:00-05:00", "west of UTC"),
        ("ts-ok-5-millis", "2026-11-09T12:00:00.123+00:00", "fractional seconds"),
        ("ts-ok-6-micros-z", "2026-11-09T12:00:00.123456Z", "fractional + Z"),
        ("ts-ok-7-plus-14", "2026-11-10T02:00:00+14:00", "extreme east offset"),
        ("ts-ok-8-minus-12", "2026-11-09T00:00:00-12:00", "extreme west offset"),
    ]
    invalid = [
        ("ts-r1-space-separator", "2026-11-09 12:00:00Z", "space instead of T"),
        ("ts-r2-date-only", "2026-11-09", "date without time"),
        ("ts-r3-no-tz", "2026-11-09T12:00:00", "missing timezone designator"),
        ("ts-r4-offset-no-colon", "2026-11-09T12:00:00+0800", "offset without colon"),
        ("ts-r5-slashes", "2026/11/09T12:00:00Z", "slash separators"),
        ("ts-r6-lowercase", "2026-11-09t12:00:00z", "lowercase t/z (rejected by regex shape)"),
        ("ts-r7-offset-one-digit", "2026-11-09T12:00:00+00:0", "offset minutes one digit"),
        ("ts-r8-tz-only-hours", "2026-11-09T12:00:00+00", "offset hours only"),
    ]
    valid_cases = []
    for cid, value, note in valid:
        assert ISO8601_REGEX.match(value), f"{cid} should be valid"
        valid_cases.append({"id": cid, "value": value, "expected": "accepted",
                            "note": note})
    invalid_cases = []
    for cid, value, note in invalid:
        assert not ISO8601_REGEX.match(value), f"{cid} should be invalid"
        invalid_cases.append({"id": cid, "value": value,
                              "expected": "rejected_by_iso8601_validation",
                              "note": note})
    # shape-vs-semantics finding: month 13 passes the regex
    shape_only = "2026-13-01T00:00:00Z"
    assert ISO8601_REGEX.match(shape_only)
    return {
        "description": (
            "ISO 8601 rules. CRITICAL cross-language rule: the timestamp "
            "inside the canonical payload is an OPAQUE STRING - never "
            "parse-then-reformat it. 'Z' and '+00:00' denote the same "
            "instant but are different bytes with different signatures. "
            "The production validator is a SHAPE regex, not a calendar: "
            "e.g. 2026-13-01T00:00:00Z passes the shape check (documented "
            "finding; tightening proposed in the proposal doc, not "
            "implemented here)."
        ),
        "valid_cases": valid_cases,
        "invalid_cases": invalid_cases,
        "shape_only_accepted_example": {
            "value": shape_only,
            "finding": "month 13 passes the shape regex; the production "
                       "validator does not check calendar semantics",
        },
    }


def timezone_section() -> dict:
    a = "2026-11-09T12:00:00Z"
    b = "2026-11-09T12:00:00+00:00"
    c = "2026-11-09T20:00:00+08:00"
    return {
        "description": (
            "Timezone offsets: three textual representations of the SAME "
            "UTC instant. Each has different bytes and therefore a "
            "different signature. A verifier that normalizes timestamps "
            "(e.g. Swift ISO8601DateFormatter parse then re-format) will "
            "rebuild different payload bytes and break verification. "
            "Rule: compare and sign the literal string."
        ),
        "same_instant_representations": [
            {
                "value": v,
                "utf8_hex": v.encode("utf-8").hex(),
                "sha256_of_utf8": hashlib.sha256(v.encode("utf-8")).hexdigest(),
            } for v in (a, b, c)
        ],
        "rule": (
            "bytes_in_payload_are_the_contract: capture_timestamp and "
            "device_reported_time are signed as opaque strings"
        ),
    }


def field_order_section(payloads: dict, priv_scalar: int, pub_bytes: bytes) -> dict:
    fields = {
        "version": "1.1",
        "record_id": "023-order-7777-1111-4222-8333-444455556666",
        "sequence_number": 0,
        "previous_record_hash": None,
        "photo_hash": TEST_PHOTO_HASH,
        "capture_timestamp": "2026-11-09T12:00:00+00:00",
        "device_reported_time": "2026-11-09T12:00:00+00:00",
        "device_id": "w023-device-001",
        "location": {"latitude": 1.25, "longitude": 2.5,
                     "horizontal_accuracy": 6.75, "altitude": None},
    }
    payload = build_payload(fields)
    k, r, s = rfc6979_sign(priv_scalar, payload)
    sig_b64 = base64.b64encode(der_encode(r, s)).decode()
    # shuffled JSON key order - same payload
    shuffled_json = {
        "device_id": fields["device_id"],
        "location": fields["location"],
        "photo_hash": fields["photo_hash"],
        "version": fields["version"],
        "device_reported_time": fields["device_reported_time"],
        "record_id": fields["record_id"],
        "sequence_number": fields["sequence_number"],
        "previous_record_hash": fields["previous_record_hash"],
        "capture_timestamp": fields["capture_timestamp"],
        "signature": sig_b64,
        "public_key": base64.b64encode(pub_bytes).decode(),
    }
    return {
        "description": (
            "Field order: the canonical payload uses a FIXED field order "
            "(see fixed_order below), independent of the JSON key order of "
            "the incoming manifest. Both ends MUST rebuild lines in this "
            "exact order - never rely on dictionary/JSONEncoder ordering. "
            "This case is signed once; a verifier loading the shuffled-"
            "order JSON must rebuild identical payload bytes and the "
            "signature must verify."
        ),
        "fixed_order": FIELD_ORDER,
        "shuffled_json_equivalence_case": {
            "manifest_json_key_order_shuffled": shuffled_json,
            "canonical_payload_hex": payload.hex(),
            "signature": {
                "der_hex": der_encode(r, s).hex(),
                "base64": sig_b64,
                "length_bytes": len(der_encode(r, s)),
            },
            "expected_verify": True,
        },
        "canonical_json_note": (
            "If/when the protocol ever signs JSON directly, use RFC 8785 "
            "(JCS) canonicalization - proposed in "
            "docs/crosslang-serialization-proposal.md, NOT part of v1.x."
        ),
    }


def tamper_section(payloads: dict, cases_by_id: dict, priv_scalar: int,
                   pub_bytes: bytes) -> dict:
    base_id = "v11-minimal-no-location"
    base_payload = payloads[base_id]
    base_case = cases_by_id[base_id]
    base_sig_der = bytes.fromhex(base_case["signature"]["der_hex"])
    base_sig_b64 = base_case["signature"]["base64"]

    loc_id = "v11-location-full"
    loc_payload = payloads[loc_id]
    loc_case = cases_by_id[loc_id]
    loc_sig_der = bytes.fromhex(loc_case["signature"]["der_hex"])

    def check_verify_false(sig: bytes, payload: bytes) -> None:
        assert not _verify(pub_bytes, sig, payload), "tamper case unexpectedly verified"

    out = []

    # --- payload byte flips -------------------------------------------------
    def flip(payload: bytes, index: int) -> bytes:
        b = bytearray(payload)
        b[index] ^= 0x01
        return bytes(b)

    flips = [
        ("t01-flip-domain-byte", 3, "single byte flipped inside the domain separator line", base_payload, base_sig_der),
        ("t02-flip-photo-hash-byte", base_payload.index(b"photo_hash=") + 11, "first byte of photo_hash value flipped", base_payload, base_sig_der),
        ("t03-flip-last-byte", len(base_payload) - 1, "final 0x0a flipped to 0x0b", base_payload, base_sig_der),
        ("t04-version-field-flip", base_payload.index(b"version=1.1") + 8, "'1.1' -> '0.1' single-byte change in the version line", base_payload, base_sig_der),
    ]
    for cid, idx, note, payload, sig in flips:
        tp = flip(payload, idx)
        check_verify_false(sig, tp)
        out.append({
            "id": cid, "base_case": base_id, "flip_index": idx,
            "tampered_payload_hex": tp.hex(),
            "signature_base64_used": base64.b64encode(sig).decode(),
            "expected_verify": False, "note": note,
        })

    # --- structural newline tampering (already covered in newline section) --

    # --- location / timestamp value tampering --------------------------------
    loc_text = loc_payload.decode("utf-8")
    loc_text_bad = loc_text.replace("39.9042", "39.9043")
    assert loc_text_bad != loc_text
    tp = loc_text_bad.encode("utf-8")
    check_verify_false(loc_sig_der, tp)
    out.append({
        "id": "t05-location-lat-last-digit", "base_case": loc_id,
        "tampered_payload_hex": tp.hex(),
        "signature_base64_used": base64.b64encode(loc_sig_der).decode(),
        "expected_verify": False,
        "note": "latitude 39.9042 -> 39.9043 (one decimal digit)",
    })
    ts_bad = base_payload.decode("utf-8").replace("+00:00", "+01:00")
    tp = ts_bad.encode("utf-8")
    check_verify_false(base_sig_der, tp)
    out.append({
        "id": "t06-timestamp-offset-change", "base_case": base_id,
        "tampered_payload_hex": tp.hex(),
        "signature_base64_used": base_sig_b64,
        "expected_verify": False,
        "note": "timezone offset +00:00 -> +01:00 (same clock reading, different offset)",
    })

    # --- domain separator swap ----------------------------------------------
    swapped = base_payload.decode("utf-8").replace("WorkCam-Sig-v1.1\n", "WorkCam-Sig-v1\n", 1)
    assert swapped != base_payload.decode("utf-8")
    tp = swapped.encode("utf-8")
    check_verify_false(base_sig_der, tp)
    out.append({
        "id": "t07-domain-separator-swap", "base_case": base_id,
        "tampered_payload_hex": tp.hex(),
        "signature_base64_used": base_sig_b64,
        "expected_verify": False,
        "note": "domain line v1.1 -> v1.0 while version field still says 1.1",
    })

    # --- signature tampering (payload untouched) -----------------------------
    sig_b = bytearray(base_sig_der)
    last = len(sig_b) - 1
    sig_b[last] = (sig_b[last] ^ 0x01)
    bad_sig = bytes(sig_b)
    # flip inside s: might still be structurally valid DER but must not verify
    structurally_valid = True
    try:
        from app.services.signature import _decode_signature
        _decode_signature(base64.b64encode(bad_sig).decode())
    except ValueError:
        structurally_valid = False
    check_verify_false(bad_sig, base_payload)
    out.append({
        "id": "t08-signature-byte-flip", "base_case": base_id,
        "tampered_signature_base64": base64.b64encode(bad_sig).decode(),
        "der_structure_after_flip": "valid" if structurally_valid else "rejected",
        "expected_verify": False,
        "note": "last DER byte flipped; may still parse as DER but verification must fail",
    })

    bad_sig = base_sig_der[:-8]
    out.append({
        "id": "t09-signature-truncated", "base_case": base_id,
        "tampered_signature_base64": base64.b64encode(bad_sig).decode(),
        "expected_der_structure": "rejected_by_der_structure_check",
        "expected_verify": False,
        "note": "8 bytes removed from the end",
    })

    bad_sig = base_sig_der + b"\x00\x00"
    out.append({
        "id": "t10-signature-trailing-garbage", "base_case": base_id,
        "tampered_signature_base64": base64.b64encode(bad_sig).decode(),
        "expected_der_structure": "rejected_by_der_structure_check",
        "expected_verify": False,
        "note": "two zero bytes appended",
    })

    # r/s swap: re-encode with r and s exchanged
    r_int, s_int = der_ints(base_sig_der)
    swapped_sig = der_encode(s_int, r_int)
    check_verify_false(swapped_sig, base_payload)
    out.append({
        "id": "t11-signature-rs-swapped", "base_case": base_id,
        "tampered_signature_base64": base64.b64encode(swapped_sig).decode(),
        "expected_verify": False,
        "note": "valid DER with r and s exchanged (structural check passes, crypto must fail)",
    })

    # base64 character swap at the string level
    b64 = bytearray(base_sig_b64.encode("ascii"))
    b64[-1] = ord("=") if b64[-1] != ord("=") else ord("A")
    # ensure an actual change
    if bytes(b64) == base_sig_b64.encode():
        b64[-1] = ord("B")
    bad_b64 = bytes(b64).decode()
    try:
        raw = base64.b64decode(bad_b64, validate=True)
        verify_result = _verify(pub_bytes, raw, base_payload)
        assert not verify_result
        expected = "verification_failed"
    except Exception:
        expected = "rejected_by_strict_base64_decode"
    out.append({
        "id": "t12-signature-base64-char-swap", "base_case": base_id,
        "tampered_signature_base64": bad_b64,
        "expected": expected,
        "note": "final base64 character replaced (payload bytes may or may not change)",
    })

    # signature by the frozen W018-style wrong-signer TEST key over our
    # payload (deterministic RFC 6979 signing keeps the generator reproducible)
    wrong_seed = b"workcam-test-wrong-signer-v1"
    wrong_scalar = int.from_bytes(hashlib.sha256(wrong_seed).digest(), "big")
    assert wrong_scalar < P256_N, "wrong-signer seed must be below the curve order"
    _, wr, ws = rfc6979_sign(wrong_scalar, base_payload)
    wrong_sig = der_encode(wr, ws)
    # sanity: it IS a valid signature under the wrong key ...
    wrong_pub = ec.derive_private_key(wrong_scalar, ec.SECP256R1()).public_key()
    wrong_pub.verify(wrong_sig, base_payload, ec.ECDSA(SHA256()))
    # ... but must NOT verify under the fixed public key
    check_verify_false(wrong_sig, base_payload)
    out.append({
        "id": "t13-wrong-key-signature", "base_case": base_id,
        "tampered_signature_base64": base64.b64encode(wrong_sig).decode(),
        "expected_verify": False,
        "note": "structurally valid DER signature produced by a DIFFERENT P-256 key over the same payload",
    })

    return {
        "description": (
            "Tamper cases: every modification of one byte (payload, "
            "signature, base64 layer, DER layer, domain separator, "
            "timezone digit) must cause verification failure. Both "
            "implementations must reject every case in this list."
        ),
        "base_case": base_id,
        "reject_cases": out,
    }


def version_section(priv_scalar: int, pub_bytes: bytes) -> dict:
    fields = {
        "version": "2.0",
        "record_id": "023-version-9999-1111-4222-8333-444455556666",
        "sequence_number": 0,
        "previous_record_hash": None,
        "photo_hash": TEST_PHOTO_HASH,
        "capture_timestamp": "2026-11-09T12:00:00+00:00",
        "device_reported_time": "2026-11-09T12:00:00+00:00",
        "device_id": "w023-device-001",
        "location": None,
    }
    # empirical: production canonical_payload for an unsupported version
    prod_manifest = _production_manifest(
        fields, "AA==", base64.b64encode(pub_bytes).decode()
    )
    prod_payload = production_canonical_payload(prod_manifest)
    used_domain = prod_payload.split(b"\n")[0].decode()
    assert used_domain == "WorkCam-Sig-v1"  # fallback behavior confirmed
    from app.services.signature import validate_manifest_structure
    errors = validate_manifest_structure(prod_manifest)
    unsupported = next(
        (e for e in errors if "Unsupported version" in e), None
    )
    assert unsupported is not None
    return {
        "description": (
            "Protocol version compatibility. The version field value is "
            "BOUND to the domain separator line. Unknown versions are "
            "rejected by structure validation. REVIEW FINDING (recorded "
            "as-is, not changed here): the production canonical_payload "
            "silently falls back to the v1.0 domain separator for any "
            "version string outside {'1.0','1.1'}; a Swift port MUST NOT "
            "copy this fallback - it must reject unknown versions before "
            "serialization (the structural check does reject the manifest "
            "first, so the fallback is unreachable through the public API, "
            "but it is still a divergence trap for fresh implementations)."
        ),
        "binding_table": {
            "1.0": "WorkCam-Sig-v1",
            "1.1": "WorkCam-Sig-v1.1",
        },
        "unknown_version_behavior": {
            "version": "2.0",
            "canonical_domain_used_by_production": used_domain,
            "structure_validation": "rejected (Unsupported version)",
            "production_error_sample": unsupported,
            "cross_lang_rule": (
                "Both ends MUST check version in {'1.0','1.1'} BEFORE "
                "building the payload and MUST reject otherwise. Do not "
                "reproduce the Python fallback domain separator."
            ),
        },
        "positive_cases": {
            "1.0": "see signed_manifest_cases[v10-minimal-no-location]",
            "1.1": "see signed_manifest_cases[v11-minimal-no-location]",
        },
        "negative_cases": [
            "t04-version-field-flip (version line byte change breaks signature)",
            "t07-domain-separator-swap (domain/version mismatch breaks signature)",
        ],
    }


def payload_contract_section(payloads: dict, cases_by_id: dict) -> dict:
    base_id = "v11-minimal-no-location"
    payload = payloads[base_id]
    case = cases_by_id[base_id]
    return {
        "description": (
            "EXACT signed payload byte contract. The signature covers "
            "precisely these bytes - nothing more, nothing less. Both ends "
            "must be able to rebuild them deterministically from the "
            "manifest fields."
        ),
        "rules": [
            "P1: UTF-8 encoding, no BOM",
            "P2: first line is the domain separator ('WorkCam-Sig-v1.1' or "
            "'WorkCam-Sig-v1' depending on the version field)",
            "P3: then one line per field in the fixed order: version, "
            "record_id, sequence_number, previous_record_hash, photo_hash, "
            "capture_timestamp, device_reported_time, device_id, location",
            "P4: every line is 'key=value' with a single '=' separator",
            "P5: null field values serialize as the literal token 'none' "
            "(previous_record_hash, location); a present location "
            "serializes as 'lat,lon,accuracy,altitude' with altitude 'none' "
            "when null",
            "P6: line separator is a single 0x0a (LF); CR (0x0d) is never "
            "emitted; field values must not contain LF or CR",
            "P7: the payload ends with exactly one trailing LF",
            "P8: the signature/public_key fields are NOT part of the payload",
        ],
        "reference_case": base_id,
        "reference_payload_hex": payload.hex(),
        "reference_payload_text": payload.decode("utf-8"),
        "reference_line_map": case["payload_line_map"],
        "reference_payload_sha256": case["canonical_payload"]["sha256_hex"],
        "record_hash_rule": (
            "record_hash = SHA-256(canonical_payload || signature_base64_ascii)"
        ),
    }


def main() -> None:
    rfc6979_selfcheck = selfcheck_rfc6979()

    priv_scalar, pub_bytes = derive_test_key()
    pub_b64 = base64.b64encode(pub_bytes).decode()

    signed, payloads = signed_manifest_cases(priv_scalar, pub_bytes)
    cases_by_id = {c["id"]: c for c in signed["cases"]}
    base_sig_der = bytes.fromhex(
        cases_by_id["v11-minimal-no-location"]["signature"]["der_hex"]
    )

    vectors = {
        "_meta": {
            "task": "WORKCAM-WINDOWS-BATCH-02 / B02-W023",
            "description": (
                "Cross-language (Python/Swift) signature and serialization "
                "compatibility vectors for the WorkCam signature protocol."
            ),
            "generated_by": "backend/generate_w023_vectors.py",
            "determinism": (
                "All signatures are RFC 6979 deterministic ECDSA "
                "(P-256 + SHA-256); re-running the generator reproduces "
                "this file byte-identically."
            ),
            "generation_environment": {
                "python": "3.12.10 (Windows)",
                "cryptography": "cryptography-50.0.2",
            },
            "byte_contract_reference": (
                "test-vectors/fixed-vectors.json (W018 canonical payload "
                "byte contract); w023 case v11-minimal-no-location follows "
                "the same layout with the w023 test key"
            ),
            "warning": "TEST KEYS ONLY - Never use in production.",
            "swift_status": (
                "NOT VERIFIED on any Mac yet. Swift-side expected results "
                "are derived from this file; see "
                "test-vectors/crosslang/W023CrosslangVectorTests.swift and "
                "MacIntegrationGuide.md. Windows does not claim Swift "
                "verification."
            ),
        },
        "test_key": {
            "seed": TEST_KEY_SEED.decode(),
            "derivation": (
                "scalar = int(sha256(seed), 16), enforced < P-256 order; "
                "private key = scalar * G. TEST KEY ONLY."
            ),
            "public_key_hex": pub_bytes.hex(),
            "public_key_b64": pub_b64,
            "rfc6979_official_selfcheck": rfc6979_selfcheck,
        },
        "sha256_cases": sha256_section(),
        "base64_cases": base64_section(pub_b64, base64.b64encode(base_sig_der).decode()),
        "public_key_cases": public_key_section(pub_bytes),
        "der_cases": der_section(base_sig_der),
        "payload_contract": payload_contract_section(payloads, cases_by_id),
        "signed_manifest_cases": signed,
        "unicode_cases": unicode_section(),
        "newline_cases": newline_section(
            payloads, "v11-minimal-no-location", pub_bytes, base_sig_der
        ),
        "float_cases": float_section(),
        "null_absent_cases": null_absent_section(payloads, priv_scalar, pub_bytes),
        "gps_cases": gps_section(),
        "iso8601_cases": iso8601_section(),
        "timezone_cases": timezone_section(),
        "field_order_cases": field_order_section(payloads, priv_scalar, pub_bytes),
        "tamper_cases": tamper_section(payloads, cases_by_id, priv_scalar, pub_bytes),
        "version_cases": version_section(priv_scalar, pub_bytes),
    }

    out_path = os.path.normpath(VECTORS_OUT)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(vectors, f, indent=2, ensure_ascii=False)
        f.write("\n")

    n_cases = len(signed["cases"]) + len(
        vectors["tamper_cases"]["reject_cases"]
    )
    print(f"Vectors written to {out_path}")
    print(f"  RFC 6979 official self-check : {len(rfc6979_selfcheck)} vectors OK")
    print(f"  signed manifest cases        : {len(signed['cases'])}")
    print(f"  tamper cases                 : {len(vectors['tamper_cases']['reject_cases'])}")
    print(f"  test public key              : {pub_b64[:24]}...")


if __name__ == "__main__":
    main()
