#!/usr/bin/env python3
"""WorkCam standalone offline evidence verifier (T18).

Verifies a tech-lab evidence package WITHOUT any server, network or
backend process. Pure file + math verification.

Usage:
    python tools/tech-lab/validation/evidence_verifier.py <package_dir> \
        [--json report.json] \
        [--anchor-pubkey server_key.pem --anchor-key-id test-key-001]

Output:
    human-readable report on stdout + optional machine JSON.
    Statuses: pass | pass_unregistered_key | fail | cannot_verify.

Exit codes:
    0  every record passed (unregistered keys count as pass with warning)
    1  at least one record failed or could not be verified

HONESTY RULES:
    - 'cannot verify' is never reported as a pass.
    - An unregistered signing key is a WARNING about identity, not a
      signature failure and never a proof of genuine hardware.
    - Anchor receipts are verified on the signature path only; whether
      the server still stores the anchor is out of scope offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# make backend imports work when run from repo root / tools dir
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from techlab.anchor_verifier import verify_anchor_receipt_standalone  # noqa: E402
from techlab.evidence_package import verify_package  # noqa: E402

EXIT_OK = 0
EXIT_FAIL = 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="evidence_verifier",
                                     description="Offline WorkCam evidence verifier")
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("--json", type=Path, default=None,
                        help="also write a machine-readable JSON report")
    parser.add_argument("--anchor-pubkey", type=Path, default=None,
                        help="trusted time-anchor server public key (PEM)")
    parser.add_argument("--anchor-key-id", default=None,
                        help="key_id expected in anchor receipts")
    args = parser.parse_args(argv[1:])

    pkg_dir: Path = args.package_dir
    print("WorkCam 离线证据验证器 (standalone, no server, no network)")
    print(f"package: {pkg_dir}")

    if not pkg_dir.exists():
        print("FAIL: package directory does not exist")
        return EXIT_FAIL

    # 1. package records (T13 verifier)
    result = verify_package(pkg_dir)

    # 2. anchor receipts, if a trusted key was provided
    anchor_lines: list[str] = []
    anchor_results = []
    if args.anchor_pubkey:
        pem = args.anchor_pubkey.read_text(encoding="utf-8")
        pkg_manifest_path = pkg_dir / "package.json"
        if pkg_manifest_path.exists():
            try:
                pkg = json.loads(pkg_manifest_path.read_text(encoding="utf-8"))
                for rec in pkg.get("records", []):
                    ref = rec.get("anchor_receipt_file")
                    if not ref:
                        continue
                    receipt_path = pkg_dir / ref
                    if not receipt_path.exists():
                        anchor_results.append(
                            {"record_id": rec["record_id"], "valid": False,
                             "reason": "cannot_verify: receipt file missing"})
                        continue
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    outcome = verify_anchor_receipt_standalone(
                        receipt, pem, key_id_expected=args.anchor_key_id)
                    outcome["record_id"] = rec["record_id"]
                    anchor_results.append(outcome)
            except Exception as e:
                anchor_results.append({"valid": False,
                                       "reason": f"cannot_verify: {e}"})

    # 3. human report
    passed = failed = cannot = 0
    print(f"package version: {result.package_version}")
    for p in result.package_problems:
        print(f"  [PACKAGE-PROBLEM] {p}")
    for rv in result.record_results:
        if not rv.photo_found or not rv.manifest_found or rv.signature_valid is None:
            state = "CANNOT_VERIFY"
            cannot += 1
        elif rv.status == "pass_unregistered_key":
            state = "PASS_UNREGISTERED_KEY (identity unknown)"
            passed += 1
        elif rv.status == "pass":
            # no trust registry was provided: signature math passed, but
            # device identity could not be checked - say so honestly.
            state = "PASS (identity not checked: no trust registry provided)"
            passed += 1
        else:
            state = "FAIL"
            failed += 1
        print(f"  [{rv.record_id}] {state}")
        for problem in rv.problems:
            print(f"      - {problem}")
        print("      - NOTE: a valid signature proves byte integrity only; "
              "it is NOT proof that the capture scene was genuine")

    for a in anchor_results:
        mark = "PASS" if a.get("valid") else "FAIL"
        print(f"  [anchor:{a.get('record_id', '?')}] {mark} - {a.get('reason', '')}")

    print(f"\n总计: {len(result.record_results)} 记录 | "
          f"{passed} 通过 | {failed} 失败 | {cannot} 无法验证")

    # 4. machine JSON
    report = {
        "package_version": result.package_version,
        "package_problems": result.package_problems,
        "records": [r.model_dump() | {"status": r.status} for r in result.record_results],
        "anchor_receipts": anchor_results,
        "overall": result.all_passed,
    }
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        print(f"JSON report: {args.json}")

    return EXIT_OK if (failed == 0 and cannot == 0 and not result.package_problems) else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main(sys.argv))
