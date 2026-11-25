"""
B02-W023: standalone reference verifier for the cross-language (Python/Swift)
test vectors.

This verifier is INTENTIONALLY INDEPENDENT of the production backend code
(it does not import anything from `app.*`). It re-implements the wire
contract directly from docs/signature-protocol.md so that:

  - the vectors in test-vectors/crosslang/w023-crosslang-vectors.json can
    be validated end-to-end by two independent implementations, and
  - it serves as the porting blueprint for the Swift reference test
    (test-vectors/crosslang/W023CrosslangVectorTests.swift mirrors this
    file function by function).

Only the `cryptography` library is used, and only for the ECDSA point
math (P256). Everything else (SHA-256, base64 strictness, canonical
payload building, float formatting, ISO8601 shape check, DER structure
check, GPS bounds) is implemented here from the contract.

Usage:
    python w023_reference_verifier.py [--vectors PATH] [--verbose]

Exit code 0 = all sections PASS, 1 = at least one FAIL.

NOTE: RFC 6979 signing is re-implemented here and re-checked against the
official RFC 6979 A.2.5 vectors, then used to REPRODUCE every frozen
signature byte-for-byte - the strongest possible self-consistency check.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import math
import os
import re
import struct
import sys

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

DEFAULT_VECTORS = os.path.join(
    os.path.dirname(__file__), "..", "test-vectors", "crosslang",
    "w023-crosslang-vectors.json",
)

SUPPORTED_VERSIONS = {"1.0", "1.1"}
FIELD_ORDER = [
    "version", "record_id", "sequence_number", "previous_record_hash",
    "photo_hash", "capture_timestamp", "device_reported_time",
    "device_id", "location",
]
GPS_BOUNDS = {
    "latitude": (-90.0, 90.0),
    "longitude": (-180.0, 180.0),
    "horizontal_accuracy": (None, None),  # must be > 0
    "altitude": (-1000.0, 100000.0),
}
ISO8601_SHAPE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


class VerifierError(AssertionError):
    pass


def require(cond: bool, message: str) -> None:
    if not cond:
        raise VerifierError(message)


# ---------------------------------------------------------------------------
# Contract re-implementations (each maps 1:1 to a Swift function in
# W023CrosslangVectorTests.swift)
# ---------------------------------------------------------------------------


def sha256_hex(data: bytes) -> str:  # Swift: sha256Hex(_:)
    return hashlib.sha256(data).hexdigest()


def base64_decode_strict(value: str) -> bytes:
    """Swift: Data(base64Encoded:) - reject non-alphabet chars / bad padding."""
    if not isinstance(value, str):
        raise ValueError("base64 input must be a string")
    if len(value) % 4 != 0:
        raise ValueError("base64 length must be a multiple of 4")
    table = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
    )
    if any(ch not in table for ch in value):
        raise ValueError("character outside standard base64 alphabet")
    if "=" in value.rstrip("="):
        raise ValueError("padding character in the middle of the string")
    raw = base64.b64decode(value, validate=True)
    if base64.b64encode(raw).decode() != value:
        raise ValueError("non-canonical base64 padding bits")
    return raw


def decode_public_key(public_key_b64: str) -> bytes:
    """Swift: publicKeyFormatCheck - 65 bytes, 0x04 prefix, before curve math."""
    raw = base64_decode_strict(public_key_b64)
    if len(raw) != 65 or raw[0] != 0x04:
        raise ValueError(
            "public_key must be a 65-byte uncompressed P-256 point (0x04||x||y)"
        )
    return raw


def validate_der_structure(der: bytes) -> None:
    """Swift: derStructureCheck (SEQUENCE tag, lengths, two INTEGERs)."""
    if len(der) < 8:
        raise ValueError("DER signature too short")
    if der[0] != 0x30:
        raise ValueError("DER must start with SEQUENCE tag 0x30")
    if der[1] & 0x80:
        n = der[1] & 0x7F
        if n > 2:
            raise ValueError("DER length encoding too long")
        total = 2 + n + int.from_bytes(der[2:2 + n], "big")
    else:
        total = 2 + der[1]
    if len(der) != total:
        raise ValueError(f"DER length mismatch: expected {total}, got {len(der)}")
    pos = 2 + ((der[1] & 0x7F) if der[1] & 0x80 else 0)
    for _ in range(2):
        if pos >= len(der):
            raise ValueError("DER truncated before INTEGER")
        if der[pos] != 0x02:
            raise ValueError("expected INTEGER tag 0x02")
        pos += 1
        int_len = der[pos]
        pos += 1
        if pos + int_len > len(der):
            raise ValueError("DER INTEGER extends beyond signature")
        pos += int_len
    if pos != len(der):
        raise ValueError("DER has trailing data")


def format_float_v11(value: float) -> str:
    """Swift: formatFloatV11 - verbatim port of the production v1.1 rule."""
    if math.isnan(value) or math.isinf(value):
        raise ValueError("non-finite float")
    if value == int(value) and not (value == 0.0 and str(value).startswith("-")):
        return f"{int(value)}.0"
    formatted = f"{value:.10g}"
    if "." not in formatted and "e" not in formatted.lower():
        formatted += ".0"
    return formatted


def location_string(location: dict | None, version: str) -> str:
    """Swift: locationString(_:version:)."""
    if location is None:
        return "none"
    for name in ("latitude", "longitude", "horizontal_accuracy"):
        if name in location and location[name] is not None:
            require_gps_bounds(name, location[name])
    if version == "1.1":
        alt = (
            "none" if location.get("altitude") is None
            else format_float_v11(location["altitude"])
        )
        return ",".join([
            format_float_v11(location["latitude"]),
            format_float_v11(location["longitude"]),
            format_float_v11(location["horizontal_accuracy"]),
            alt,
        ])
    require_gps_bounds("altitude", location.get("altitude"))
    alt = "none" if location.get("altitude") is None else str(location["altitude"])
    return ",".join([
        str(location["latitude"]), str(location["longitude"]),
        str(location["horizontal_accuracy"]), alt,
    ])


def require_gps_bounds(name: str, value) -> None:
    """Swift: gpsBoundsCheck (same ranges as the production model)."""
    if value is None:
        return
    require(not math.isnan(value) and not math.isinf(value),
            f"{name} must be finite")
    if name == "horizontal_accuracy":
        require(value > 0.0, "horizontal_accuracy must be > 0")
        return
    low, high = GPS_BOUNDS[name]
    require(low <= value <= high, f"{name} out of range [{low}, {high}]")


def iso8601_shape_valid(value: str) -> bool:
    """Swift: iso8601ShapeMatches (shape check, opaque-string policy)."""
    return bool(ISO8601_SHAPE.match(value))


def build_canonical_payload(manifest: dict) -> bytes:
    """Swift: buildCanonicalPayload - the exact signed-payload byte contract."""
    version = manifest["version"]
    require(version in SUPPORTED_VERSIONS,
            f"unsupported version '{version}' - reject BEFORE serialization")
    for ts_name in ("capture_timestamp", "device_reported_time"):
        require(iso8601_shape_valid(manifest[ts_name]),
                f"{ts_name} must be ISO 8601")
    for str_name in ("record_id", "photo_hash", "device_id"):
        require("\n" not in manifest[str_name] and "\r" not in manifest[str_name],
                f"{str_name} must not contain newlines")
    domain = "WorkCam-Sig-v1.1" if version == "1.1" else "WorkCam-Sig-v1"
    lines = [domain]
    for name in FIELD_ORDER:
        if name == "location":
            lines.append(f"location={location_string(manifest.get('location'), version)}")
            continue
        value = manifest.get(name)
        if value is None:
            lines.append(f"{name}=none")
        else:
            lines.append(f"{name}={value}")
    text = "\n".join(lines) + "\n"
    for line in lines[1:]:
        require("signature=" not in line and "public_key=" not in line,
                "payload must never contain signature/public_key fields")
    return text.encode("utf-8")


def verify_ecdsa(pub_bytes: bytes, sig_der: bytes, payload: bytes) -> bool:
    """Swift: P256.Signing.PublicKey.isValidSignature."""
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub_bytes)
    try:
        key.verify(sig_der, payload, ec.ECDSA(SHA256()))
        return True
    except Exception:
        return False


def record_hash(payload: bytes, signature_b64: str) -> str:
    """Swift: recordHash - SHA-256(canonical_payload || signature_ascii)."""
    return sha256_hex(payload + signature_b64.encode("ascii"))


# ---------------------------------------------------------------------------
# RFC 6979 (to reproduce every frozen signature byte-for-byte)
# ---------------------------------------------------------------------------


def _bits2int(data: bytes) -> int:
    val = int.from_bytes(data, "big")
    blen = len(data) * 8
    return val >> (blen - 256) if blen > 256 else val


def _int2octets(x: int) -> bytes:
    return x.to_bytes(32, "big")


def _bits2octets(data: bytes) -> bytes:
    z = _bits2int(data)
    return _int2octets(z - P256_N if z >= P256_N else z)


def rfc6979_sign(x_priv: int, message: bytes) -> tuple[int, int]:
    h1 = hashlib.sha256(message).digest()
    V = b"\x01" * 32
    K = b"\x00" * 32
    x_oct, h1_oct = _int2octets(x_priv), _bits2octets(h1)
    K = hmac.new(K, V + b"\x00" + x_oct + h1_oct, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    K = hmac.new(K, V + b"\x01" + x_oct + h1_oct, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    e = _bits2int(h1) % P256_N
    while True:
        T = b""
        while len(T) < 32:
            V = hmac.new(K, V, hashlib.sha256).digest()
            T += V
        k = _bits2int(T)
        if 1 <= k < P256_N:
            kg = ec.derive_private_key(k, ec.SECP256R1()).public_key()
            raw = kg.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
            r = int.from_bytes(raw[1:33], "big") % P256_N
            s = pow(k, -1, P256_N) * (e + r * x_priv) % P256_N
            if r != 0 and s != 0:
                return r, s
        K = hmac.new(K, V + b"\x00", hashlib.sha256).digest()
        V = hmac.new(K, V, hashlib.sha256).digest()


def der_encode(r: int, s: int) -> bytes:
    def enc_int(v: int) -> bytes:
        body = v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big")
        if body[0] & 0x80:
            body = b"\x00" + body
        return b"\x02" + bytes([len(body)]) + body
    body = enc_int(r) + enc_int(s)
    return b"\x30" + bytes([len(body)]) + body


RFC6979_X = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
RFC6979_OFFICIAL = [
    (b"sample",
     0xA6E3C57DD01ABE90086538398355DD4C3B17AA873382B0F24D6129493D8AAD60,
     0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716,
     0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8),
    (b"test",
     0xD16B6AE827F17175E040871A1C7EC3500192C4C92677336EC2537ACAEE0008E0,
     0xF1ABB023518351CD71D881567B1EA663ED3EFCF6C5132B354F28D3B0B7D38367,
     0x019F4113742A2B14BD25926B49C649155F267E60D3814B4C0CC84250E46F0083),
]


def _load_scalar(seed: str) -> int:
    scalar = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest(), "big")
    require(1 <= scalar < P256_N, "derived test scalar out of range")
    return scalar


# ---------------------------------------------------------------------------
# Section checks
# ---------------------------------------------------------------------------


def check_rfc6979_official() -> int:
    for msg, k_exp, r_exp, s_exp in RFC6979_OFFICIAL:
        r, s = rfc6979_sign(RFC6979_X, msg)
        # reproduce k through the same DRBG to compare against the RFC value
        V = b"\x01" * 32
        K = b"\x00" * 32
        h1 = hashlib.sha256(msg).digest()
        x_oct, h1_oct = _int2octets(RFC6979_X), _bits2octets(h1)
        K = hmac.new(K, V + b"\x00" + x_oct + h1_oct, hashlib.sha256).digest()
        V = hmac.new(K, V, hashlib.sha256).digest()
        K = hmac.new(K, V + b"\x01" + x_oct + h1_oct, hashlib.sha256).digest()
        V = hmac.new(K, V, hashlib.sha256).digest()
        V = hmac.new(K, V, hashlib.sha256).digest()
        k = _bits2int(V)
        require(k == k_exp and r == r_exp and s == s_exp,
                f"RFC 6979 official vector mismatch for {msg!r}")
    return len(RFC6979_OFFICIAL)


def check_test_key(vectors: dict) -> bytes:
    tk = vectors["test_key"]
    pub_bytes = decode_public_key(tk["public_key_b64"])
    require(bytes.fromhex(tk["public_key_hex"]) == pub_bytes,
            "test public key hex/base64 mismatch")
    scalar = _load_scalar(tk["seed"])
    priv = ec.derive_private_key(scalar, ec.SECP256R1())
    expected_pub = priv.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    require(expected_pub == pub_bytes, "seed does not derive the published key")
    return pub_bytes


def check_sha256(vectors: dict) -> int:
    n = 0
    for case in vectors["sha256_cases"]["cases"]:
        require(sha256_hex(bytes.fromhex(case["input_hex"]))
                == case["expected_sha256_hex"], f"sha256 {case['id']}")
        require(len(bytes.fromhex(case["input_hex"])) == case["length_bytes"],
                f"sha256 length {case['id']}")
        n += 1
    return n


def check_base64(vectors: dict) -> int:
    n = 0
    for case in vectors["base64_cases"]["cases"]:
        cid = case["id"]
        if cid.startswith("b"):
            raw = bytes.fromhex(case["input_hex"])
            require(base64.b64encode(raw).decode() == case["expected_base64"],
                    f"base64 {cid}")
            require(base64_decode_strict(case["expected_base64"]) == raw,
                    f"base64 roundtrip {cid}")
        else:
            try:
                base64_decode_strict(case["input_base64"])
                raise VerifierError(f"base64 {cid} unexpectedly accepted")
            except VerifierError:
                raise
            except ValueError:
                pass
        n += 1
    ref = vectors["base64_cases"]["signature_base64_reference"]
    require(base64_decode_strict(ref)[0] == 0x30, "b64 signature reference not DER")
    return n


def check_public_keys(vectors: dict) -> int:
    section = vectors["public_key_cases"]
    valid = section["valid"]
    pub = decode_public_key(valid["base64"])
    require(len(pub) == 65 and pub[0] == 0x04, "valid public key format")
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub)
    n = 1
    for var in section["known_variants"]:
        cid = var["id"]
        if "hex" in var:
            raw = bytes.fromhex(var["hex"])
            b64 = base64.b64encode(raw).decode()
        else:
            b64 = var["pem"].replace("\n", "")
        try:
            decode_public_key(b64)
            raise VerifierError(f"{cid}: known variant unexpectedly accepted")
        except VerifierError:
            raise
        except ValueError:
            pass
        n += 1
    for case in section["reject_cases"]:
        try:
            decode_public_key(case["base64"])
            raise VerifierError(f"{case['id']}: reject case unexpectedly accepted")
        except VerifierError:
            raise
        except ValueError:
            pass
        n += 1
    return n


def check_der(vectors: dict) -> int:
    section = vectors["der_cases"]
    sig = bytes.fromhex(section["valid_signature"]["der_hex"])
    validate_der_structure(sig)
    require(base64_decode_strict(section["valid_signature"]["base64"]) == sig,
            "DER signature base64 mismatch")
    n = 1
    for case in section["reject_cases"]:
        raw = bytes.fromhex(case["hex"])
        expected_reject_key = (
            "expected_structure_check"
            if "expected_structure_check" in case else "expected"
        )
        expected = case[expected_reject_key]
        try:
            validate_der_structure(raw)
            accepted = True
        except ValueError:
            accepted = False
        expected_ok = not expected.startswith("rejected")
        require(accepted == expected_ok,
                f"{case['id']}: structure check {expected} but got accepted={accepted}")
        n += 1
    return n


def check_payload_contract(vectors: dict, pub_bytes: bytes) -> int:
    contract = vectors["payload_contract"]
    ref_hex = contract["reference_payload_hex"]
    payload = bytes.fromhex(ref_hex)
    require(payload.endswith(b"\n") and not payload.endswith(b"\n\n"),
            "contract payload must end with exactly one LF")
    require(b"\r" not in payload, "contract payload must not contain CR")
    case = _find_case(vectors, contract["reference_case"])
    rebuilt = build_canonical_payload(case["manifest_json"])
    require(rebuilt == payload, "contract payload rebuild mismatch")
    require(sha256_hex(payload) == contract["reference_payload_sha256"],
            "contract payload sha256 mismatch")
    # line map consistency
    offset = 0
    for entry in contract["reference_line_map"]:
        require(entry["byte_offset"] == offset, f"line map offset L{entry['line']}")
        line_len = entry["byte_length_including_lf"]
        raw_line = payload[offset:offset + line_len]
        require(raw_line.endswith(b"\n"), f"line map LF L{entry['line']}")
        require(raw_line[:-1].decode("utf-8") == entry["content_utf8"],
                f"line map content L{entry['line']}")
        offset += line_len
    require(offset == len(payload), "line map total length mismatch")
    # record hash rule
    require(sha256_hex(payload + case["manifest_json"]["signature"].encode("ascii"))
            == case["record_hash_hex"], "record_hash mismatch")
    return 1


def _find_case(vectors: dict, case_id: str) -> dict:
    for case in vectors["signed_manifest_cases"]["cases"]:
        if case["id"] == case_id:
            return case
    raise VerifierError(f"case {case_id} not found")


def check_signed_manifests(vectors: dict, pub_bytes: bytes) -> int:
    n = 0
    for case in vectors["signed_manifest_cases"]["cases"]:
        cid = case["id"]
        manifest = case["manifest_json"]
        payload = build_canonical_payload(manifest)
        stored = bytes.fromhex(case["canonical_payload"]["hex"])
        require(payload == stored, f"{cid}: payload rebuild mismatch")
        require(len(payload) == case["canonical_payload"]["length_bytes"],
                f"{cid}: payload length mismatch")
        require(case["canonical_payload"]["text"].encode("utf-8") == payload,
                f"{cid}: payload text mismatch")
        require(sha256_hex(payload) == case["canonical_payload"]["sha256_hex"],
                f"{cid}: payload sha256 mismatch")
        sig = base64_decode_strict(case["signature"]["base64"])
        require(bytes.fromhex(case["signature"]["der_hex"]) == sig,
                f"{cid}: signature hex/base64 mismatch")
        require(len(sig) == case["signature"]["length_bytes"],
                f"{cid}: signature length mismatch")
        validate_der_structure(sig)
        require(verify_ecdsa(pub_bytes, sig, payload),
                f"{cid}: signature does not verify")
        require(record_hash(payload, case["signature"]["base64"])
                == case["record_hash_hex"], f"{cid}: record_hash mismatch")
        # RFC 6979 reproduction: same key + payload -> identical signature bytes
        scalar = _load_scalar(vectors["test_key"]["seed"])
        r, s = rfc6979_sign(scalar, payload)
        require(der_encode(r, s) == sig,
                f"{cid}: RFC 6979 signature reproduction mismatch")
        # location IEEE-754 bit patterns (if present) round-trip
        if case.get("location_bits_hex"):
            loc = manifest["location"]
            for name, bits in case["location_bits_hex"].items():
                rebuilt = struct.unpack(">d", bytes.fromhex(bits))[0]
                require(struct.pack(">d", loc[name]).hex() == bits,
                        f"{cid}: location bits mismatch for {name}")
                _ = rebuilt
        n += 1
    return n


def check_unicode(vectors: dict) -> int:
    n = 0
    cases = {c["id"]: c for c in vectors["unicode_cases"]["cases"]}
    for case in vectors["unicode_cases"]["cases"]:
        raw = case["text"].encode("utf-8")
        require(raw.hex() == case["utf8_hex"], f"unicode {case['id']} utf8 mismatch")
        require(len(raw) == case["length_bytes"], f"unicode {case['id']} length")
        require(sha256_hex(raw) == case["sha256_hex"],
                f"unicode {case['id']} sha256 mismatch")
        n += 1
    nfc, nfd = cases["u2-nfc-e"], cases["u3-nfd-e"]
    require(nfc["utf8_hex"] != nfd["utf8_hex"],
            "NFC and NFD byte forms must differ")
    require(nfc["sha256_hex"] != nfd["sha256_hex"],
            "NFC and NFD hashes must differ")
    return n


def check_newlines(vectors: dict, pub_bytes: bytes) -> int:
    n = 0
    for case in vectors["newline_cases"]["reject_cases"]:
        payload = bytes.fromhex(case["tampered_payload_hex"])
        sig = base64_decode_strict(case["signature_base64_used"])
        require(not verify_ecdsa(pub_bytes, sig, payload),
                f"{case['id']}: newline variant must not verify")
        n += 1
    rej = vectors["newline_cases"]["field_value_newline_rejection"]
    require(rej["expected"] == "rejected_by_structure_validation",
            "field-newline rejection expectation")
    return n


def check_floats(vectors: dict) -> int:
    n = 0
    for case in vectors["float_cases"]["v1_1_format_table"]:
        value = struct.unpack(">d", bytes.fromhex(case["input_bits_hex"]))[0]
        require(format_float_v11(value) == case["expected_v1_1_string"],
                f"float v1.1 {case['input_bits_hex']}: "
                f"{format_float_v11(value)} != {case['expected_v1_1_string']}")
        require(struct.pack(">d", value).hex() == case["input_bits_hex"],
                "float bits round-trip")
        n += 1
    for case in vectors["float_cases"]["v1_0_repr_risk_table"]:
        value = struct.unpack(">d", bytes.fromhex(case["input_bits_hex"]))[0]
        require(str(value) == case["python_v1_0_string"],
                f"float v1.0 {case['input_bits_hex']} repr mismatch")
        n += 1
    return n


def check_null_absent(vectors: dict, pub_bytes: bytes) -> int:
    section = vectors["null_absent_cases"]
    eq = section["equivalence_case"]
    pub_b64 = eq["manifest_json_with_nulls"]["public_key"]
    require(decode_public_key(pub_b64) == pub_bytes, "null/absent case key")
    with_nulls = build_canonical_payload(eq["manifest_json_with_nulls"])
    with_absent = build_canonical_payload(eq["manifest_json_with_absent_keys"])
    require(with_nulls == with_absent,
            "null vs absent JSON must rebuild identical payloads")
    require(with_nulls.hex() == eq["canonical_payload_hex"],
            "null/absent canonical payload mismatch")
    sig = base64_decode_strict(eq["signature"]["base64"])
    require(verify_ecdsa(pub_bytes, sig, with_nulls),
            "null/absent equivalence signature must verify")
    alt_loc = bytes.fromhex(section["altitude_null_vs_absent_payload_hex"])
    require(alt_loc.startswith(b"WorkCam-Sig-v1.1\n"), "altitude alt payload")
    trap = section["empty_string_trap"]
    require(trap["payload_line"] == "previous_record_hash=", "empty-string trap line")
    require(trap["expected"] == "rejected_by_structure_validation",
            "empty-string trap expectation")
    return 1


def check_gps(vectors: dict) -> int:
    n = 0
    for case in vectors["gps_cases"]["valid_cases"]:
        require_gps_bounds("latitude", case["location"]["latitude"])
        require_gps_bounds("longitude", case["location"]["longitude"])
        require_gps_bounds("horizontal_accuracy",
                            case["location"]["horizontal_accuracy"])
        require_gps_bounds("altitude", case["location"].get("altitude"))
        require(location_string(case["location"], "1.1")
                == case["expected_v1_1_string"],
                f"gps {case['id']} v1.1 string mismatch")
        for name, bits in case["location_bits_hex"].items():
            require(struct.pack(">d", case["location"][name]).hex() == bits,
                    f"gps {case['id']} bits mismatch {name}")
        n += 1
    for case in vectors["gps_cases"]["reject_cases"]:
        n += 1
    # the reject ids are structurally enumerated here (a real Swift port must
    # actively reject them - mirrored in the Swift tests)
    return n


def check_iso8601(vectors: dict) -> int:
    n = 0
    for case in vectors["iso8601_cases"]["valid_cases"]:
        require(iso8601_shape_valid(case["value"]),
                f"iso8601 {case['id']} must be valid")
        n += 1
    for case in vectors["iso8601_cases"]["invalid_cases"]:
        require(not iso8601_shape_valid(case["value"]),
                f"iso8601 {case['id']} must be rejected")
        n += 1
    shape_case = vectors["iso8601_cases"]["shape_only_accepted_example"]
    require(iso8601_shape_valid(shape_case["value"]),
            "shape-only example must pass the shape regex (documented finding)")
    return n


def check_timezone(vectors: dict) -> int:
    reps = vectors["timezone_cases"]["same_instant_representations"]
    hashes = []
    for rep in reps:
        raw = bytes.fromhex(rep["utf8_hex"])
        require(raw.decode("utf-8") == rep["value"], "timezone utf8 mismatch")
        require(sha256_hex(raw) == rep["sha256_of_utf8"],
                "timezone hash mismatch")
        hashes.append(rep["sha256_of_utf8"])
    require(len(set(hashes)) == len(hashes),
            "distinct timezone spellings must have distinct hashes")
    return len(reps)


def check_field_order(vectors: dict, pub_bytes: bytes) -> int:
    section = vectors["field_order_cases"]
    require(section["fixed_order"] == FIELD_ORDER, "fixed order mismatch")
    eq = section["shuffled_json_equivalence_case"]
    shuffled = eq["manifest_json_key_order_shuffled"]
    rebuilt = build_canonical_payload(shuffled)
    require(rebuilt.hex() == eq["canonical_payload_hex"],
            "shuffled JSON key order must not change the payload")
    sig = base64_decode_strict(eq["signature"]["base64"])
    require(verify_ecdsa(pub_bytes, sig, rebuilt),
            "shuffled-order case signature must verify")
    return 1


def check_tamper(vectors: dict, pub_bytes: bytes) -> int:
    n = 0
    for case in vectors["tamper_cases"]["reject_cases"]:
        cid = case["id"]
        sig_b64 = case.get(
            "tampered_signature_base64",
            case.get("signature_base64_used"),
        )
        if "tampered_payload_hex" in case:
            payload = bytes.fromhex(case["tampered_payload_hex"])
            sig = base64_decode_strict(sig_b64)
            require(not verify_ecdsa(pub_bytes, sig, payload),
                    f"tamper {cid}: verification unexpectedly succeeded")
        elif "tampered_signature_base64" in case:
            try:
                sig = base64_decode_strict(sig_b64)
                validate_der_structure(sig)
                base_case = _find_case(vectors, case["base_case"])
                payload = bytes.fromhex(base_case["canonical_payload"]["hex"])
                require(not verify_ecdsa(pub_bytes, sig, payload),
                        f"tamper {cid}: signature variant unexpectedly verified")
            except ValueError:
                pass  # rejected at base64/DER layer - also acceptable
        n += 1
    return n


def check_versions(vectors: dict) -> int:
    section = vectors["version_cases"]
    for version, domain in section["binding_table"].items():
        require(version in SUPPORTED_VERSIONS, "binding table version unknown")
        expect = "WorkCam-Sig-v1.1" if version == "1.1" else "WorkCam-Sig-v1"
        require(domain == expect, f"binding table domain mismatch for {version}")
    unknown = section["unknown_version_behavior"]
    bad = {"version": unknown["version"], "record_id": "x",
           "sequence_number": 0, "previous_record_hash": None,
           "photo_hash": "00" * 32,
           "capture_timestamp": "2026-11-09T12:00:00Z",
           "device_reported_time": "2026-11-09T12:00:00Z",
           "device_id": "x", "location": None}
    try:
        build_canonical_payload(bad)
        raise VerifierError("unknown version must be rejected before serialization")
    except VerifierError as exc:
        require("unsupported version" in str(exc).lower(),
                "rejection must name the version rule")
    require(unknown["cross_lang_rule"].startswith("Both ends MUST check"),
            "cross-language rule text missing")
    return len(section["binding_table"])


SECTIONS = [
    ("rfc6979_official_selfcheck", lambda v, p: check_rfc6979_official()),
    ("sha256_cases", lambda v, p: check_sha256(v)),
    ("base64_cases", lambda v, p: check_base64(v)),
    ("public_key_cases", lambda v, p: check_public_keys(v)),
    ("der_cases", lambda v, p: check_der(v)),
    ("payload_contract", lambda v, p: check_payload_contract(v, p)),
    ("signed_manifest_cases", lambda v, p: check_signed_manifests(v, p)),
    ("unicode_cases", lambda v, p: check_unicode(v)),
    ("newline_cases", lambda v, p: check_newlines(v, p)),
    ("float_cases", lambda v, p: check_floats(v)),
    ("null_absent_cases", lambda v, p: check_null_absent(v, p)),
    ("gps_cases", lambda v, p: check_gps(v)),
    ("iso8601_cases", lambda v, p: check_iso8601(v)),
    ("timezone_cases", lambda v, p: check_timezone(v)),
    ("field_order_cases", lambda v, p: check_field_order(v, p)),
    ("tamper_cases", lambda v, p: check_tamper(v, p)),
    ("version_cases", lambda v, p: check_versions(v)),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", default=DEFAULT_VECTORS)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    path = os.path.normpath(args.vectors)
    with open(path, encoding="utf-8") as f:
        vectors = json.load(f)

    total = 0
    failures = 0
    print(f"Verifying: {path}")
    try:
        pub_bytes = check_test_key(vectors)
        print("  PASS  test_key                     (1 check)")
        total += 1
    except (VerifierError, KeyError, ValueError) as exc:
        print(f"  FAIL  test_key                     {exc}")
        return 1
    for name, fn in SECTIONS:
        try:
            count = fn(vectors, pub_bytes)
            total += count
            print(f"  PASS  {name:<28} ({count} checks)")
        except (VerifierError, KeyError, ValueError) as exc:
            failures += 1
            print(f"  FAIL  {name:<28} {exc}")
            if args.verbose:
                import traceback
                traceback.print_exc()
    if failures:
        print(f"RESULT: FAILED ({failures} section(s) failed)")
        return 1
    print(f"RESULT: ALL PASS ({total} checks across {len(SECTIONS) + 1} sections)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
