"""M03-D03 tests - long-term evidence verification policy."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.business.long_term_evidence import (  # noqa: E402
    ArchivedMaterials, LongTermPolicyError, LongTermStatus,
    assess_long_term, renewal_path)


def _mats(**kw) -> ArchivedMaterials:
    base = dict(embedded_public_key="key-pem-or-b64",
                key_registration_state=None,
                signature_algorithm="ecdsa-p256-sha256",
                tsa_certificate=None,
                tsa_token_gen_time=None,
                tsa_chain_expired=False)
    base.update(kw)
    return ArchivedMaterials(**base)


class TestLongTermVerdicts:
    def test_fully_verifiable(self):
        v = assess_long_term(
            _mats(key_registration_state="active"), True)
        assert v.status == LongTermStatus.fully_verifiable
        assert v.signature_math == "valid (checked)"
        assert not v.risks

    def test_math_valid_key_unknown_never_blends(self):
        v = assess_long_term(_mats(key_registration_state=None), True)
        assert v.status == LongTermStatus.math_valid_key_unknown
        # the three statements are SEPARATE: math / key / time
        assert v.signature_math == "valid (checked)"
        assert "CURRENT registration is unknown" in v.key_statement
        assert "no external time anchor" in v.timestamp_statement
        assert any("historical" in r.lower() for r in v.risks)

    def test_key_retired_is_historical_not_authorized(self):
        for state in ("rotated_out", "revoked"):
            v = assess_long_term(
                _mats(key_registration_state=state), True)
            assert v.status == LongTermStatus.key_retired
            assert state.upper() in v.key_statement
            assert "HISTORICAL" in v.key_statement
            assert any("current-authorization" in r for r in v.risks)

    def test_material_missing_never_guesses(self):
        v = assess_long_term(
            _mats(embedded_public_key=None), None)
        assert v.status == LongTermStatus.material_missing
        assert v.signature_math == "not checked"
        assert "never guessed" in v.key_statement

    def test_failed_math_is_failure_regardless_of_key(self):
        v = assess_long_term(
            _mats(key_registration_state="active"), False)
        assert v.status == LongTermStatus.material_missing
        assert "FAILED" in v.signature_math
        assert not v.renewal_available

    def test_deprecated_algorithm_is_risk_not_invalidation(self):
        v = assess_long_term(
            _mats(signature_algorithm="ecdsa-p192-sha256"), True)
        assert v.status == LongTermStatus.algorithm_deprecated
        assert "risk statement, not a validity claim" in v.risks[1]
        assert v.signature_math == "valid (checked)"

    def test_caller_bug_refused(self):
        # key retained but math not run -> the policy REFUSES to guess
        with pytest.raises(LongTermPolicyError):
            assess_long_term(_mats(), None)


class TestTimestampStatements:
    def test_no_token(self):
        v = assess_long_term(_mats(key_registration_state="active"),
                             True)
        assert "no external time anchor" in v.timestamp_statement

    def test_valid_token(self):
        v = assess_long_term(
            _mats(key_registration_state="active",
                  tsa_token_gen_time="Nov 26 22:34:44 2026 GMT"),
            True)
        assert "verifiable against the retained TSA chain" \
            in v.timestamp_statement

    def test_expired_chain_still_proves_existence(self):
        """THE core long-term semantics: chain expiry does NOT lapse
        the existence proof when the TSA cert is archived."""
        v = assess_long_term(
            _mats(key_registration_state="active",
                  tsa_token_gen_time="Nov 26 2026",
                  tsa_chain_expired=True,
                  tsa_certificate="archived-tsa-pem"), True)
        assert v.status == LongTermStatus.fully_verifiable
        assert "EXPIRED" in v.timestamp_statement
        assert "still proves the data existed at genTime" \
            in v.timestamp_statement
        assert "does not lapse" in v.timestamp_statement

    def test_expired_chain_without_archived_cert(self):
        v = assess_long_term(
            _mats(key_registration_state="active",
                  tsa_token_gen_time="Nov 26 2026",
                  tsa_chain_expired=True,
                  tsa_certificate=None), True)
        assert "cannot be re-verified" in v.timestamp_statement


class TestRenewal:
    def test_renewal_is_explicit_audit_event(self):
        v = assess_long_term(_mats(key_registration_state=None), True)
        entry = renewal_path(v, "ab" * 32)
        assert entry["action"] == "renewal"
        assert "NEVER rewritten" in entry["steps"][2]
        assert entry["prior_status"] == "math_valid_key_unknown"

    def test_renewal_refused_for_missing_materials(self):
        v = assess_long_term(
            _mats(embedded_public_key=None), None)
        with pytest.raises(LongTermPolicyError, match="never"):
            renewal_path(v, "ab" * 32)

    def test_renewal_refused_for_failed_math(self):
        v = assess_long_term(_mats(key_registration_state="active"),
                             False)
        with pytest.raises(LongTermPolicyError):
            renewal_path(v, "ab" * 32)
