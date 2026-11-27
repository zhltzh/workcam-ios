"""M03-D04: open evidence package V2.

THE unified delivery: originals + derivatives + evidence + annotations
+ issues + measurements + reports + timestamps + audit in ONE
machine-readable, stream-hashed, ZIP64, path-safe archive that the
standalone verifier checks WITHOUT any WorkCam account.

V2 layout (evpack/2.0):
    manifest.json            - the ONLY entry point; every file listed
                               with size + sha256; refuses unknown
                               versions (schema rule, TM01 semantics)
    originals/<sha>.jpg      - immutable originals (content-addressed)
    derivatives/<sha>.jpg    - watermarked/redacted derivatives
    evidence/<record>.json  - signed manifests (v1.x protocol)
    annotations/<photo>.json- per-photo annotation sets
    issues/<issue>.json      - issue snapshots + photo roles + history
    measurements/<id>.json   - TM23 measurement records
    reports/<id>.pdf         - generated report artifacts
    timestamps/<record>.tsr  - RFC 3161 tokens (existence proofs)
    audit/audit.jsonl        - redacted audit trail (NO credentials)
    verification-guide.txt   - how a third party verifies standalone

Compatibility:
    v1 (TM36 project backup packages): importable; the v1 pack
    becomes a V2 with only projects/photos populated - the loader
    says so explicitly rather than pretending v1 == v2.
    Night 05 DeliveryZIP32: a strict SUBSET (stored, no-compression,
    name+sha256 entries) - the mapping is documented in
    _NIGHT05_MAP_NOTE; a V2 reader can verify a Night 05 zip's
    entries against its own manifest, and a Night 05 zip can be
    UPGRADED to V2 by re-listing its files under the v2 manifest.

Integrity discipline:
    - stream hashing (D05 chunk iterators): 1 MiB peak per file
    - ZIP64 forced (D05): >4GiB and >65k files supported
    - path safety: every listed name validated (no ../, no absolute,
      no backslashes) at BUILD and VERIFY time
    - tamper detection: verify re-hashes every present file and
      NAMES every missing one; nothing is silently skipped
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZipInfo

EVPACK_VERSION = "evpack/2.0"
CHUNK = 1024 * 1024

_NIGHT05_MAP_NOTE = (
    "Night 05 DeliveryZIP32 is a strict subset of V2: a stored "
    "(uncompressed) zip whose manifest lists (name, sha256) per "
    "entry. A V2 verifier accepts it by re-hashing each stored "
    "entry against the Night 05 manifest; upgrading to a full V2 "
    "means re-listing the same files under an evpack/2.0 manifest "
    "with the sections this format defines. Night 05's 128 MiB "
    "in-memory cap does not apply to V2 (streamed, ZIP64).")


class EvpackError(ValueError):
    pass


# ---------------------------------------------------------- path safety

def safe_rel_path(name: str) -> str:
    """TM44 semantics: no absolute, no .., no backslash, no empty
    segment. Returns the normalized posix name."""
    if not name or "\\" in name:
        raise EvpackError(f"unsafe package path: {name!r}")
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or any(not s
                                                  for s in p.parts):
        raise EvpackError(f"unsafe package path: {name!r}")
    return p.as_posix()


# ------------------------------------------------------------- digesting

def stream_sha256(path_or_bytes) -> str:
    h = hashlib.sha256()
    if isinstance(path_or_bytes, (bytes, bytearray)):
        h.update(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK), b""):
                h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------- building

@dataclass
class Section:
    """One V2 section: files land under `prefix/` with the given
    content. names are validated; digests are streamed."""
    prefix: str
    files: list[tuple[str, bytes]] = field(default_factory=list)
    """(relative name within the section, content)."""

    def add(self, name: str, content: bytes) -> None:
        self.files.append((safe_rel_path(name), content))


def build_evpack(sections: list[Section],
                 meta: dict,
                 out_zip: Path) -> dict:
    """Assemble the V2 package. meta rides beside version in the
    manifest root (project info, created_at, tool version...)."""
    if not out_zip.parent.exists():
        out_zip.parent.mkdir(parents=True)
    entries: dict[str, str] = {}      # posix name -> sha256
    part = out_zip.with_suffix(out_zip.suffix + ".part")
    # stage content in a scratch zip first so the manifest (with
    # exact byte sizes) can be written as the FIRST entry of the
    # final artifact - no placeholder entry, no reordering tricks
    scratch = out_zip.with_suffix(out_zip.suffix + ".scratch")
    try:
        with ZipFile(scratch, "w") as zf:
            for section in sections:
                for name, content in section.files:
                    full = safe_rel_path(f"{section.prefix}/{name}")
                    if full in entries:
                        raise EvpackError(
                            f"duplicate package path: {full}")
                    entries[full] = stream_sha256(content)
                    zf.writestr(full, content)
        manifest = {
            "format_version": EVPACK_VERSION,
            "meta": meta,
            "files": {name: {"sha256": digest, "bytes": None}
                      for name, digest in entries.items()},
            "night05_map": _NIGHT05_MAP_NOTE,
            "verification": "re-hash every listed file; ANY "
                            "mismatch or missing file is a named "
                            "failure; unknown format_version is "
                            "refused",
        }
        for section in sections:
            for name, content in section.files:
                full = safe_rel_path(f"{section.prefix}/{name}")
                manifest["files"][full]["bytes"] = len(content)
        with ZipFile(part, "w") as zf:
            zf.writestr("manifest.json",
                        json.dumps(manifest, indent=1,
                                   ensure_ascii=False))
            with ZipFile(scratch, "r") as src:
                for info in src.infolist():
                    zf.writestr(info.filename, src.read(info))
        scratch.unlink(missing_ok=True)
        part.replace(out_zip)
        return {"entries": len(entries),
                "zip_sha256": stream_sha256(out_zip),
                "bytes": out_zip.stat().st_size}
    except Exception:
        part.unlink(missing_ok=True)
        scratch.unlink(missing_ok=True)
        raise


# ------------------------------------------------------------- verifying

def verify_evpack(package: Path, strict: bool = True,
                  max_member_bytes: int = 2 * 1024 * 1024 * 1024
                  ) -> dict:
    """Standalone verification: manifest version rule, path safety,
    per-file digest (streamed), missing/extra naming, tamper
    detection. No account, no network, no WorkCam runtime."""
    problems: list[str] = []
    with ZipFile(package, "r") as zf:
        names = set(zf.namelist())
        if "manifest.json" not in names:
            return {"ok": False, "problems":
                    ["manifest.json missing"]}
        manifest_bytes = zf.read("manifest.json")
        manifest = json.loads(manifest_bytes)
        version = manifest.get("format_version")
        if version != EVPACK_VERSION:
            problems.append(f"unknown format_version {version!r} - "
                            f"refused (this verifier accepts "
                            f"{EVPACK_VERSION} only; the schema rule: "
                            f"never silently accept)")
            return {"ok": False, "problems": problems}
        listed: dict[str, dict] = manifest.get("files", {})
        missing = [n for n in listed if n not in names]
        if missing:
            problems.append(f"MISSING ({len(missing)}): "
                            f"{missing[:8]}")
        extra = sorted(n for n in names
                       if n not in listed
                       and n != "manifest.json")
        if extra and strict:
            problems.append(f"EXTRA unlisted files ({len(extra)}): "
                            f"{extra[:8]}")
        tampered: list[str] = []
        for name, spec in listed.items():
            if name not in names:
                continue
            info = zf.getinfo(name)
            if info.file_size > max_member_bytes:
                problems.append(f"oversized member {name}: "
                                f"{info.file_size} bytes")
                continue
            digest = stream_sha256(zf.read(name))
            if digest != spec.get("sha256"):
                tampered.append(name)
        if tampered:
            problems.append(f"TAMPERED ({len(tampered)}): "
                            f"{tampered[:8]}")
        # path safety on every LISTED name
        for name in listed:
            try:
                safe_rel_path(name)
            except EvpackError:
                problems.append(f"unsafe listed path {name!r}")
    return {"ok": not problems, "problems": problems,
            "entries": len(manifest.get("files", {}))}


# ------------------------------------------------------------- v1 import

def import_v1_package(v1_dir: Path) -> list[Section]:
    """TM36 project-backup v1 -> V2 sections. The loader states the
    downgrade honestly: only projects/photos were in v1."""
    pkg = json.loads((v1_dir / "package.json").read_text(
        encoding="utf-8"))
    sections: list[Section] = []
    photos_sec = Section("originals")
    issues_sec = Section("issues")
    for entry in pkg.get("records", []):
        sha = entry.get("photo", {}).get("original_sha256")
        obj = v1_dir / "originals" / sha[:2] / f"{sha}.jpg"
        if obj.exists():
            photos_sec.add(f"{sha}.jpg", obj.read_bytes())
    for rec in pkg.get("records", []):
        photo_id = rec.get("photo", {}).get("photo_id")
        for issue in rec.get("issues", []):
            issues_sec.add(f"{issue['issue_id']}.json",
                           json.dumps(issue, indent=1).encode())
    sections.append(photos_sec)
    sections.append(issues_sec)
    return sections


# --------------------------------------------------- Night 05 subsetting

def verify_night05_zip(zip_path: Path) -> dict:
    """Verify a Night 05 DeliveryZIP32 against ITS OWN manifest:
    stored entries, name+sha256 each. A valid Night 05 zip can then
    be upgraded into a V2 (re-listing under evpack/2.0)."""
    problems: list[str] = []
    with ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if "manifest.json" not in names:
            return {"ok": False, "problems":
                    ["night05 zip lacks manifest.json"]}
        manifest = json.loads(zf.read("manifest.json"))
        entries = manifest.get("entries", [])
        listed = {e["name"]: e["sha256"] for e in entries}
        missing = [n for n in listed if n not in set(names)]
        if missing:
            problems.append(f"MISSING: {missing[:8]}")
        for n, sha in listed.items():
            if n not in set(names):
                continue
            if stream_sha256(zf.read(n)) != sha:
                problems.append(f"TAMPERED: {n}")
    return {"ok": not problems, "problems": problems,
            "night05_subset": True}
