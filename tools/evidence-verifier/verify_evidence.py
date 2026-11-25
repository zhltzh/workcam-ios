#!/usr/bin/env python3
"""WorkCam public evidence package verifier CLI (B02-W018).

Standalone OFFLINE verifier for exported WorkCam evidence packages
(directory or .zip). No WorkCam account, no server, no network call:
every byte stays on the machine running the tool. Photo files are
never uploaded anywhere - the tool performs file hashing and signature
math only.

Evolved from the tech-lab T13/T18 standalone verifier
(tools/tech-lab/validation/evidence_verifier.py, kept in place for the
tech-lab experiment). This CLI is the hardened public-facing tool and
adds: ZIP handling with zip-slip protection, batch verification, the
six-layer trust report and a strict exit-code contract.

SIX VERIFICATION LAYERS (reported separately, never conflated):
  L1 file integrity      files present, SHA-256 of photo bytes matches
                         the signed photo_hash, package.json listing
                         <-> manifest record_id binding
  L2 signature math      public key format (65-byte uncompressed P-256
                         point), DER signature structure, exact
                         canonical payload reconstruction, ECDSA P-256
                         verification
  L3 device identity     is the signing key in a LOCAL trust registry
                         (offline JSON file; no online lookup is ever
                         performed)
  L4 hardware provenance Secure Enclave / hardware key attestation -
                         NOT verifiable offline; never claimed proven
  L5 trusted time       anchor receipt signature (optional) versus a
                         plain device-reported timestamp
  L6 location provenance coordinates fixed by the signature versus
                         absent; the coordinate SOURCE (gps/manual)
                         can never be proven offline

HONESTY CONTRACT:
  A valid signature proves byte integrity and signature math ONLY.
  It is NOT proof that the photo content is genuine, NOT proof of
  hardware key provenance and NOT a legal certification.
  'cannot_verify' is never reported as a pass.
  DEVICE_PENDING: this tool is validated against public test vectors
  (test-vectors/fixed-vectors.json + generated TEST-ONLY keys); no
  real Mac device package has been cross-validated yet.

Exit codes (full spec in docs/evidence-verifier/b02-w018-report-format.md):
  0  every package passed (signature math valid for every record;
     unknown states in layers L3-L6 are reported but do not fail)
  1  verification failed for at least one record or package
     (tampering, missing files, broken binding, invalid anchor
     receipt, orphan files not listed in the manifest)
  2  package format corrupt (missing/unreadable package.json, broken
     zip, zip-slip path traversal, unsafe entry, unzip size guard
     triggered, input path missing) - also used by argparse for CLI
     usage errors
"""

from __future__ import annotations

import argparse
import glob as _glob
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

# make backend imports work no matter where the CLI is launched from
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.key_registry import InMemoryKeyRegistry  # noqa: E402
from techlab.anchor_verifier import verify_anchor_receipt_standalone  # noqa: E402
from techlab.evidence_package import PackageManifest, verify_package  # noqa: E402

TOOL_NAME = "workcam-evidence-verifier"
TASK_ID = "B02-W018"
TOOL_VERSION = "1.0.0"

EXIT_OK = 0
EXIT_VERIFY_FAILED = 1
EXIT_CORRUPT = 2

DEFAULT_MAX_UNCOMPRESSED_MB = 1024
_BOMB_COMPRESSION_RATIO = 200
_BOMB_MIN_INTERESTING_BYTES = 1024 * 1024  # only ratio-check files > 1 MiB

DISCLAIMER_LINES = [
    "This report is OFFLINE VERIFICATION MATH ONLY.",
    "A valid signature proves byte integrity and signature math.",
    "It is NOT proof that the photo content is genuine.",
    "It is NOT proof of hardware key provenance (Secure Enclave).",
    "It is NOT a legal certification of anything.",
]

DEVICE_PENDING_NOTE = (
    "DEVICE_PENDING: validated against public test vectors only; "
    "no real Mac device evidence package has been cross-validated yet"
)

LOCATION_PROVENANCE_NOTE = (
    "the signature only fixes the coordinate BYTES at signing time; "
    "the coordinate source (gps/manual) and its truthfulness cannot "
    "be proven offline"
)

HARDWARE_PROVENANCE_NOTE = (
    "hardware key provenance (Secure Enclave attestation) cannot be "
    "verified from package bytes offline; a valid signature is never "
    "proof of a hardware-backed key"
)


# ---------------------------------------------------------------------------
# ZIP safety (zip-slip / bombs / collisions)
# ---------------------------------------------------------------------------
def check_zip_safety(zf: zipfile.ZipFile, max_total_bytes: int) -> list[str]:
    """Static inspection of every zip entry BEFORE any extraction.

    Rejects: absolute paths, drive letters, backslashes, '..' path
    components, case-insensitive destination collisions (Windows),
    per-file and total uncompressed-size bombs, extreme compression
    ratios. Returns a list of problems ([] = safe).

    Note: CPython 3.12's zipfile READ side normalizes '\\' in entry
    names to '/', so hostile backslash names normally resurface as
    '..' traversal entries and are caught by that guard; the explicit
    backslash check stays as defense for other ZipFile-compatible
    readers / future stdlib behavior."""
    problems: list[str] = []
    seen_lower: dict[str, str] = {}
    total = 0
    for info in zf.infolist():
        name = info.filename
        # path traversal / platform separators
        if name.startswith("/") or name.startswith("\\"):
            problems.append(f"unsafe zip entry (absolute path): {name!r}")
            continue
        if "\\" in name:
            problems.append(f"unsafe zip entry (backslash separator): {name!r}")
            continue
        if len(name) > 1 and name[1] == ":":
            problems.append(f"unsafe zip entry (drive letter): {name!r}")
            continue
        parts = PurePosixPath(name).parts
        if ".." in parts:
            problems.append(f"unsafe zip entry (path traversal '..'): {name!r}")
            continue
        if not info.is_dir():
            # case-insensitive collision: two entries writing one path
            key = name.lower()
            if key in seen_lower and seen_lower[key] != name:
                problems.append(
                    f"zip entries collide case-insensitively: "
                    f"{seen_lower[key]!r} vs {name!r}"
                )
            seen_lower[key] = name
            # size guards
            if info.file_size > max_total_bytes:
                problems.append(
                    f"zip entry exceeds size guard "
                    f"({info.file_size} > {max_total_bytes} bytes): {name!r}"
                )
            if (
                info.file_size > _BOMB_MIN_INTERESTING_BYTES
                and info.compress_size > 0
                and info.file_size > info.compress_size * _BOMB_COMPRESSION_RATIO
            ):
                problems.append(
                    f"zip entry has extreme compression ratio "
                    f"({info.file_size} -> {info.compress_size} bytes): {name!r}"
                )
            total += info.file_size
    if total > max_total_bytes:
        problems.append(
            f"zip total uncompressed size exceeds guard "
            f"({total} > {max_total_bytes} bytes)"
        )
    return problems


def safe_extract_zip(
    zip_path: Path, dest: Path, max_total_bytes: int
) -> list[str]:
    """Extract a pre-checked zip into `dest`. Problems found here are
    treated as package corruption (exit 2)."""
    problems: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            problems = check_zip_safety(zf, max_total_bytes)
            if problems:
                return problems
            for info in zf.infolist():
                if info.is_dir():
                    continue
                zf.extract(info, dest)
    except zipfile.BadZipFile as e:
        problems.append(f"not a valid zip file: {e}")
    except (OSError, RuntimeError, zipfile.LargeZipFile) as e:
        problems.append(f"zip extraction failed: {e}")
    return problems


def find_package_root(extracted: Path) -> tuple[Path | None, list[str]]:
    """package.json must sit at the zip root, or inside exactly one
    top-level directory (the common 'zip a folder' layout). Anything
    else is ambiguous -> corrupt."""
    problems: list[str] = []
    if (extracted / "package.json").is_file():
        return extracted, problems
    top = [p for p in extracted.iterdir() if p.is_dir()]
    top_files = [p for p in extracted.iterdir() if p.is_file()]
    if top_files:
        problems.append(
            "package.json not found at zip root; unexpected top-level "
            f"files: {[p.name for p in top_files]}"
        )
        return None, problems
    if len(top) == 1 and (top[0] / "package.json").is_file():
        return top[0], []
    problems.append(
        "package.json not found at zip root or inside a single "
        "top-level directory"
    )
    return None, problems


# ---------------------------------------------------------------------------
# local trust registry (L3) - an OFFLINE JSON file, operator-maintained
# ---------------------------------------------------------------------------
def load_local_registry(path: Path) -> InMemoryKeyRegistry:
    """Registry JSON format:
    {"keys": [{"public_key_b64": "...", "device_id": "..."}, ...]}
    The registry is a LOCAL trust decision by whoever runs the tool -
    the CLI never performs any online lookup."""
    data = json.loads(path.read_text(encoding="utf-8"))
    reg = InMemoryKeyRegistry()
    for item in data.get("keys", []):
        reg.register(item["public_key_b64"], item.get("device_id", "unknown"))
    return reg


# ---------------------------------------------------------------------------
# six-layer evaluation
# ---------------------------------------------------------------------------
def _load_manifest_data(pkg_dir: Path, manifest_file: str) -> dict | None:
    p = pkg_dir / manifest_file
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _location_meta_notes(location_meta: dict | None) -> tuple[list[str], list[str]]:
    """Returns (notes, problems) for the T02 location_meta block, if any.
    Reuses techlab.location_meta.LocationMetadata for the trust label -
    'manual' locations must never look like verified positions."""
    if location_meta is None:
        return [], []
    try:
        from techlab.location_meta import LocationMetadata

        meta = LocationMetadata.model_validate(location_meta)
        return [
            f"location_meta source={meta.source.value}, "
            f"grade={meta.grade().value}, label: {meta.trust_label()}"
        ], []
    except Exception as e:
        return [], [f"location_meta invalid: {e}"]


def build_record_report(
    rv,                      # techlab.evidence_package.RecordVerification
    rec,                     # techlab.evidence_package.PackageRecord
    pkg_dir: Path,
    registry_used: bool,
    anchor_outcome: dict | None,
    anchor_key_provided: bool,
) -> dict:
    """Compute the six layer statuses + verdict for one record.

    Layer semantics (must stay honest, see module docstring):
      L1 fail  = missing-file-blanket is impossible: a present file whose
                 bytes/tamper binding is broken (or a record_id mismatch)
      L1 cannot_verify = photo or manifest file missing
      L2 fail  = malformed structure (bad key/DER/fields) or signature
                 verification failed - malformed input is a FAIL, not a
                 cannot-verify
      L2 cannot_verify = manifest missing/unreadable so no math was run
      L5 fail  = an anchor receipt is claimed but its signature is bad
      L5 cannot_verify = receipt claimed but no trusted anchor key given
    """
    manifest_data = _load_manifest_data(pkg_dir, rec.manifest_file)
    notes: list[str] = [DEVICE_PENDING_NOTE]

    # ---- L1 file integrity
    if not rv.photo_found or not rv.manifest_found:
        l1 = "cannot_verify"
    elif rv.photo_hash_valid is False:
        l1 = "fail"
    elif any("record_id mismatch" in p for p in rv.problems):
        l1 = "fail"
    elif rv.photo_hash_valid is None:
        l1 = "cannot_verify"
    else:
        l1 = "pass"

    # ---- L2 signature math (key format + DER + payload + ECDSA)
    if not rv.manifest_found:
        l2 = "cannot_verify"
    elif rv.manifest_struct_valid is False:
        l2 = "fail"
    elif rv.signature_valid is False:
        l2 = "fail"
    elif rv.signature_valid is None:
        l2 = "cannot_verify"
    else:
        l2 = "pass"
        if manifest_data is not None:
            notes.append(
                f"signature: ECDSA P-256 (secp256r1) + SHA-256 over "
                f"canonical payload v{manifest_data.get('version', '?')}"
            )

    # ---- L3 device identity (local registry only)
    if not registry_used or rv.key_registered is None:
        l3 = "not_checked"
        if not registry_used:
            notes.append("identity not checked: no --registry provided")
    elif rv.key_registered:
        l3 = "registered"
    else:
        l3 = "unregistered"
        notes.append(
            "signing key is NOT in the provided local registry; the "
            "signature math passing says nothing about who owned the key"
        )

    # ---- L4 hardware provenance: never claimable offline
    l4 = "not_verifiable_offline"
    notes.append(HARDWARE_PROVENANCE_NOTE)

    # ---- L5 trusted time
    if rec.anchor_receipt_file is None:
        l5 = "device_reported"
        notes.append("no anchor receipt: timestamp is device-reported only")
    elif not anchor_key_provided:
        l5 = "cannot_verify"
        notes.append(
            "anchor receipt present but no trusted anchor public key "
            "provided (--anchor-pubkey)"
        )
    elif anchor_outcome is not None and anchor_outcome.get("valid"):
        l5 = "server_anchored"
    else:
        l5 = "fail"
        if anchor_outcome is not None:
            notes.append(f"anchor receipt rejected: {anchor_outcome.get('reason', '')}")

    # ---- L6 location provenance
    l6_notes, l6_problems = _location_meta_notes(rec.location_meta)
    if manifest_data is None:
        l6 = "cannot_verify"
    elif manifest_data.get("location") is not None:
        l6 = "signed_in_payload"
    else:
        l6 = "none"
    notes.extend(l6_notes)
    notes.append(LOCATION_PROVENANCE_NOTE)

    problems = list(rv.problems) + l6_problems

    # ---- verdict: strictly derived from the layers above
    if "fail" in (l1, l2, l5) or l6_problems:
        verdict = "fail"
    elif "cannot_verify" in (l1, l2, l5):
        verdict = "cannot_verify"
    elif l3 == "unregistered":
        verdict = "pass_unregistered_key"
    else:
        verdict = "pass"

    return {
        "record_id": rv.record_id,
        "verdict": verdict,
        "layers": {
            "L1_file_integrity": l1,
            "L2_signature_math": l2,
            "L3_device_identity": l3,
            "L4_hardware_provenance": l4,
            "L5_trusted_time": l5,
            "L6_location_provenance": l6,
        },
        "problems": problems,
        "notes": notes,
        "raw": rv.model_dump() | {"status": rv.status},
    }


# ---------------------------------------------------------------------------
# orphan files: manifest consistency (every file must be listed)
# ---------------------------------------------------------------------------
def find_orphan_files(pkg_dir: Path, pkg: PackageManifest) -> list[str]:
    """Files physically present in the package but NOT referenced by
    package.json. A package carrying extra unlisted payloads is not
    consistent with its manifest -> package-level failure.

    TM05/TM06 chain conventions are part of the PACKAGE STRUCTURE
    (chain + external checkpoint + its verification key + the optional
    server-side App Attest result) and are whitelisted here - they are
    inputs to the trust bridge, not unlisted payloads."""
    referenced = {"package.json"}
    referenced.update({
        "chain.json", "checkpoint.json", "checkpoint-key.pem",
        "attestation.json"})
    for rec in pkg.records:
        referenced.add(rec.photo_file.replace("\\", "/"))
        referenced.add(rec.manifest_file.replace("\\", "/"))
        if rec.anchor_receipt_file:
            referenced.add(rec.anchor_receipt_file.replace("\\", "/"))
        for d in rec.derivatives:
            referenced.add(d.file.replace("\\", "/"))
    orphans = []
    for f in sorted(pkg_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(pkg_dir).as_posix()
            if rel not in referenced:
                orphans.append(rel)
    return orphans


# ---------------------------------------------------------------------------
# per-package verification
# ---------------------------------------------------------------------------
def verify_package_dir(pkg_dir: Path, args, kind: str) -> dict:
    """Full verification of one extracted/decoded package directory."""
    pkg_path = pkg_dir / "package.json"
    try:
        pkg = PackageManifest.model_validate(
            json.loads(pkg_path.read_text(encoding="utf-8"))
        )
    except Exception as e:
        return {
            "input": str(pkg_dir),
            "kind": kind,
            "outcome": "corrupt",
            "reason": f"package.json missing or unreadable: {e}",
            "package_problems": [f"package.json unreadable: {e}"],
            "records": [],
        }

    registry = load_local_registry(args.registry) if args.registry else None
    result = verify_package(pkg_dir, key_registry=registry)

    # anchor receipts (L5) - reuses the T18 standalone signature path
    anchor_key_provided = args.anchor_pubkey is not None
    anchor_outcomes: dict[str, dict] = {}
    if anchor_key_provided:
        pem = args.anchor_pubkey.read_text(encoding="utf-8")
        for rec in pkg.records:
            if not rec.anchor_receipt_file:
                continue
            receipt_path = pkg_dir / rec.anchor_receipt_file
            if not receipt_path.exists():
                anchor_outcomes[rec.record_id] = {
                    "valid": False, "signature_valid": False,
                    "key_id_matches": None,
                    "reason": "cannot_verify: receipt file missing",
                }
                continue
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                outcome = verify_anchor_receipt_standalone(
                    receipt, pem, key_id_expected=args.anchor_key_id
                )
            except Exception as e:
                outcome = {
                    "valid": False, "signature_valid": False,
                    "key_id_matches": None, "reason": f"cannot_verify: {e}",
                }
            anchor_outcomes[rec.record_id] = outcome

    record_reports = []
    for rv, rec in zip(result.record_results, pkg.records):
        record_reports.append(
            build_record_report(
                rv, rec, pkg_dir,
                registry_used=registry is not None,
                anchor_outcome=anchor_outcomes.get(rec.record_id),
                anchor_key_provided=anchor_key_provided,
            )
        )

    package_problems = list(result.package_problems)
    orphans = find_orphan_files(pkg_dir, pkg)
    for o in orphans:
        package_problems.append(f"orphan file not listed in package.json: {o}")

    derivative_problems = result.derivative_problems

    any_bad = (
        any(r["verdict"] in ("fail", "cannot_verify") for r in record_reports)
        or bool(package_problems)
        or bool(derivative_problems)
    )
    outcome = "fail" if any_bad else "pass"

    return {
        "input": str(pkg_dir),
        "kind": kind,
        "outcome": outcome,
        "package_version": result.package_version,
        "device_id": pkg.device_id,
        "records": record_reports,
        "package_problems": package_problems,
        "derivative_problems": derivative_problems,
        "anchor_receipts": [
            {"record_id": k, **v} for k, v in anchor_outcomes.items()
        ],
    }


def verify_one_input(input_path: Path, args) -> dict:
    kind = "zip" if input_path.suffix.lower() == ".zip" else "dir"
    max_bytes = args.max_uncompressed_mb * 1024 * 1024

    if not input_path.exists():
        return {
            "input": str(input_path), "kind": kind, "outcome": "corrupt",
            "reason": "input path does not exist",
            "package_problems": ["input path does not exist"],
            "records": [],
        }

    if kind == "zip":
        extracted = Path(tempfile.mkdtemp(prefix="wcv-extract-"))
        try:
            problems = safe_extract_zip(input_path, extracted, max_bytes)
            if problems:
                return {
                    "input": str(input_path), "kind": kind,
                    "outcome": "corrupt",
                    "reason": "; ".join(problems),
                    "package_problems": problems, "records": [],
                }
            pkg_root, root_problems = find_package_root(extracted)
            if pkg_root is None:
                return {
                    "input": str(input_path), "kind": kind,
                    "outcome": "corrupt",
                    "reason": "; ".join(root_problems),
                    "package_problems": root_problems, "records": [],
                }
            report = verify_package_dir(pkg_root, args, kind)
            report["input"] = str(input_path)
            return report
        finally:
            if not args.keep_extracted:
                shutil.rmtree(extracted, ignore_errors=True)
            else:
                print(f"[note] extracted zip kept at: {extracted}")

    return verify_package_dir(input_path, args, kind)


# ---------------------------------------------------------------------------
# human report
# ---------------------------------------------------------------------------
_LAYER_LABELS = {
    "L1_file_integrity": "L1 file integrity      ",
    "L2_signature_math": "L2 signature math      ",
    "L3_device_identity": "L3 device identity     ",
    "L4_hardware_provenance": "L4 hardware provenance ",
    "L5_trusted_time": "L5 trusted time        ",
    "L6_location_provenance": "L6 location provenance ",
}


def human_report(reports: list[dict], summary: dict, exit_code: int) -> str:
    lines: list[str] = []
    lines.append(
        f"{TOOL_NAME} {TOOL_VERSION} (task {TASK_ID}) - "
        "offline, no account, no network"
    )
    lines.append("input photos are hashed locally and never uploaded")
    for d in DISCLAIMER_LINES:
        lines.append(f"  ! {d}")
    lines.append(f"  ! {DEVICE_PENDING_NOTE}")
    lines.append("")

    for i, rep in enumerate(reports, 1):
        lines.append(f"== Package {i}/{len(reports)}: {rep['input']} ==")
        lines.append(f"  kind: {rep['kind']}")
        if rep["outcome"] == "corrupt":
            lines.append(f"  PACKAGE CORRUPT: {rep.get('reason', '')}")
            lines.append("")
            continue
        lines.append(
            f"  package version: {rep.get('package_version')} | "
            f"device_id: {rep.get('device_id')}"
        )
        for r in rep["records"]:
            lines.append(f"  [record {r['record_id']}] verdict: {r['verdict'].upper()}")
            for key in (
                "L1_file_integrity", "L2_signature_math", "L3_device_identity",
                "L4_hardware_provenance", "L5_trusted_time", "L6_location_provenance",
            ):
                lines.append(f"    {_LAYER_LABELS[key]}: {r['layers'][key].upper()}")
            for p in r["problems"]:
                lines.append(f"    problem: {p}")
            for n in r["notes"]:
                lines.append(f"    note: {n}")
            lines.append(
                "    NOTE: a valid signature proves byte integrity only; "
                "it is NOT proof that the photo content was genuine, NOT "
                "proof of hardware provenance, and NOT a legal certification"
            )
        for p in rep["package_problems"]:
            lines.append(f"  [PACKAGE-PROBLEM] {p}")
        for d in rep["derivative_problems"]:
            for p in d.get("problems", []):
                lines.append(f"  [DERIVATIVE-PROBLEM] {d.get('record_id')}: {p}")
        for a in rep.get("anchor_receipts", []):
            mark = "PASS" if a.get("valid") else "FAIL"
            lines.append(f"  [anchor:{a['record_id']}] {mark} - {a.get('reason', '')}")
        lines.append(f"  package outcome: {rep['outcome'].upper()}")
        lines.append("")

    lines.append(
        f"Summary: {summary['packages']} packages | "
        f"{summary['passed']} passed | {summary['failed']} failed | "
        f"{summary['corrupt']} corrupt"
    )
    lines.append(f"exit code: {exit_code}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Offline WorkCam evidence package verifier (B02-W018). "
            "Verifies exported evidence packages (directories or .zip) "
            "without any account or network access."
        ),
    )
    parser.add_argument(
        "packages", nargs="+", type=Path,
        help="evidence package(s): directories or .zip files (batch OK; "
             "glob patterns like *.zip are expanded)",
    )
    parser.add_argument("--json", type=Path, default=None,
                        help="also write a machine-readable JSON report")
    parser.add_argument("--report", type=Path, default=None,
                        help="also write the human report to a text file")
    parser.add_argument("--registry", type=Path, default=None,
                        help="local trust registry JSON for L3 device "
                             "identity checks (offline file)")
    parser.add_argument("--anchor-pubkey", type=Path, default=None,
                        help="trusted time-anchor server public key (PEM)")
    parser.add_argument("--anchor-key-id", default=None,
                        help="key_id expected inside anchor receipts")
    parser.add_argument("--keep-extracted", action="store_true",
                        help="keep the temporary extraction directory of "
                             "each zip (debugging)")
    parser.add_argument("--max-uncompressed-mb", type=int,
                        default=DEFAULT_MAX_UNCOMPRESSED_MB,
                        help="maximum total uncompressed zip size in MiB "
                             f"(default {DEFAULT_MAX_UNCOMPRESSED_MB})")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # expand globs (Windows shells do not do this for us)
    inputs: list[Path] = []
    for pattern in args.packages:
        if any(ch in str(pattern) for ch in "*?"):
            matches = sorted(_glob.glob(str(pattern)))
            inputs.extend(Path(m) for m in matches)
        else:
            inputs.append(pattern)

    reports: list[dict] = []
    for input_path in inputs:
        reports.append(verify_one_input(input_path, args))

    n_pass = sum(1 for r in reports if r["outcome"] == "pass")
    n_fail = sum(1 for r in reports if r["outcome"] == "fail")
    n_corrupt = sum(1 for r in reports if r["outcome"] == "corrupt")
    summary = {
        "packages": len(reports), "passed": n_pass,
        "failed": n_fail, "corrupt": n_corrupt,
    }
    if n_corrupt:
        exit_code = EXIT_CORRUPT
    elif n_fail:
        exit_code = EXIT_VERIFY_FAILED
    else:
        exit_code = EXIT_OK

    text = human_report(reports, summary, exit_code)
    print(text)

    if args.json:
        payload = {
            "tool": TOOL_NAME,
            "task": TASK_ID,
            "version": TOOL_VERSION,
            "offline": True,
            "disclaimer": DISCLAIMER_LINES,
            "device_pending": True,
            "packages": reports,
            "summary": summary,
            "exit_code": exit_code,
        }
        args.json.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"JSON report: {args.json}")
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
        print(f"text report: {args.report}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
