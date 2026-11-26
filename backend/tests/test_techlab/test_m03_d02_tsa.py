"""M03-D02 tests - RFC 3161 over the REAL protocol (local test TSA)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.business.tsa_rfc3161 import (  # noqa: E402
    TSAError, TestTSA, build_test_tsa, evidence_meaning_note,
    message_imprint, timestamp_query, tsa_respond, tsa_verify)


@pytest.fixture(scope="module")
def tsa(tmp_path_factory) -> TestTSA:
    d = tmp_path_factory.mktemp("tsa")
    return build_test_tsa(d)


class TestRFC3161:
    def test_full_query_respond_verify(self, tsa, tmp_path):
        data = b"photo-bytes-" + b"x" * 5000
        q = tmp_path / "q.tsq"
        r = tmp_path / "r.tsr"
        query_der = timestamp_query(data, q)
        assert query_der[:1] == b"\x30"        # DER SEQUENCE
        assert len(query_der) > 50
        tsa_respond(q, r, tsa)
        assert r.exists() and r.stat().st_size > 0
        v = tsa_verify(data, r, tsa)
        assert v["verified"], v
        assert v["gen_time"]                    # genTime extracted

    def test_nonce_round_trips(self, tsa, tmp_path):
        data = b"nonce-data"
        q = tmp_path / "q.tsq"
        r = tmp_path / "r.tsr"
        timestamp_query(data, q)           # -nonce included by default
        tsa_respond(q, r, tsa)
        v = tsa_verify(data, r, tsa)
        assert v["verified"]
        assert v["nonce"]                  # nonce echoed in the token

    def test_wrong_data_fails_verification(self, tsa, tmp_path):
        data = b"original data"
        other = b"tampered data"
        q = tmp_path / "q.tsq"
        r = tmp_path / "r.tsr"
        timestamp_query(data, q)
        tsa_respond(q, r, tsa)
        v = tsa_verify(other, r, tsa)      # verify against OTHER bytes
        assert not v["verified"], \
            "messageImprint mismatch MUST fail verification"

    def test_tampered_token_fails(self, tsa, tmp_path):
        data = b"payload"
        q = tmp_path / "q.tsq"
        r = tmp_path / "r.tsr"
        timestamp_query(data, q)
        tsa_respond(q, r, tsa)
        raw = bytearray(r.read_bytes())
        # flip a byte deep inside the token (past the DER headers)
        raw[-20] ^= 0xFF
        r2 = tmp_path / "r-tampered.tsr"
        r2.write_bytes(bytes(raw))
        v = tsa_verify(data, r2, tsa)
        assert not v["verified"], "token tampering MUST fail"

    def test_message_imprint_shape(self):
        imp = message_imprint(b"abc")
        assert imp["hash_algorithm_oid"] == "2.16.840.1.101.3.4.2.1"
        assert imp["digest_hex"] == \
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

    def test_test_tsa_clearly_labeled(self, tsa):
        cert = tsa.tsa_cert.read_text(encoding="utf-8")
        # the DER in the PEM contains the CN bytes; verify the label
        import base64
        body = base64.b64decode(
            "".join(l for l in cert.splitlines()
                    if not l.startswith("---")))
        assert b"TEST ONLY" in body, \
            "test TSA certs must carry the TEST ONLY label"

    def test_evidence_meaning_stated(self):
        note = evidence_meaning_note()
        assert "NOTHING about when the photo was taken" in note
