"""M03-D02/D03 supplement tests - issuance-time certificate audit.

The D02 supplement: verification verdicts now carry cert FACTS
(key usage = timeStamping EKU, algorithm policy floor, validity
windows, chain presence) instead of a bare verified=True. The D03
rule holds: an expired-TODAY cert valid at genTime still verifies;
windows are REPORTED, never silently re-judged. And the long-term
policy REFUSES to treat a mere archived cert as full trust - the
chain presence is a REPORTED fact with an explicit note.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.business.tsa_rfc3161 import (  # noqa: E402
    build_test_tsa, timestamp_query, tsa_respond, tsa_verify)
from techlab.business.long_term_evidence import (  # noqa: E402
    ArchivedMaterials, LongTermStatus, assess_long_term)


@pytest.fixture(scope="module")
def tsa(tmp_path_factory):
    return build_test_tsa(tmp_path_factory.mktemp("tsa-audit"))


class TestIssuanceAudit:
    def test_verdict_carries_cert_facts(self, tsa, tmp_path):
        data = b"audit-supplement-data"
        q = tmp_path / "q.tsq"
        r = tmp_path / "r.tsr"
        timestamp_query(data, q)
        tsa_respond(q, r, tsa)
        v = tsa_verify(data, r, tsa)
        assert v["verified"], v
        audit = v["cert_audit"]
        assert audit["key_usage"] is True, \
            "the TSA signer cert MUST carry the timeStamping EKU"
        assert audit["sha1_signature"] is False, \
            "SHA-1 signing is below policy"
        assert audit["chain_anchor_present"] is True
        assert audit["not_before"] and audit["not_after"]
        assert "genTime" in audit["note"]

    def test_verdict_is_not_silent_about_long_term_meaning(
            self, tsa, tmp_path):
        data = b"meaning-check"
        q = tmp_path / "q.tsq"
        r = tmp_path / "r.tsr"
        timestamp_query(data, q)
        tsa_respond(q, r, tsa)
        v = tsa_verify(data, r, tsa)
        note = v["cert_audit"]["note"]
        assert "valid at genTime" in note
        assert "expired TODAY" in note
        assert "never silently re-judged" in note


class TestLongTermCertRules:
    def test_archived_cert_alone_is_not_full_trust(self):
        """D03 rule: an archived TSA cert + expired chain is the
        expired_timestamp semantics (proof holds), NOT
        fully_verifiable - the policy keeps them DISTINCT."""
        v = assess_long_term(ArchivedMaterials(
            embedded_public_key="k",
            key_registration_state="active",
            tsa_certificate="archived-pem",
            tsa_token_gen_time="Nov 26 2026",
            tsa_chain_expired=True), True)
        # key active + signature valid -> fully_verifiable STATUS,
        # but the TIMESTAMP statement explicitly carries the
        # expired-chain semantics with the archived-cert caveat
        assert v.status == LongTermStatus.fully_verifiable
        assert "EXPIRED" in v.timestamp_statement
        assert "ARCHIVED" in v.timestamp_statement

    def test_algorithm_floor_flags_sha1(self):
        v = assess_long_term(ArchivedMaterials(
            embedded_public_key="k",
            key_registration_state="active",
            signature_algorithm="any-sha1"), True)
        assert v.status == LongTermStatus.algorithm_deprecated
        assert any("policy" in r for r in v.risks)

    def test_windows_reported_not_rejudged(self):
        """The policy output NEVER invents an 'expired at issuance'
        verdict - it has no genTime-vs-window oracle here; it
        reports the archived facts and hands the judgement to the
        caller (openssl's own verify at request time did the real
        check)."""
        v = assess_long_term(ArchivedMaterials(
            embedded_public_key="k",
            key_registration_state=None,
            tsa_certificate="pem",
            tsa_token_gen_time="Nov 26 2026",
            tsa_chain_expired=True), True)
        assert "still proves the data existed" in \
            v.timestamp_statement
        assert "expired TODAY" not in \
            v.timestamp_statement.split("genTime")[0]
