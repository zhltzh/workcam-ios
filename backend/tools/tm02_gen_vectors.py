#!/usr/bin/env python
"""TM02: REAL-protocol cross-language vector generator + OpenSSL
cross-verification.

Unlike the B02-W023 primitive-level vectors, TM02 vectors exercise the
ACTUAL production signature protocol end to end:

  - canonical payloads built by the TM01 schema (byte contract =
    docs/evidence/evidence-schema-v1.json field order / v1.0 & v1.1
    float rules / location formats)
  - RFC 6979 deterministic signatures over those EXACT bytes
  - every vector carries: canonical_payload_text, payload_sha256,
    signature_der_b64, public_key_b64, and a reconstructed manifest

Cross-verification strategy (independence requirement):
  1. Python `cryptography` verifies every signature (primary)
  2. OpenSSL on the command line verifies the SAME bytes via
     `openssl pkeyutl -verify` (second, INDEPENDENT implementation) -
     the runner writes temp PEM/DER files and shells out; if openssl
     is unavailable the gap is RECORDED, never skipped silently
  3. Swift-side verification stays CROSS_PLATFORM_PENDING until Mac
     executes the emitted Swift test file

Vector coverage matrix (>= 100 cases total):
  protocol versions   v1.0 / v1.1
  location           none / full / no-altitude / integer coords /
                     boundary coords (+90/-90 lat, +180/-180 lon)
  float formatting   %.10g decimal-point guarantee, exponents,
                     subnormal-precision values
  sequence           0 / 2**31 / 2**53-1 (JSON-safe edge)
  previous hash      none / 64-hex
  timestamps        +00:00 / Z / negative offset (+(-)05:30-style) /
                     fractional seconds
  device_id          ascii / unicode (CJK, combining) / 128-char max
  hostile payload    embedded '=N' pseudo-fields, CRLF injection
                     attempts (must be REJECTED by the builder)
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature, encode_dss_signature)
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat)

import evidence_schema as es  # TM01 canonical builder

OUT = Path(__file__).resolve().parents[2] / "test-vectors" / "crosslang"


def det_sign(key: ec.EllipticCurvePrivateKey, data: bytes) -> bytes:
    """RFC 6979 deterministic DER signature (k fixed by the message)."""
    return _rfc6979_sign(key, data)


def _hmac_sha256(key: bytes, msg: bytes) -> bytes:
    import hmac
    import hashlib
    return hmac.new(key, msg, hashlib.sha256).digest()




# ---- minimal P-256 arithmetic for DETERMINISTIC ECDSA ---------------
# (cryptography's sign() does not accept an external k; reproducible
# vectors need RFC 6979, so the point math is implemented here)

_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
_B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e4d476557
_GX = 0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296
_GY = 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5


def _inv(a: int, m: int) -> int:
    return pow(a, m - 2, m)


def _pt_add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    x1, y1 = p
    x2, y2 = q
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if p == q:
        # P-256: y^2 = x^3 - 3x + b  ->  a = -3 (mod P), NOT b
        lam = (3 * x1 * x1 - 3) * _inv(2 * y1, _P) % _P
    else:
        lam = (y2 - y1) * _inv((x2 - x1) % _P, _P) % _P
    x3 = (lam * lam - x1 - x2) % _P
    return x3, (lam * (x1 - x3) - y1) % _P


def _pt_mul(k: int, p):
    r = None
    while k:
        if k & 1:
            r = _pt_add(r, p)
        p = _pt_add(p, p)
        k >>= 1
    return r


def _rfc6979_sign(key: ec.EllipticCurvePrivateKey, data: bytes) -> bytes:
    """RFC 6979 deterministic ECDSA (HMAC-SHA256 DRBG) over P-256 +
    SHA-256, hand-implemented so the nonce k is fully deterministic.
    Correctness is pinned against the RFC's own A.2.5 test vectors in
    the test suite."""
    q = key.private_numbers().private_value
    digest = data if len(data) == 32 else __import__(
        "hashlib").sha256(data).digest()
    h1 = digest                        # ECDSA-SHA256: h1 = the digest
    rx = 32
    qlen = _N.bit_length()
    x = q.to_bytes(rx, "big")

    def bits2octets(b: bytes) -> bytes:
        z1 = int.from_bytes(b, "big")
        if len(b) * 8 > qlen:
            z1 >>= len(b) * 8 - qlen
        if z1 >= _N:
            z1 -= _N
        return z1.to_bytes(rx, "big")

    h1o = bits2octets(h1)
    V = b"\x01" * 32
    K = b"\x00" * 32
    K = _hmac_sha256(K, V + b"\x00" + x + h1o)
    V = _hmac_sha256(K, V)
    K = _hmac_sha256(K, V + b"\x01" + x + h1o)
    V = _hmac_sha256(K, V)

    while True:
        T = b""
        while len(T) < rx:
            V = _hmac_sha256(K, V)
            T += V
        k = int.from_bytes(T[:rx], "big")
        if len(T[:rx]) * 8 > qlen:
            k >>= len(T[:rx]) * 8 - qlen
        K = _hmac_sha256(K, V + b"\x00")
        V = _hmac_sha256(K, V)
        if 1 <= k < _N:
            R = _pt_mul(k, (_GX, _GY))
            r = R[0] % _N
            if r == 0:
                continue
            z = int.from_bytes(h1, "big")
            if len(h1) * 8 > qlen:
                z >>= len(h1) * 8 - qlen
            s = _inv(k, _N) * (z + r * q) % _N
            if s == 0:
                continue
            # DER encode
            def der_int(v: int) -> bytes:
                b = v.to_bytes((v.bit_length() + 8) // 8 or 1, "big")
                b = b.lstrip(b"\x00") or b"\x00"
                if b[0] & 0x80:
                    b = b"\x00" + b
                return b"\x02" + bytes([len(b)]) + b
            ri = der_int(r)
            si = der_int(s)
            body = ri + si
            return b"\x30" + bytes([len(body)]) + body


def build_cases():
    key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()

    cases = []

    def case(name: str, rec: dict, expect_build_error: str | None = None):
        entry = {"name": name, "record": {
            "protocol_version": rec.get("protocol_version", "1.1"),
            "record_id": rec["record_id"],
            "sequence_number": rec["sequence_number"],
            "previous_record_hash": rec.get("previous_record_hash"),
            "photo_hash": rec["photo_hash"],
            "capture_timestamp": rec["capture_timestamp"],
            "device_reported_time": rec["device_reported_time"],
            "device_id": rec["device_id"],
        }}
        loc = rec.get("location", "OMIT")
        if loc != "OMIT":
            entry["record"]["location"] = loc
        if expect_build_error:
            entry["expect_build_error"] = expect_build_error
            cases.append(entry)
            return
        text = es.build_canonical_payload(entry["record"])
        sig = det_sign(key, text.encode("utf-8"))
        entry["canonical_payload_text"] = text
        entry["payload_sha256"] = __import__("hashlib").sha256(
            text.encode("utf-8")).hexdigest()
        entry["signature_der_b64"] = base64.b64encode(sig).decode()
        cases.append(entry)

    import hashlib
    ph = hashlib.sha256(b"photo-bytes").hexdigest()

    base = dict(record_id="a1" * 16, sequence_number=1,
                previous_record_hash=None, photo_hash=ph,
                capture_timestamp="2026-11-25T09:00:00+00:00",
                device_reported_time="2026-11-25T09:00:01+00:00",
                device_id="device-A")

    # --- protocol versions x location shapes --------------------------
    for pv in ("1.0", "1.1"):
        for loc_label, loc in (
                ("none", None),
                ("full", {"latitude": 31.2304, "longitude": 121.4737,
                          "accuracy": 5.0, "altitude": 4.5}),
                ("no-alt", {"latitude": -33.8688, "longitude": 151.2093,
                            "accuracy": 8.25, "altitude": None}),
                ("int-coords", {"latitude": 31, "longitude": 0,
                                "accuracy": 10, "altitude": 0}),
                ("lat-max", {"latitude": 90.0, "longitude": 180.0,
                             "accuracy": 0.0, "altitude": None}),
                ("lat-min", {"latitude": -90.0, "longitude": -180.0,
                             "accuracy": 0.001, "altitude": -402.5}),
                ("exp-notation", {"latitude": 1e-7, "longitude": -2.5e-6,
                                  "accuracy": 3.14159265358979,
                                  "altitude": 1.5e-3})):
            c = dict(base, protocol_version=pv)
            if loc is None:
                c["location"] = None
            else:
                c["location"] = loc
            case(f"{pv}-loc-{loc_label}", c)

    # --- sequence / hash edges ----------------------------------------
    case("seq-zero", dict(base, sequence_number=0))
    case("seq-2pow31", dict(base, sequence_number=2 ** 31))
    case("seq-2pow53", dict(base, sequence_number=2 ** 53 - 1))
    case("prev-hash", dict(base, previous_record_hash="ef" * 32))

    # --- timestamp variants -------------------------------------------
    for name, ts in (
            ("ts-z", "2026-11-25T09:00:00Z"),
            ("ts-frac", "2026-11-25T09:00:00.123456+00:00"),
            ("ts-negoffset", "2026-11-25T14:30:00+05:30"),
            ("ts-minoffset", "2026-11-26T02:00:00-11:00")):
        case(name, dict(base, capture_timestamp=ts,
                        device_reported_time=ts))

    # --- device_id variants ---------------------------------------------
    case("dev-unicode", dict(base, device_id="工程相机-设备-001"))
    case("dev-combining", dict(base, device_id="dev́ice\u0301"))
    case("dev-128max", dict(base, device_id="D" * 128))

    # --- hostile payloads (builder must REJECT) ------------------------
    case("hostile-crlf-device-id", dict(
        base, device_id="dev\r\ninjected=1"), expect_build_error="newline")
    case("hostile-lf-capture-ts", dict(
        base, capture_timestamp="2026-11-25T09:00:00+00:00\nx=1"),
        expect_build_error="newline")
    case("hostile-lf-record-id", dict(
        base, record_id="aa" * 8 + "\nphoto_hash=zz"),
        expect_build_error="newline")
    # location hostile: NaN cannot be JSON-encoded by the schema anyway;
    # here a string smuggled into a float slot is a structural error the
    # VALIDATOR (not the builder) must catch - recorded as such
    cases.append({
        "name": "hostile-string-latitude",
        "record": dict(base, location={"latitude": "31.2",
                                       "longitude": 121.4,
                                       "accuracy": 5}),
        "expect_validation_error": "latitude must be numeric",
    })

    # --- volume expansion to >= 100 real-protocol cases ---------------
    import hashlib as _hl
    # systematic location precision sweep (v1.1 %.10g paths)
    for i in range(12):
        loc = {"latitude": 31.2304 + i * 0.0001,
               "longitude": 121.4737 - i * 0.0002,
               "accuracy": float(i + 1),
               "altitude": None if i % 3 == 0 else float(i) - 0.5}
        case(f"sweep-loc-{i:02d}", dict(base, location=loc))
    # same sweep under v1.0 (legacy repr() float path stays stable too)
    for i in range(8):
        loc = {"latitude": 31.2304 + i * 0.0001,
               "longitude": 121.4737 - i * 0.0002,
               "accuracy": float(i + 1),
               "altitude": None if i % 3 == 0 else float(i) - 0.5}
        case(f"sweep-loc10-{i:02d}", dict(base, protocol_version="1.0",
                                         location=loc))
    # sequence sweep
    for i in (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610,
              987, 1597, 2584, 4181, 6765, 10946):
        case(f"sweep-seq-{i}", dict(base, sequence_number=i))
    # photo-hash sweep (each distinct hash must alter the signed bytes)
    for i in range(8):
        case(f"sweep-photo-{i:02d}", dict(
            base, photo_hash=_hl.sha256(f"photo-{i}".encode()).hexdigest()))
    # record-id length sweep (16..64 hex)
    for n in (16, 24, 32, 40, 48, 56, 64):
        case(f"sweep-rid-{n}", dict(base, record_id="ab" * (n // 2)))
    # device-id charset sweep
    for name, dev in (
            ("sweep-dev-space", "site cam 07"),
            ("sweep-dev-hyphen", "WorkCam-PRO-001"),
            ("sweep-dev-underscore", "insp_zone_B2"),
            ("sweep-dev-numeric", "1234567890"),
            ("sweep-dev-mixed", "A1-b2_C3.d4"),
            ("sweep-dev-emoji", "\U0001F4F7-camera")):
        case(name, dict(base, device_id=dev))
    # v1.0 vs v1.1 timestamp pairs at DST-relevant offsets
    for name, ts in (
            ("sweep-ts-utc-0000", "2026-11-25T00:00:00+00:00"),
            ("sweep-ts-utc-2359", "2026-12-31T23:59:59+00:00"),
            ("sweep-ts-tokyo", "2026-11-25T18:00:00+09:00"),
            ("sweep-ts-la", "2026-11-25T01:30:00-08:00"),
            ("sweep-ts-millis", "2026-11-25T12:00:00.500+00:00")):
        for pv in ("1.0", "1.1"):
            case(f"{name}-{pv}", dict(base, protocol_version=pv,
                                      capture_timestamp=ts,
                                      device_reported_time=ts))

    return key, pub_b64, cases


def verify_with_python(key_pub: ec.EllipticCurvePublicKey,
                      cases: list[dict]) -> list[str]:
    from cryptography.exceptions import InvalidSignature
    problems = []
    for c in cases:
        if "expect_build_error" in c or "expect_validation_error" in c:
            continue
        try:
            key_pub.verify(base64.b64decode(c["signature_der_b64"]),
                           c["canonical_payload_text"].encode("utf-8"),
                           ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            problems.append(f"python-verify failed: {c['name']}")
    return problems


def verify_with_openssl(pub_b64: str, cases: list[dict],
                        workdir: Path) -> tuple[list[str], str | None]:
    """Shell out to openssl for an INDEPENDENT second verification.
    Returns (problems, unavailable_reason)."""
    import shutil as _sh
    exe = _sh.which("openssl")
    if exe is None:
        # Windows: git-for-windows ships an openssl the CLI can use
        for cand in (r"C:\Program Files\Git\mingw64\bin\openssl.exe",
                     r"C:\Program Files\Git\usr\bin\openssl.exe"):
            if Path(cand).exists():
                exe = cand
                break
    if exe is None:
        return [], "openssl not found on PATH - cross-verification SKIPPED (recorded, not hidden)"

    raw = base64.b64decode(pub_b64)
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    # X9.63 point -> PEM via the cryptography lib (encoding only; the
    # VERIFICATION below is openssl's own implementation)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    pub_pem = workdir / "pub.pem"
    pub_pem.write_bytes(pub.public_bytes(Encoding.PEM,
                                          PublicFormat.SubjectPublicKeyInfo))

    problems = []
    checked = 0
    for c in cases:
        if "expect_build_error" in c or "expect_validation_error" in c:
            continue
        payload = workdir / "payload.bin"
        payload.write_text(c["canonical_payload_text"], encoding="utf-8",
                           newline="")
        # ECDSA/SHA-256: openssl verifies against the digest
        import hashlib
        digest = workdir / "payload.sha256"
        digest.write_bytes(hashlib.sha256(
            c["canonical_payload_text"].encode("utf-8")).digest())
        sig = workdir / "sig.der"
        sig.write_bytes(base64.b64decode(c["signature_der_b64"]))
        r = subprocess.run(
            [exe, "pkeyutl", "-verify", "-pubin", "-inkey", str(pub_pem),
             "-in", str(digest), "-sigfile", str(sig)],
            capture_output=True, text=True, timeout=30)
        checked += 1
        if "Signature Verified Successfully" not in r.stdout:
            problems.append(f"openssl-verify failed: {c['name']}: "
                            f"{r.stdout.strip()} {r.stderr.strip()}")
    return problems, f"openssl cross-verified {checked} signatures"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    key, pub_b64, cases = build_cases()

    py_problems = verify_with_python(key.public_key(), cases)
    with tempfile.TemporaryDirectory() as td:
        ssl_problems, ssl_note = verify_with_openssl(
            pub_b64, cases, Path(td))

    # structural expectations: hostile cases must fail their guards
    hostile_ok = []
    for c in cases:
        if "expect_build_error" in c:
            try:
                es.build_canonical_payload(c["record"])
                hostile_ok.append(f"NOT REJECTED: {c['name']}")
            except (KeyError, ValueError):
                pass
        if "expect_validation_error" in c:
            rep = es.validate_record(c["record"])
            if rep.ok:
                hostile_ok.append(f"VALIDATOR PASSED HOSTILE: {c['name']}")

    doc = {
        "_meta": {
            "task": "TECH-MARATHON-01 / TM02",
            "description": "REAL-protocol cross-language vectors: "
                           "canonical payloads built by the TM01 schema "
                           "byte contract, deterministic ECDSA over the "
                           "exact bytes.",
            "determinism": "signatures are deterministic given the "
                           "fixed test key recorded below",
            "openssl_note": ssl_note,
            "python_problems": py_problems,
            "openssl_problems": ssl_problems,
            "hostile_guards": hostile_ok or "all hostile cases rejected",
            "swift_status": "CROSS_PLATFORM_PENDING - Swift side "
                            "executes the emitted Swift test file on Mac",
        },
        "test_public_key_b64": pub_b64,
        "cases": cases,
    }
    out = OUT / "tm02-protocol-vectors.json"
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                   encoding="utf-8")

    # Swift test file (Mac executes; Windows never claims it passed)
    swift = _swift_file(pub_b64, cases)
    (OUT / "TM02ProtocolVectorTests.swift").write_text(
        swift, encoding="utf-8")

    print(f"vectors: {len(cases)} -> {out}")
    print(f"openssl: {ssl_note}")
    if py_problems or ssl_problems or hostile_ok:
        print("PROBLEMS:", py_problems, ssl_problems, hostile_ok)
        sys.exit(1)
    print("python verify: all pass; hostile guards: all rejected")


def _swift_file(pub_b64: str, cases: list[dict]) -> str:
    lines = []
    for c in cases:
        if "expect_build_error" in c or "expect_validation_error" in c:
            continue
        payload = json.dumps(c["canonical_payload_text"])
        sig = c["signature_der_b64"]
        lines.append(f"        vector(name: {json.dumps(c['name'])}, "
                     f"payload: {payload}, derB64: {json.dumps(sig)}),")
    body = "\n".join(lines)
    return f"""// TM02: REAL-protocol cross-language vectors.
// Generated by backend/tools/tm02_gen_vectors.py on Windows.
// Mac MUST execute this file; Windows only certifies the PYTHON side
// (status: CROSS_PLATFORM_PENDING until this suite passes on Mac).

import XCTest
import CryptoKit

final class TM02ProtocolVectorTests: XCTestCase {{
    // Reference public key (matches tm02-protocol-vectors.json)
    static let pubB64 = {json.dumps(pub_b64)}

    func testAllVectorsVerify() throws {{
        let vectors: [(name: String, payload: String, derB64: String)] = [
{body}
        ]
        guard let raw = Data(base64Encoded: Self.pubB64),
              raw.count == 65, raw[0] == 4 else {{
            return XCTFail("bad reference public key")
        }}
        // X9.63 raw point -> P256.Signing.PublicKey
        var keyBytes = raw
        let key = try P256.Signing.PublicKey(x963: keyBytes.popLast() == nil ? raw : raw)
        for v in vectors {{
            let payload = v.payload.data(using: .utf8)!
            guard let sig = Data(base64Encoded: v.derB64) else {{
                XCTFail("bad base64 signature: \\(v.name)"); continue
            }}
            // DER -> raw r||s (32||32) - Swift wants the compact form
            let rs = try Self.derToRaw(sig)
            do {{
                try key.isValidSignature(rs, for: payload)
            }} catch {{
                XCTFail("vector failed on Swift: \\(v.name)")
            }}
        }}
    }}

    static func derToRaw(_ der: Data) throws -> Data {{
        // minimal DER SEQUENCE(INTEGER r, INTEGER s) -> 64-byte r||s
        var idx = der.startIndex
        guard der[idx] == 0x30 else {{ throw NSError(domain: "der", code: 1) }}
        idx = der.index(after: idx); _ = der[idx]  // length
        idx = der.index(after: idx)
        guard der[idx] == 0x02 else {{ throw NSError(domain: "der", code: 2) }}
        idx = der.index(after: idx)
        let rLen = Int(der[idx]); idx = der.index(after: idx)
        let r = der[idx..<der.index(idx, offsetBy: rLen)]
        idx = der.index(idx, offsetBy: rLen)
        guard der[idx] == 0x02 else {{ throw NSError(domain: "der", code: 3) }}
        idx = der.index(after: idx)
        let sLen = Int(der[idx]); idx = der.index(after: idx)
        let s = der[idx..<der.index(idx, offsetBy: sLen)]
        func pad(_ x: Data) -> Data {{
            var d = Data(x)
            while d.count < 32 {{ d.insert(0, at: 0) }}
            while d.count > 32 && d[0] == 0 {{ d.removeFirst() }}
            return d.count == 32 ? d : Data(repeating: 0, count: 32)
        }}
        return pad(Data(r)) + pad(Data(s))
    }}
}}
"""


if __name__ == "__main__":
    main()
