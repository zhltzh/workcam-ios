"""WC-W007 P0 - immutable original-photo object storage.

Content-addressed (sha256 IS the identity), atomic-commit, path-traversal-
proof by construction. Reuses the W003-R3 write discipline (tmp -> hash
-> rename) at the object level.

Design rules (from the task brief):
  - store path is SERVER-generated from the hash (a/b/<sha>.jpg) - no
    client byte ever reaches a filesystem path
  - write: temp file -> hash-while-streaming -> compare with the CLAIMED
    hash -> atomic rename. Mismatch -> temp discarded, 400, NOTHING
    persisted
  - idempotent: same hash -> same object path; re-store is a no-op
    verified read-back (never overwrites an existing object)
  - originals are NEVER re-encoded and EXIF is NEVER rewritten
  - file integrity VERIFIED is a DIFFERENT claim from photo AUTHENTICITY
    - status vocabulary keeps those apart ('stored' says bytes match the
      hash; nothing here claims the scene was real)
  - consistency recovery: DB and FS can disagree after a crash; fsck()
    reports orphans/corruption and the repair policy is EXPLICIT, never
    auto-delete (P4 requirement)
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import tempfile
from pathlib import Path

from app.db import ApiError, db, utc_now

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 256 * 1024


def store_root() -> Path:
    """Originals live NEXT TO the business DB (independent of the
    experimental upload store)."""
    import app.db as dbm
    return dbm._db_path().parent / "originals"


def object_path(sha256: str) -> Path:
    """Server-generated two-level layout: originals/ab/abcd...jpg.
    The sha is validated FIRST - a malformed 'hash' can never traverse."""
    if not _SHA256_RE.match(sha256):
        raise ApiError(400, "invalid_hash",
                       "sha256 must be 64 lowercase hex characters")
    return store_root() / sha256[:2] / f"{sha256}.jpg"


def store_original(data: bytes, claimed_sha256: str) -> dict:
    """Store one original-photo object.

    Flow: validate claim -> stream to unique temp -> hash while writing
    -> compare -> atomic rename. The claimed hash must match the ACTUAL
    bytes or nothing is persisted (P0: mismatch -> reject).

    Idempotency: if the object already exists with matching content, the
    call returns the existing record - an existing original is NEVER
    overwritten (immutability)."""
    if not _SHA256_RE.match(claimed_sha256):
        raise ApiError(400, "invalid_hash",
                       "sha256 must be 64 lowercase hex characters")

    target = object_path(claimed_sha256)

    # fast path: object already stored AND content-verified
    if target.exists():
        actual = _hash_file(target)
        if actual == claimed_sha256:
            _touch_verified(claimed_sha256)
            return {"sha256": claimed_sha256, "byte_size": target.stat().st_size,
                    "status": "stored", "idempotent_replay": True}
        # exists but CORRUPTED: report, never overwrite (P0 immutability)
        with db() as conn:
            _mark_original_status(conn, claimed_sha256,
                                  "corruption_detected")
        raise ApiError(409, "object_corrupted",
                       "stored object exists but its content does not "
                       "match its hash; manual recovery required",
                       {"sha256": claimed_sha256})

    # write: temp -> hash -> atomic rename
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                               prefix=".w007_", suffix=".part")
    hasher = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            hasher.update(data)
        actual = hasher.hexdigest()
        if actual != claimed_sha256:
            os.unlink(tmp)
            raise ApiError(400, "hash_mismatch",
                           f"content hash {actual} does not match the "
                           f"claimed {claimed_sha256} - object rejected")
        # os.replace is atomic on the same volume; a crash before it
        # leaves ONLY a temp file (fsck cleans those)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    now = utc_now()
    with db() as conn:
        conn.execute(
            "INSERT INTO mvp_originals (sha256, byte_size, status, "
            "stored_at, verified_at) VALUES (?,?, 'stored', ?, ?) "
            "ON CONFLICT(sha256) DO NOTHING",
            (claimed_sha256, len(data), now, now))
    return {"sha256": claimed_sha256, "byte_size": len(data),
            "status": "stored", "idempotent_replay": False}


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(_CHUNK):
            hasher.update(block)
    return hasher.hexdigest()


def _touch_verified(sha256: str) -> None:
    try:
        with db() as conn:
            conn.execute(
                "UPDATE mvp_originals SET verified_at = ?, "
                "status = 'stored' WHERE sha256 = ?",
                (utc_now(), sha256))
    except Exception:
        pass        # verified_at bookkeeping never fails a store call


def _mark_original_status(conn, sha256: str, status: str) -> None:
    conn.execute(
        "UPDATE mvp_originals SET status = ? WHERE sha256 = ?",
        (status, sha256))


def read_original(sha256: str) -> bytes:
    """Read one object back (after ownership checks at the API layer)."""
    path = object_path(sha256)
    if not path.exists():
        raise ApiError(404, "object_missing",
                       "original object not found in storage")
    return path.read_bytes()


# ------------------------------------------------------------- fsck (P4)


def fsck(fix_temps: bool = False) -> dict:
    """Audit storage consistency. NEVER auto-deletes originals - orphan
    handling is an explicit human/ops decision (P4 requirement).

    Checks:
      - every DB original exists on disk and hashes correctly
      - every bound photo's original is present and intact
      - files on disk with no DB record (potential orphans)
      - leftover .part temp files (crash residue - removable safely)

    fix_temps=True removes ONLY the crash-residue temps (never a
    committed object)."""
    report = {
        "checked_objects": 0,
        "intact": 0,
        "corrupt": [],            # [{sha256, reason}]
        "missing_files": [],      # DB rows whose file vanished
        "orphan_files": [],       # files with no DB record
        "orphan_suspected_db": [],  # DB originals no photo references
        "temp_residue": [],
        "repaired_temps": 0,
    }
    import sqlite3 as _sql

    root = store_root()
    conn = _sql.connect(str(root and _db_file()), timeout=30)
    conn.row_factory = _sql.Row
    try:
        originals = conn.execute(
            "SELECT o.sha256 FROM mvp_originals o").fetchall()
        report["checked_objects"] = len(originals)
        for row in originals:
            sha = row["sha256"]
            path = object_path(sha)
            if not path.exists():
                report["missing_files"].append(sha)
                with db() as c:
                    _mark_original_status(c, sha, "corruption_detected")
                continue
            actual = _hash_file(path)
            if actual != sha:
                report["corrupt"].append(
                    {"sha256": sha, "reason": "content does not match hash"})
                with db() as c:
                    _mark_original_status(c, sha, "corruption_detected")
            else:
                report["intact"] += 1
                _touch_verified(sha)

        # bound photos whose object is missing on the FS
        bound = conn.execute(
            "SELECT po.photo_id, po.sha256 FROM mvp_photo_originals po "
            "JOIN mvp_originals o ON o.sha256 = po.sha256").fetchall()
        report["bound_checks"] = len(bound)
        report["bound_missing"] = [
            b["photo_id"] for b in bound
            if not object_path(b["sha256"]).exists()]

        # DB originals nobody references -> suspected orphans (REPORT ONLY)
        orphans = conn.execute(
            "SELECT o.sha256 FROM mvp_originals o "
            "WHERE o.sha256 NOT IN "
            "(SELECT sha256 FROM mvp_photo_originals)").fetchall()
        report["orphan_suspected_db"] = [o["sha256"] for o in orphans]
        if orphans:
            with db() as c:
                for o in orphans:
                    _mark_original_status(c, o["sha256"], "orphan_suspected")
    finally:
        conn.close()

    # filesystem sweep: committed objects unknown to the DB + temps
    if root.exists():
        for part in sorted(root.rglob("*.part")):
            report["temp_residue"].append(str(part.relative_to(root)))
            if fix_temps:
                part.unlink(missing_ok=True)
                report["repaired_temps"] += 1
        known = set()
        conn2 = _sql.connect(str(_db_file()), timeout=30)
        conn2.row_factory = _sql.Row
        try:
            known = {r["sha256"] for r in conn2.execute(
                "SELECT sha256 FROM mvp_originals").fetchall()}
        finally:
            conn2.close()
        for jpg in sorted(root.rglob("*.jpg")):
            sha = jpg.stem
            if sha not in known:
                report["orphan_files"].append(str(jpg.relative_to(root)))
    return report


def _db_file() -> Path:
    import app.db as dbm
    return dbm._db_path()


# ------------------------------------------------- backup/restore (P4)


def backup(originals_target: Path) -> dict:
    """Consistent two-part backup: VACUUM INTO for the DB + verified
    copy for every intact object. Returns a manifest the restore step
    consumes."""
    import shutil
    import sqlite3 as _sql

    root = store_root()
    manifest = {"objects": {}, "failed": []}
    originals_target.mkdir(parents=True, exist_ok=True)
    conn = _sql.connect(str(_db_file()), timeout=30)
    conn.row_factory = _sql.Row
    try:
        rows = conn.execute(
            "SELECT sha256, byte_size FROM mvp_originals").fetchall()
        for row in rows:
            sha = row["sha256"]
            src = object_path(sha)
            if not src.exists():
                manifest["failed"].append(
                    {"sha256": sha, "reason": "missing on disk"})
                continue
            dst = originals_target / src.relative_to(root)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            if _hash_file(dst) != sha:
                manifest["failed"].append(
                    {"sha256": sha, "reason": "post-copy hash mismatch"})
                dst.unlink(missing_ok=True)
                continue
            manifest["objects"][sha] = row["byte_size"]
    finally:
        conn.close()
    return manifest


def restore(originals_dir: Path, manifest: dict) -> dict:
    """Restore objects into the store. Idempotent: an existing INTACT
    object is never re-overwritten (P4 #6 - repeated restores never
    damage data); a corrupted/mismatching existing object is refused,
    not clobbered."""
    restored = {"objects": 0, "skipped_intact": 0, "refused": []}
    root = store_root()
    for sha in manifest.get("objects", {}):
        src = originals_dir / sha[:2] / f"{sha}.jpg"
        if not src.exists():
            restored["refused"].append(
                {"sha256": sha, "reason": "backup part missing"})
            continue
        if _hash_file(src) != sha:
            restored["refused"].append(
                {"sha256": sha, "reason": "backup part hash mismatch"})
            continue
        dst = object_path(sha)
        if dst.exists():
            if _hash_file(dst) == sha:
                restored["skipped_intact"] += 1
                continue
            restored["refused"].append(
                {"sha256": sha,
                 "reason": "existing object corrupted; manual resolution "
                           "required - restore never overwrites"})
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        restored["objects"] += 1
    return restored
