"""TM06: bridge the B02-W018 six-layer CLI report to the TM07
ten-dimension TrustReport.

The W018 CLI produces honest per-record layer dicts (L1..L6). TM07
defines the canonical ten-dimension ledger. This module maps one to
the other WITHOUT ever upgrading a claim:

  L1 pass -> file_integrity VERIFIED
  L2 pass -> signature_validity VERIFIED
  L3 registered/... -> device_registration (registry states mirror
             TM03: active / rotated_out / suspended / revoked /
             unknown - a rotated-out key keeps its MATH but reports
             UNVERIFIED, exactly like the server-side model)
  L4 (never verifiable offline) -> hardware_attestation NOT_ATTESTED
  L5 server_anchored/device_reported -> time_source VERIFIED / UNVERIFIED
  L6 signed_in_payload/none -> location_source UNVERIFIED / NOT_ATTESTED
  chain.json + checkpoint.json in the package (TM05 conventions)
             -> chain_integrity + external_timestamp
  derivatives from the W018 record verification
             -> derivative_integrity
  CLI context -> verification_environment 'desktop' (always stated)

The registry JSON gains an OPTIONAL per-key "status" mirroring the
TM03 lifecycle; the classic {public_key_b64, device_id} entries keep
their 'active' semantics. Unknown status values are treated as
'unknown' - never as active.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]     # tm-security/
for p in (_REPO_ROOT / "backend" / "tools",
          _REPO_ROOT / "backend",
          _REPO_ROOT / "backend" / "tools" / "trust_engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from trust_levels import compute_trust  # noqa: E402

_REGISTRY_STATES = ("active", "rotated_out", "suspended", "revoked")


def load_registry_statuses(registry_path: Path) -> dict[str, dict]:
    """{public_key_b64: {status, device_id, last_event_at?}} - statuses
    mirror TM03's lifecycle. Missing/unknown status -> unknown."""
    out: dict[str, dict] = {}
    data = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    for item in data.get("keys", []):
        pub = item.get("public_key_b64")
        if not pub:
            continue
        status = item.get("status")
        if status not in _REGISTRY_STATES:
            status = "unknown"
        out[pub] = {"enrollment_status": status,
                    "device_id": item.get("device_id", "unknown"),
                    "last_event_at": item.get("last_event_at")}
    return out


def _chain_inputs(pkg_dir: Path) -> tuple[dict | None, dict | None,
                                          dict | None, str | None]:
    """Read TM05 conventions from the package: chain.json (record
    list), checkpoint.json (signed external checkpoint) and the anchor
    PEM for checkpoint verification (checkpoint-key.pem, operator
    supplied). Returns (chain_records, chain_report, checkpoint_report,
    error)."""
    chain_path = pkg_dir / "chain.json"
    if not chain_path.exists():
        return None, None, None, None
    try:
        chain = json.loads(chain_path.read_text(encoding="utf-8"))
        records = chain.get("records", [])
    except Exception as e:
        return None, None, None, f"chain.json unreadable: {e}"

    import audit_chain as ac  # TM05
    report = ac.verify_chain(records).as_dict()

    cp_report = None
    cp_path = pkg_dir / "checkpoint.json"
    key_path = pkg_dir / "checkpoint-key.pem"
    if cp_path.exists() and key_path.exists():
        try:
            cp = json.loads(cp_path.read_text(encoding="utf-8"))
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            pub = serialization.load_pem_public_key(
                key_path.read_bytes())
            if not isinstance(pub, ec.EllipticCurvePublicKey):
                raise ValueError("checkpoint key is not EC P-256")

            def verify_sig(body: bytes, sig: bytes) -> bool:
                try:
                    pub.verify(sig, body, ec.ECDSA(hashes.SHA256()))
                    return True
                except Exception:
                    return False

            cp_report = ac.verify_checkpoint(
                cp, records, verify_sig)
        except Exception as e:
            cp_report = {"ok": False,
                         "problems": [f"checkpoint verify error: {e}"]}
    elif cp_path.exists():
        cp_report = {"ok": False,
                     "problems": ["checkpoint present but "
                                  "checkpoint-key.pem missing"]}
    return records, report, cp_report, None


def record_report_to_trust(record_report: dict, pkg_dir: Path,
                           registry_statuses: dict[str, dict] | None,
                           manifest_data: dict | None) -> dict:
    """Map one W018 record_report to a TM07 ten-dimension ledger."""
    layers = record_report["layers"]
    raw = record_report.get("raw", {})

    photo_sha_matches = None
    if layers["L1_file_integrity"] == "pass":
        photo_sha_matches = True
    elif layers["L1_file_integrity"] == "fail":
        photo_sha_matches = False

    sig_valid = None
    if layers["L2_signature_math"] == "pass":
        sig_valid = True
    elif layers["L2_signature_math"] == "fail":
        sig_valid = False

    key_registration = None
    pub_b64 = (manifest_data or {}).get("public_key") \
        or (manifest_data or {}).get("public_key_b64")
    if registry_statuses and pub_b64 in registry_statuses:
        key_registration = registry_statuses[pub_b64]
    elif layers["L3_device_identity"] == "registered":
        key_registration = {"enrollment_status": "active"}

    app_attest = None
    att_path = pkg_dir / "attestation.json"
    if att_path.exists():
        # operator-supplied App Attest result (from the backend TM04
        # module) - the CLI itself never verifies Apple chains
        try:
            att = json.loads(att_path.read_text(encoding="utf-8"))
            app_attest = {"verified": bool(att.get("verified"))}
            bnd = att.get("photo_key_binding")
        except Exception:
            app_attest, bnd = {"verified": False}, None
    else:
        bnd = None

    time_anchor = None
    if layers["L5_trusted_time"] == "server_anchored":
        time_anchor = {"state": "server_anchored",
                       "anchored_at": (manifest_data or {}).get(
                           "capture_timestamp")}
    elif layers["L5_trusted_time"] in ("device_reported", "cannot_verify"):
        time_anchor = {"state": "unverifiable"}

    location = None
    if layers["L6_location_provenance"] == "signed_in_payload":
        location = {"source": "gnss"}     # a CLAIM - engine caps it
    elif layers["L6_location_provenance"] == "none":
        location = {"source": "none"}

    chain_records, chain_report, cp_report, chain_err = \
        _chain_inputs(pkg_dir)
    if chain_err:
        chain_report = {"valid": False, "problems": [chain_err]}

    derivative_input = None
    deriv_problems = raw.get("derivative_problems") or []
    if isinstance(deriv_problems, list):
        # the W018 record verification reports derivative PROBLEMS
        # (broken links/tamper); "no problems" over a package that
        # declares derivatives means every declared one matched
        manifest_rec = manifest_data or {}
        derivative_input = {
            "all_match": len(deriv_problems) == 0,
            "checked": len(deriv_problems) + (1 if not deriv_problems else 0),
        }

    report = compute_trust({
        "verification_env": "desktop",
        "photo_sha256_matches": photo_sha_matches,
        "signature_verifies": sig_valid,
        "key_registration": key_registration,
        "app_attest": app_attest,
        "photo_key_binding": bnd,
        "time_anchor": time_anchor,
        "location": location,
        "chain_report": chain_report,
        "external_checkpoint": cp_report,
        "derivatives": derivative_input,
    })
    out = report.as_dict()
    out["verdict"] = record_report["verdict"]        # W018's own call
    out["problems"] = record_report.get("problems", [])
    return out
