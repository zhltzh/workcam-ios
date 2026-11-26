"""T13 - Evidence package format v1 (tech-lab experiment).

A self-contained, offline-verifiable evidence bundle:

    package/
      package.json                 # v1 manifest binding everything below
      photos/<photo_id>.jpg        # original photo bytes
      manifests/<photo_id>.json    # signed SignatureManifest per photo
      receipts/time-anchor.json    # optional: server anchor receipt(s)

Binding rules (task brief: photo/metadata/signature/device identity):
- package.json lists every record with its photo file + manifest file +
  location metadata (T02) + capture time + optional anchor receipt ref.
- verify_package() re-computes every hash and re-checks every signature
  from bytes on disk. A tampered byte anywhere in the chain is reported
  with the SPECIFIC record and reason.
- Trust boundary is explicit: package verification proves *math* (bytes
  + signatures). Whether the signing key belongs to a REGISTERED device
  is a separate status field - an unregistered key is never a failure of
  the math, and a valid signature is never proof of genuine hardware.

EXPERIMENTAL - not wired into the production `app` package.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.capture import SignatureManifest
from app.services.serialization import canonical_payload
from app.services.signature import (
    validate_manifest_structure,
    verify_signature_with_details,
)

PACKAGE_VERSION = "1"


class DerivativeRef(BaseModel):
    """A derivative artifact bundled alongside its original (W-08).

    `original_hash` binds the derivative back to the SIGNED original
    (must equal that record's manifest.photo_hash at verification)."""
    derivative_id: str
    file: str                 # relative path inside the package
    content_hash: str          # SHA-256 of the derivative file bytes
    original_hash: str         # the original it derives from
    recipe_engine: str
    recipe_template_version: str
    operation_seq: int


def _safe_rel_path(v: str) -> str:
    """TM44 fuzz finding: the manifest MODEL must refuse traversal
    itself - the ZIP layer (check_zip_safety) guards extraction, but
    a hostile package.json could still point a record's photo_file at
    an arbitrary path INSIDE an unpacked directory tree. Rules: no
    absolute paths, no drive letters, no '..' segments, no
    backslashes, must be relative and inside the package."""
    if not v or v.startswith(("/", "\\")) or len(v) > 1 and v[1] == ":":
        raise ValueError(f"unsafe package path: {v!r}")
    if "\\" in v:
        raise ValueError(f"backslash in package path: {v!r}")
    parts = v.split("/")
    if ".." in parts or any(p == "" for p in parts[:-1]):
        raise ValueError(f"traversal or empty segment in path: {v!r}")
    return v


class PackageRecord(BaseModel):
    record_id: str
    photo_file: str                 # relative path inside the package
    manifest_file: str
    capture_timestamp: str
    location_meta: dict | None = None      # T02 LocationMetadata
    anchor_receipt_file: str | None = None  # optional
    derivatives: list[DerivativeRef] = []   # W-08

    @field_validator("photo_file", "manifest_file",
                    "anchor_receipt_file")
    @classmethod
    def _paths(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _safe_rel_path(v)


class PackageManifest(BaseModel):
    package_version: str = Field(default=PACKAGE_VERSION)
    created_at: str
    device_id: str
    records: list[PackageRecord]
    notes: list[str] = Field(default_factory=list)


class RecordVerification(BaseModel):
    record_id: str
    photo_found: bool
    photo_hash_valid: bool | None      # None = cannot check (missing file)
    manifest_found: bool
    manifest_struct_valid: bool | None  # None = cannot check
    signature_valid: bool | None        # None = cannot check
    key_registered: bool | None         # None = no registry provided
    derivative_problems: list[str] = []  # W-08: broken derivative links
    problems: list[str] = []

    @property
    def passed(self) -> bool:
        # "cannot verify" is NOT "passed" (T18 honesty rule); structural
        # binding problems (record_id mismatch etc.) are also NOT passed
        # even when the individual math checks happen to hold.
        if self.problems:
            return False
        return (
            self.photo_found
            and self.photo_hash_valid is True
            and self.manifest_found
            and self.manifest_struct_valid is True
            and self.signature_valid is True
        )

    @property
    def status(self) -> str:
        if self.passed and (self.key_registered is None or self.key_registered):
            return "pass"
        if self.passed:
            return "pass_unregistered_key"
        return "fail"


class PackageVerification(BaseModel):
    package_version: str | None
    record_results: list[RecordVerification]
    anchor_receipts_valid: bool | None = None
    package_problems: list[str] = []
    derivative_problems: list[dict] = []   # W-08

    @property
    def all_passed(self) -> bool:
        return (
            all(r.passed for r in self.record_results)
            and not self.package_problems
            and not self.derivative_problems
        )


# --------------------------------------------------------------------- build
def build_package(
    target_dir: Path,
    records: list[dict[str, Any]],
    device_id: str,
    created_at: str,
    anchor_receipts: dict[str, Any] | None = None,
    force: bool = False,
) -> PackageManifest:
    """Assemble a package on disk. `records` = list of dicts with keys:
    manifest (SignatureManifest dict), photo_bytes (bytes),
    location_meta (dict | None) and optionally derivatives
    (list of {derivative_id, content_hash, original_hash, recipe_engine,
    recipe_template_version, operation_seq, bytes}).

    Duplicate packing: an existing package.json is NEVER silently
    overwritten - pass force=True to rebuild explicitly."""
    target_dir = Path(target_dir)
    if (target_dir / "package.json").exists() and not force:
        raise ValueError(
            f"package already exists at {target_dir / 'package.json'}; "
            "pass force=True to rebuild (an evidence package is never "
            "silently overwritten)"
        )
    (target_dir / "photos").mkdir(parents=True, exist_ok=True)
    (target_dir / "manifests").mkdir(parents=True, exist_ok=True)
    (target_dir / "derivatives").mkdir(parents=True, exist_ok=True)
    if anchor_receipts:
        (target_dir / "receipts").mkdir(parents=True, exist_ok=True)

    pkg_records: list[PackageRecord] = []
    for r in records:
        manifest = SignatureManifest.model_validate(r["manifest"])
        photo_bytes: bytes = r["photo_bytes"]
        anchor_file = None
        if anchor_receipts and manifest.record_id in anchor_receipts:
            anchor_file = f"receipts/{manifest.record_id}.json"
            (target_dir / anchor_file).write_text(
                json.dumps(anchor_receipts[manifest.record_id], indent=2),
                encoding="utf-8",
            )
        photo_file = f"photos/{manifest.record_id}.jpg"
        manifest_file = f"manifests/{manifest.record_id}.json"
        (target_dir / photo_file).write_bytes(photo_bytes)
        (target_dir / manifest_file).write_text(
            json.dumps(manifest.model_dump(), indent=2), encoding="utf-8"
        )

        derivative_refs: list[DerivativeRef] = []
        for d in r.get("derivatives", []):
            d_file = f"derivatives/{d['derivative_id']}.jpg"
            (target_dir / d_file).write_bytes(d["bytes"])
            # the packaged derivative is bound to the SIGNED original of
            # THIS record - anything else is a broken back-link
            if d["original_hash"] != manifest.photo_hash:
                raise ValueError(
                    f"derivative {d['derivative_id']} claims original "
                    f"{d['original_hash'][:12]}... but record "
                    f"{manifest.record_id} signs {manifest.photo_hash[:12]}..."
                )
            derivative_refs.append(DerivativeRef(
                derivative_id=d["derivative_id"],
                file=d_file,
                content_hash=d["content_hash"],
                original_hash=d["original_hash"],
                recipe_engine=d["recipe_engine"],
                recipe_template_version=d["recipe_template_version"],
                operation_seq=d["operation_seq"],
            ))

        pkg_records.append(PackageRecord(
            record_id=manifest.record_id,
            photo_file=photo_file,
            manifest_file=manifest_file,
            capture_timestamp=manifest.capture_timestamp,
            location_meta=r.get("location_meta"),
            anchor_receipt_file=anchor_file,
            derivatives=derivative_refs,
        ))

    manifest_out = PackageManifest(
        created_at=created_at, device_id=device_id, records=pkg_records,
        notes=["experimental tech-lab package v1"],
    )
    (target_dir / "package.json").write_text(
        json.dumps(manifest_out.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest_out


# ------------------------------------------------------------------- verify
def verify_package(
    package_dir: Path,
    key_registry=None,             # app key registry (optional, in tests)
    anchor_public_key_loader=None,  # callable key_id -> pubkey (optional)
) -> PackageVerification:
    """Offline package verification. NEVER turns 'cannot verify' into
    'pass'. Optional registries refine the trust report but a package
    without them still verifies its MATH."""
    problems: list[str] = []
    pkg_path = package_dir / "package.json"
    if not pkg_path.exists():
        return PackageVerification(
            package_version=None, record_results=[], package_problems=["package.json missing"]
        )
    try:
        pkg = PackageManifest.model_validate(json.loads(pkg_path.read_text(encoding="utf-8")))
    except Exception as e:
        return PackageVerification(
            package_version=None, record_results=[],
            package_problems=[f"package.json unreadable: {e}"],
        )

    results: list[RecordVerification] = []
    for rec in pkg.records:
        rv = RecordVerification(
            record_id=rec.record_id,
            photo_found=False, photo_hash_valid=None,
            manifest_found=False, manifest_struct_valid=None,
            signature_valid=None, key_registered=None,
        )

        # 1. photo bytes
        photo_path = package_dir / rec.photo_file
        if photo_path.exists():
            rv.photo_found = True
        else:
            rv.problems.append("photo file missing")

        # 2. signed manifest
        manifest_path = package_dir / rec.manifest_file
        manifest: SignatureManifest | None = None
        if manifest_path.exists():
            rv.manifest_found = True
            try:
                manifest = SignatureManifest.model_validate(
                    json.loads(manifest_path.read_text(encoding="utf-8"))
                )
            except Exception as e:
                rv.problems.append(f"manifest unreadable: {e}")
                manifest = None
        else:
            rv.problems.append("manifest file missing")

        # 3. cross-binding: manifest record_id must match the listing
        if manifest is not None and manifest.record_id != rec.record_id:
            rv.problems.append(
                f"record_id mismatch: package lists {rec.record_id}, "
                f"manifest signs {manifest.record_id}"
            )

        # 4. structure validation
        if manifest is not None:
            errs = validate_manifest_structure(manifest)
            rv.manifest_struct_valid = not errs
            if errs:
                rv.problems.extend(errs)

        # 5. photo hash against the SIGNED hash
        if rv.photo_found and manifest is not None and rv.manifest_struct_valid:
            actual = hashlib.sha256(photo_path.read_bytes()).hexdigest()
            rv.photo_hash_valid = actual == manifest.photo_hash
            if not rv.photo_hash_valid:
                rv.problems.append("photo bytes do not match signed photo_hash")

        # 6. signature math (verifies with the key INSIDE the manifest)
        if manifest is not None and rv.manifest_struct_valid:
            ok, msg = verify_signature_with_details(manifest)
            rv.signature_valid = ok
            if not ok:
                rv.problems.append(f"signature invalid: {msg}")

            # trust boundary: is that key registered? (separate from math)
            if key_registry is not None:
                from app.services.key_registry import KeyRegistrationStatus
                lookup = key_registry.lookup(manifest.public_key)
                rv.key_registered = lookup.registration_status == KeyRegistrationStatus.REGISTERED

        # 7. W-08: derivative back-links. A derivative with wrong bytes
        # or a broken link to the signed original is a derivative problem
        # (reported separately - it never invalidates the ORIGINAL's
        # signature math, and the original's pass never blesses a broken
        # derivative).
        for dref in rec.derivatives:
            dpath = package_dir / dref.file
            if not dpath.exists():
                rv.derivative_problems.append(
                    f"derivative file missing: {dref.file}")
                continue
            actual = hashlib.sha256(dpath.read_bytes()).hexdigest()
            if actual != dref.content_hash:
                rv.derivative_problems.append(
                    f"derivative bytes tampered: {dref.file} "
                    f"(listed {dref.content_hash[:12]}..., actual {actual[:12]}...)")
            if manifest is not None and dref.original_hash != manifest.photo_hash:
                rv.derivative_problems.append(
                    f"derivative {dref.derivative_id} back-link does not "
                    f"match this record's signed photo_hash")

        results.append(rv)

    # 7. anchor receipts (optional; verified only if a key loader is given)
    anchor_ok: bool | None = None
    if anchor_public_key_loader is not None:
        anchor_ok = True
        for rec in pkg.records:
            if not rec.anchor_receipt_file:
                continue
            receipt_path = package_dir / rec.anchor_receipt_file
            if not receipt_path.exists():
                anchor_ok = False
                problems.append(f"anchor receipt missing for {rec.record_id}")
                continue
            # structural presence check; full anchor verification is the
            # T18 verifier's job (it owns the time-anchor signature path)
    return PackageVerification(
        package_version=pkg.package_version,
        record_results=results,
        anchor_receipts_valid=anchor_ok,
        package_problems=problems,
        derivative_problems=[
            {"record_id": rv.record_id, "problems": rv.derivative_problems}
            for rv in results if rv.derivative_problems
        ],
    )
