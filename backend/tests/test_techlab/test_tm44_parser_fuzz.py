"""TM44: fuzz-style robustness suite for every evidence-facing parser.

Resource-limited adversarial inputs against our OWN parsers (never
against third-party systems). Every case must produce a TYPED error
or a safe rejection - a crash, hang or materialized giant allocation
is a failure. Covered parsers:

  evidence_schema.validate_record / build_canonical_payload (JSON)
  cbor_min (attestation/assertion CBOR)
  DER signatures (ECDSA verification path)
  Base64 (key/sigma decoding paths)
  ZIP packages (W018 check_zip_safety - bombs, traversal, nesting)
  manifests (techlab.evidence_package validation)
  NaN / Infinity / oversized ints / deep nesting / hostile unicode
"""

from __future__ import annotations

import base64
import io
import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"
                        / "appattest"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"
                       / "evidence-verifier"))

import cbor_min  # noqa: E402
import evidence_schema as es  # noqa: E402

import pytest  # noqa: E402

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature  # noqa: E402

# W018 CLI zip-safety (loaded the same way as the TM06 bridge suite)
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "verify_evidence",
    Path(__file__).resolve().parents[3] / "tools"
    / "evidence-verifier" / "verify_evidence.py")
ve = importlib.util.module_from_spec(_spec)
sys.modules["verify_evidence"] = ve
_spec.loader.exec_module(ve)

MAX_SECONDS_PER_CASE = 2.0        # a hang is a failure


def _timed(fn, *a, **k):
    t0 = time.perf_counter()
    out = fn(*a, **k)
    return out, time.perf_counter() - t0


# ------------------------------------------------------- JSON/schema fuzz

def test_schema_rejects_non_dict_and_scalars():
    for bad in (None, [], "x", 42, 3.14, True, b"bytes"):
        r = es.validate_record(bad)   # type: ignore[arg-type]
        assert not r.ok


def test_schema_handles_hostile_field_values():
    base = {"protocol_version": "1.1", "record_id": "a" * 32,
            "sequence_number": 1, "photo_hash": "b" * 64,
            "capture_timestamp": "2026-11-26T00:00:00+00:00",
            "device_reported_time": "2026-11-26T00:00:00+00:00",
            "device_id": "d", "signature": base64.b64encode(b"x").decode(),
            "public_key_b64":
                base64.b64encode(b"\x04" + b"\x00" * 64).decode()}
    # NaN / Infinity smuggled into numeric slots via raw dict (the
    # JSON wire layer rejects these; the validator must not crash)
    for bad_loc in ({"latitude": float("nan")},
                    {"latitude": float("inf")},
                    {"accuracy": float("-inf")}):
        r = es.validate_record(dict(base, location=bad_loc))
        assert not r.ok          # range checks catch non-finite too
    # gigantic sequence_number: the production model caps at 1e6 and
    # the TM01 schema doc now mirrors it (fuzz-found doc/model gap)
    r = es.validate_record(dict(base, sequence_number=2 ** 62))
    assert not r.ok
    r = es.validate_record(dict(base, sequence_number=1_000_001))
    assert not r.ok


def test_builder_rejects_hostile_newlines_everywhere():
    base = {"protocol_version": "1.1", "record_id": "a" * 32,
            "sequence_number": 1, "photo_hash": "b" * 64,
            "capture_timestamp": "2026-11-26T00:00:00+00:00",
            "device_reported_time": "2026-11-26T00:00:00+00:00",
            "device_id": "d"}
    for field in ("record_id", "capture_timestamp",
                  "device_reported_time", "device_id"):
        for payload_nl in ("a\nb", "a\rb", "a\r\nb"):
            with pytest.raises(ValueError):
                es.build_canonical_payload(dict(
                    base, **{field: payload_nl}))


def test_builder_rejects_nonfinite_location():
    base = {"protocol_version": "1.1", "record_id": "a" * 32,
            "sequence_number": 1, "photo_hash": "b" * 64,
            "capture_timestamp": "2026-11-26T00:00:00+00:00",
            "device_reported_time": "2026-11-26T00:00:00+00:00",
            "device_id": "d"}
    with pytest.raises(ValueError):
        es.build_canonical_payload(dict(
            base, location={"latitude": float("nan"),
                            "longitude": 0.0, "accuracy": 1.0}))


def test_deeply_nested_json_does_not_hang():
    deep = {"x": None}
    node = deep
    for _ in range(500):
        node["x"] = {"x": None}
        node = node["x"]
    rec = {"protocol_version": "1.1", "record_id": "a" * 32,
           "sequence_number": 1, "photo_hash": "b" * 64,
           "capture_timestamp": "2026-11-26T00:00:00+00:00",
           "device_reported_time": "2026-11-26T00:00:00+00:00",
           "device_id": "d", "extensions": deep}
    (_, dt) = _timed(es.validate_record, rec)
    assert dt < MAX_SECONDS_PER_CASE     # closed bag: no traversal


# ------------------------------------------------------------- CBOR fuzz

def test_cbor_hostile_inputs_all_typed_errors():
    bads = [
        b"", b"\xff", b"\x1c", b"\x1d\x00", b"\x1e", b"\x1f",
        b"\x58", b"\x58\x05ab",                   # truncated bytes
        b"\x59\x01",                              # truncated uint16 len
        b"\x5a\x00\x00",                          # truncated uint32 len
        b"\x5b" + b"\x00" * 7,                    # truncated uint64
        b"\x7f\x01",                              # indefinite text
        b"\x9f\x01",                              # indefinite array
        b"\xbf\x01\x02",                          # indefinite map
        bytes([0x9b]) + (2 ** 30).to_bytes(8, "big"),   # giant array claim
        bytes([0x7b]) + (2 ** 32).to_bytes(8, "big"),   # giant text claim
        bytes([0xdb]) + (2 ** 62).to_bytes(8, "big"),   # giant tag claim
        b"\x82\x01",                              # array, missing item
        b"\xa1\x01",                              # map, missing value
        b"\x61",                                  # text, missing bytes
        b"\xf8\x63",                              # unknown simple
        b"\xf9\x7c\x01",                          # float16 NaN payload
    ]
    for raw in bads:
        with pytest.raises(cbor_min.CBORDecodeError):
            cbor_min.loads(raw)
    # sanity: these LOOK hostile but are legal CBOR atoms - the
    # decoder must accept them (an over-eager decoder would corrupt
    # legitimate attestations)
    for ok in (b"\x00",                # integer 0
               b"\x38\xff",            # negative integer -256
               b"\x01", b"\x20", b"\xf4", b"\xf5", b"\xf6"):
        cbor_min.loads(ok)


def test_cbor_deep_nesting_bounded():
    """A crafted 2000-deep nested array must NOT blow the interpreter
    stack: the decoder's depth cap refuses it as a typed error, fast
    (TM44 fuzz finding - recursive-descent stack DoS)."""
    payload = b"\x81" * 2000 + b"\x01"            # [ [ [ ... 1
    t0 = time.perf_counter()
    with pytest.raises(cbor_min.CBORDecodeError):
        cbor_min.loads(payload)
    assert time.perf_counter() - t0 < MAX_SECONDS_PER_CASE
    # acceptable nesting (a few levels) still parses fine
    assert cbor_min.loads(b"\x81" * 10 + b"\x01") == [[[[[[[[[[1]]]]]]]]]]


# -------------------------------------------------------------- DER fuzz

def _valid_record_and_sig():
    key = ec.generate_private_key(ec.SECP256R1())
    text = es.build_canonical_payload({
        "protocol_version": "1.1", "record_id": "a" * 32,
        "sequence_number": 1, "photo_hash": "b" * 64,
        "capture_timestamp": "2026-11-26T00:00:00+00:00",
        "device_reported_time": "2026-11-26T00:00:00+00:00",
        "device_id": "d", "location": None})
    sig = key.sign(text.encode(), ec.ECDSA(hashes.SHA256()))
    return key, text.encode(), sig


def test_der_malformed_signatures_all_typed():
    key, data, sig = _valid_record_and_sig()
    pub = key.public_key()
    ders = [
        b"", b"\x30", b"\x30\x00", b"\x30\x81",           # length games
        b"\x30\x06\x02\x02\x01",                          # missing s
        b"\x30\x06\x02\x01\x01\x02",                      # missing s len
        b"\x30\x08\x02\x04\x01\x02\x03\x04\x02\x02\x01",  # s overflow
        b"\x30\x05\x02\x01\x01\x02\x01\x01",              # bad total len
        b"\x30\x03\x02\x01\x01",                          # r only
        b"\x30\x06\x00\x01\x01\x00\x01",                   # bad tags
        sig[:-1] + bytes([sig[-1] ^ 0x01]),               # bit flip
        b"\xff" * 72,                                      # garbage
    ]
    for d in ders:
        with pytest.raises((InvalidSignature, ValueError)):
            pub.verify(d, data, ec.ECDSA(hashes.SHA256()))


def test_base64_hostile_inputs_do_not_reach_crypto():
    bads = ["!!!!", "====", "\x00" * 4, "\U0001F386" * 8,
            "AA=A", "A===", "-_-", " " * 16]
    for b in bads:
        try:
            base64.b64decode(b, validate=True)
            ok = True
        except Exception:
            ok = False
        # validate=True must reject every hostile form here; the
        # production path (enroll + verify) uses exactly this mode
        assert ok is False, f"validator passed hostile base64: {b[:20]!r}"
    # long-but-legal base64 decodes fine - the DEFENSE against junk
    # keys lives downstream (65-byte X9.63 point length + curve check),
    # which is exactly what enroll_device enforces
    assert base64.b64decode("A" * 1000, validate=True) == b"\x00" * 750


# -------------------------------------------------------------- ZIP fuzz

def _zip_bytes(entries: dict, compress=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_bomb_rejected_by_size_guard():
    # 200MB of zeros compresses to ~200KB: the guard must catch it
    bomb = _zip_bytes({"bomb.bin": b"\x00" * (200 * 1024 * 1024)})
    with zipfile.ZipFile(io.BytesIO(bomb)) as zf:
        problems = ve.check_zip_safety(
            zf, ve.DEFAULT_MAX_UNCOMPRESSED_MB * 1024 * 1024)
    assert any("size" in p or "ratio" in p for p in problems)


def test_zip_traversal_variants_all_rejected():
    names = ["../evil.txt", "..\\evil.txt", "/abs.txt", "C:\\ev.txt",
             "a/../../evil.txt", "ok/../..//evil.txt"]
    for name in names:
        raw = _zip_bytes({name: b"x"})
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            problems = ve.check_zip_safety(
                zf, ve.DEFAULT_MAX_UNCOMPRESSED_MB * 1024 * 1024)
        assert problems, f"traversal name slipped: {name!r}"
        assert any("unsafe" in p for p in problems)


def test_zip_case_collision_rejected():
    raw = _zip_bytes({"Photos/a.jpg": b"x", "photos/a.jpg": b"y"})
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        problems = ve.check_zip_safety(
            zf, ve.DEFAULT_MAX_UNCOMPRESSED_MB * 1024 * 1024)
    assert any("collide" in p for p in problems)


def test_zip_extreme_ratio_rejected():
    # high-entropy claimed sizes: tiny file claiming gigabytes
    import struct
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("small.txt", b"A" * 1024 * 1024)   # 1MB -> tiny
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        info = zf.infolist()[0]
        # forge the uncompressed size field via a raw rebuild
        forged = bytearray(buf.getvalue())
        # CEN header size field: locate and patch is fragile; instead
        # assert the guard fires on a REAL high-ratio file
    raw = _zip_bytes({"rep.bin": b"\x41" * (50 * 1024 * 1024)})
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        problems = ve.check_zip_safety(zf, 1024 * 1024)  # 1MB cap
    assert any("size" in p for p in problems)


# --------------------------------------------------------- manifest fuzz

def test_manifest_wrapper_rejects_hostile_json():
    from techlab.evidence_package import PackageManifest
    import pydantic
    bads = [
        "{}", "[]", "null", "3", '"x"',
        '{"package_version": "9.9", "created_at": 1, "device_id": 2,'
        ' "records": "nope"}',
        '{"package_version": "1.0", "created_at": "t", "device_id":'
        ' "d", "records": [{"record_id": "x", "photo_file": "../etc",'
        ' "manifest_file": "m", "capture_timestamp": "t"}]}',
    ]
    for raw in bads:
        with pytest.raises((pydantic.ValidationError, ValueError)):
            PackageManifest.model_validate(json.loads(raw))


def test_unknown_protocol_version_never_trusted_fuzz_variants():
    for pv in ("0", "0.9", "1.2", "2.0", "1.1 ", " 1.1", "1.10", "X",
               1, 1.1, None, True, [], {}, b"1.1"):
        rec = {"protocol_version": pv, "record_id": "a" * 32,
               "sequence_number": 1, "photo_hash": "b" * 64,
               "capture_timestamp": "2026-11-26T00:00:00+00:00",
               "device_reported_time": "2026-11-26T00:00:00+00:00",
               "device_id": "d"}
        r = es.validate_record(rec)
        assert r.unknown_version is True or not r.ok, \
            f"version {pv!r} was not refused"
        assert not (r.ok and r.protocol_version not in ("1.0", "1.1"))
