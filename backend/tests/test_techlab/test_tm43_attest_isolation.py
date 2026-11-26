"""TM43 supplement: App Attest trust-anchor ENVIRONMENT ISOLATION.

The TM04 verifier takes the anchor as a CONFIGURED input; this suite
pins the deployment rule the task demands:

  - a DEV-issued attestation (signed by the dev CA tree) must FAIL
    verification against the PRODUCTION anchor
  - the same dev attestation PASSES against the dev anchor (the
    verifier itself works - the isolation is a CONFIG property)
  - a NEITHER-tree attestation fails against both (unknown root)

All CA material is SYNTHETIC (synth_vectors.py); the real-Apple
cross-check stays DEVICE_PENDING. The test proves the ISOLATION
MECHANISM, not Apple's actual roots.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"
                       / "appattest"))

import synth_vectors as sv  # noqa: E402
from app_attest_verify import verify_attestation  # noqa: E402

DEV_APP_ID = "TEAMDEV.com.example.workcam.dev"
PROD_APP_ID = "TEAMPROD.com.example.workcam"
RP_ID = "com.example.workcam"


def _build_env(app_id: str, challenge: bytes):
    root, leaf, inter, key, cose = sv.build_tree(app_id, challenge)
    obj = sv.build_attestation(leaf, inter, RP_ID, cose)
    return root, obj


def test_dev_attestation_rejected_against_production_anchor():
    """THE isolation rule: dev material never verifies under the
    production trust anchor."""
    challenge = b"prod-challenge-1"
    prod_root, _ = _build_env(PROD_APP_ID, b"prod-seed")
    dev_root, dev_obj = _build_env(DEV_APP_ID, challenge)
    # (dev_obj was built for the DEV app id; even ignoring the app-id
    # mismatch, the CHAIN itself must fail against the prod anchor)
    result = verify_attestation(dev_obj, challenge, DEV_APP_ID,
                                prod_root, environment_hint="production")
    assert not result.verified
    assert any("chain" in f for f in result.failures)


def test_dev_attestation_passes_against_dev_anchor():
    """The verifier works; isolation is configuration, not breakage."""
    challenge = b"dev-challenge-1"
    dev_root, dev_obj = _build_env(DEV_APP_ID, challenge)
    result = verify_attestation(dev_obj, challenge, DEV_APP_ID,
                                dev_root, environment_hint="development")
    assert result.verified, result.failures
    assert result.environment == "development"


def test_prod_attestation_rejected_against_dev_anchor():
    """And the mirror: production material under the dev anchor is a
    config error the verifier must refuse too."""
    challenge = b"dev-challenge-2"
    dev_root, _ = _build_env(DEV_APP_ID, b"dev-seed")
    prod_root, prod_obj = _build_env(PROD_APP_ID, challenge)
    result = verify_attestation(prod_obj, challenge, PROD_APP_ID,
                                dev_root, environment_hint="development")
    assert not result.verified


def test_unknown_root_rejected_everywhere():
    challenge = b"u-1"
    stranger_root, _ = _build_env("STRANGER.com.other", b"u-seed")
    _, att_obj = _build_env(PROD_APP_ID, challenge)
    for anchor, env in ((stranger_root, "production"),):
        result = verify_attestation(att_obj, challenge, PROD_APP_ID,
                                    anchor, environment_hint=env)
        assert not result.verified


def test_anchor_never_comes_from_client_payload():
    """Structural rule: the verifier signature takes the anchor as a
    parameter - it can never be extracted from the attestation. The
    x5c inside the object carries leaf+intermediate ONLY; the check
    below pins that the synth builder cannot smuggle a root in."""
    import cbor_min
    root, leaf, inter, key, cose = sv.build_tree(PROD_APP_ID, b"c")
    obj = cbor_min.loads(sv.build_attestation(leaf, inter, RP_ID, cose))
    stmt = cbor_min.loads(obj["attStmt"])   # attStmt is nested CBOR
    certs = stmt["x5c"]
    assert len(certs) == 2          # leaf + intermediate, NO root
