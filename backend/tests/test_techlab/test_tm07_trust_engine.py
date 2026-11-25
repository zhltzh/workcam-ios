"""TM07: trust engine dimension tests - explainability + no client
promotion + honest statuses."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"
                        / "trust_engine"))

from trust_levels import (  # noqa: E402
    FAILED, NOT_ATTESTED, UNKNOWN, UNVERIFIED, VERIFIED, compute_trust)

import pytest  # noqa: E402


def test_ten_dimensions_always_present():
    r = compute_trust({"verification_env": "desktop"})
    names = [d.name for d in r.dimensions]
    assert len(names) == 10 and len(set(names)) == 10
    for expected in ("file_integrity", "signature_validity",
                     "device_registration", "hardware_attestation",
                     "time_source", "location_source", "chain_integrity",
                     "external_timestamp", "derivative_integrity",
                     "verification_environment"):
        assert expected in names


def test_every_dimension_carries_reason_evidence_failure_condition():
    r = compute_trust({"verification_env": "server"})
    for d in r.dimensions:
        assert d.reason and d.evidence and d.failure_condition, d.name


def test_happy_path_all_verified_except_location():
    r = compute_trust({
        "verification_env": "server",
        "photo_sha256_matches": True,
        "signature_verifies": True,
        "key_registration": {"key_fingerprint": "ab12",
                             "enrollment_status": "active"},
        "app_attest": {"verified": True},
        "photo_key_binding": {"verified": True},
        "time_anchor": {"state": "server_anchored",
                        "anchored_at": "2026-11-25T00:00:00+00:00"},
        "location": {"source": "gnss", "spoof_risk": "low"},
        "chain_report": {"valid": True, "records": 8,
                         "tail_truncation_risk": "LOCAL-CHAIN BOUNDARY..."},
        "external_checkpoint": {"ok": True, "records": 8},
        "derivatives": {"all_match": True, "checked": 3},
    })
    assert r.by_name("file_integrity").status == VERIFIED
    assert r.by_name("signature_validity").status == VERIFIED
    assert r.by_name("device_registration").status == VERIFIED
    assert r.by_name("hardware_attestation").status == VERIFIED
    assert r.by_name("time_source").status == VERIFIED
    assert r.by_name("chain_integrity").status == VERIFIED
    assert r.by_name("external_timestamp").status == VERIFIED
    assert r.by_name("derivative_integrity").status == VERIFIED
    # location NEVER upgrades past unverified - a GNSS claim is a claim
    assert r.by_name("location_source").status == UNVERIFIED
    assert not r.any_failed()


def test_tampered_bytes_fail_file_integrity():
    r = compute_trust({"verification_env": "desktop",
                       "photo_sha256_matches": False})
    d = r.by_name("file_integrity")
    assert d.status == FAILED
    assert r.any_failed()


def test_revoked_key_fails_registration():
    r = compute_trust({"verification_env": "desktop",
                       "key_registration": {"enrollment_status": "revoked"}})
    assert r.by_name("device_registration").status == FAILED


def test_rotated_out_key_is_unverified_not_failed():
    """Historical evidence signed by a rotated-out key keeps its math
    but never gains CURRENT authorization - TM03 semantics preserved."""
    r = compute_trust({"verification_env": "desktop",
                       "key_registration": {
                           "enrollment_status": "rotated_out"}})
    d = r.by_name("device_registration")
    assert d.status == UNVERIFIED and "historical" in d.reason


def test_app_attest_without_binding_is_not_attested():
    """THE honesty rule: App Attest verifying alone must NOT imply
    hardware provenance of the PHOTO key (they are different keys)."""
    r = compute_trust({"verification_env": "desktop",
                       "app_attest": {"verified": True}})
    d = r.by_name("hardware_attestation")
    assert d.status == NOT_ATTESTED
    assert "NOT bound" in d.reason


def test_app_attest_with_failed_binding_fails():
    r = compute_trust({"verification_env": "desktop",
                       "app_attest": {"verified": True},
                       "photo_key_binding": {"verified": False}})
    assert r.by_name("hardware_attestation").status == FAILED


def test_extrapolated_time_never_reports_verified():
    """Extrapolation is a bounded ESTIMATE - the wording must never
    read as a server-certified capture time."""
    r = compute_trust({"verification_env": "desktop",
                       "time_anchor": {"state": "extrapolated"}})
    d = r.by_name("time_source")
    assert d.status == UNVERIFIED
    assert "NOT a server-certified" in d.reason


def test_missing_everything_is_unknown_not_failed():
    r = compute_trust({"verification_env": "device"})
    assert r.by_name("file_integrity").status == UNKNOWN
    assert r.by_name("signature_validity").status == UNVERIFIED
    assert r.by_name("device_registration").status == UNKNOWN
    assert r.by_name("hardware_attestation").status == NOT_ATTESTED
    assert r.by_name("time_source").status == UNKNOWN
    assert r.by_name("location_source").status == NOT_ATTESTED
    assert r.by_name("chain_integrity").status == UNKNOWN
    assert r.by_name("external_timestamp").status == NOT_ATTESTED
    assert r.by_name("derivative_integrity").status == UNKNOWN
    assert not r.any_failed()


def test_no_composite_score_exists():
    """The report structure itself must not carry a total score."""
    r = compute_trust({"verification_env": "desktop"})
    as_dict = r.as_dict()
    assert "score" not in as_dict and "total" not in as_dict
    assert any("No composite score exists by design" in n
               for n in as_dict["notes"])


def test_verification_env_is_required():
    with pytest.raises(ValueError):
        compute_trust({})
    with pytest.raises(ValueError):
        compute_trust({"verification_env": "nonsense"})


def test_desktop_env_is_marked_unverified():
    r = compute_trust({"verification_env": "desktop"})
    d = r.by_name("verification_environment")
    assert d.status == UNVERIFIED
    assert "DESKTOP_SIMULATION" in d.failure_condition


def test_chain_failure_names_problems():
    r = compute_trust({"verification_env": "desktop",
                       "chain_report": {"valid": False,
                                        "problems": ["seq_gap: 3"]}})
    d = r.by_name("chain_integrity")
    assert d.status == FAILED and "seq_gap" in d.reason


def test_manual_location_is_unverified_claim():
    r = compute_trust({"verification_env": "desktop",
                       "location": {"source": "manual"}})
    d = r.by_name("location_source")
    assert d.status == UNVERIFIED
    assert "HUMAN-ENTERED" in d.reason
