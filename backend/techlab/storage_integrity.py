"""T06 - Data integrity & crash recovery (tech-lab experiment).

Offline photo store with atomic writes, manifest<->disk consistency
checking, fault injection, orphan detection, corrupted-record quarantine
and recovery reports.

DESIGN RULES (from the task brief):
- A crash at ANY point must never leave a half-written photo or a manifest
  claiming a photo that does not exist.
- Recovery NEVER deletes data: corrupted/unknown artifacts are moved to a
  quarantine area and reported, so evidence is never silently lost.

EXPERIMENTAL - not wired into the production `app` package.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from pydantic import BaseModel


class StorageFault:
    """Fault injection knobs for tests (simulated crashes / disk-full)."""

    def __init__(self) -> None:
        self.crash_before_rename = False     # die between temp write & replace
        self.crash_after_rename = False       # die right after rename (no manifest update)
        self.write_bytes_budget: int | None = None  # None = unlimited (disk-full sim)


class StorageError(Exception):
    pass


class DiskFullError(StorageError):
    pass


class CrashedSim(Exception):
    """Raised to SIMULATE a power loss at the injected point."""

    def __init__(self, point: str):
        super().__init__(f"simulated crash at: {point}")
        self.point = point


@dataclass
class StoreEntry:
    photo_id: str
    content_hash: str
    captured_at: str


class RecoveryReport(BaseModel):
    consistent: bool
    matched: int = 0
    orphan_files: list[str] = []
    missing_files: list[str] = []          # manifest claims them, disk lacks them
    hash_mismatches: list[str] = []        # file exists but bytes differ
    quarantined: list[str] = []
    notes: list[str] = []

    @property
    def problems(self) -> int:
        return (
            len(self.orphan_files)
            + len(self.missing_files)
            + len(self.hash_mismatches)
        )


class PhotoStore:
    """Directory-based store: photos/ + manifest.json (the DB stand-in).

    Invariant: a photo file that exists in photos/ is only "committed"
    if manifest.json lists it with the matching content hash. Anything
    else is an orphan (crash leftover) -> quarantine + report.
    """

    MANIFEST = "manifest.json"
    PHOTOS = "photos"
    QUARANTINE = "quarantine"

    def __init__(self, root: Path, fault: StorageFault | None = None):
        self.root = Path(root)
        self.fault = fault or StorageFault()
        (self.root / self.PHOTOS).mkdir(parents=True, exist_ok=True)
        (self.root / self.QUARANTINE).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ write
    def _budget_check(self, size: int) -> None:
        if self.fault.write_bytes_budget is not None:
            if size > self.fault.write_bytes_budget:
                raise DiskFullError(
                    f"simulated disk full: need {size} bytes, budget {self.fault.write_bytes_budget}"
                )

    def _atomic_write(self, path: Path, data: bytes) -> None:
        """temp file + fsync + atomic replace (same pattern as W003-R3)."""
        self._budget_check(len(data))
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if self.fault.crash_before_rename:
                os.unlink(tmp)
                raise CrashedSim("before rename")
            os.replace(tmp, path)
        except CrashedSim:
            raise
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        if self.fault.crash_after_rename:
            raise CrashedSim("after rename")

    def save_photo(self, data: bytes, captured_at: str) -> StoreEntry:
        """All-or-nothing: photo bytes AND manifest entry land together,
        or (on crash) neither is committed."""
        photo_id = hashlib.sha256(data).hexdigest()[:16]
        entry = StoreEntry(photo_id=photo_id, content_hash=hashlib.sha256(data).hexdigest(),
                           captured_at=captured_at)

        # stage the new manifest FIRST into memory, then:
        # 1. write photo atomically
        # 2. rewrite manifest atomically
        # crash points let tests verify no torn state becomes "committed".
        self._atomic_write(self.root / self.PHOTOS / f"{photo_id}.jpg", data)

        if self.fault.crash_after_rename:
            # photo on disk but manifest not yet updated -> orphan path in fsck
            raise CrashedSim("photo written, manifest not updated")

        manifest = self._read_manifest()
        manifest[photo_id] = {"content_hash": entry.content_hash, "captured_at": captured_at}
        self._atomic_write(self.root / self.MANIFEST, json.dumps(manifest, indent=2).encode())
        return entry

    # ------------------------------------------------------------------ read
    def _read_manifest(self) -> dict:
        path = self.root / self.MANIFEST
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise StorageError(f"manifest corrupted: {e}") from e

    def read_manifest(self) -> dict:
        return self._read_manifest()

    def photo_path(self, photo_id: str) -> Path:
        return self.root / self.PHOTOS / f"{photo_id}.jpg"

    def contains(self, photo_id: str) -> bool:
        return photo_id in self._read_manifest()

    # ------------------------------------------------------------------ fsck
    def fsck(self, quarantine: bool = True) -> RecoveryReport:
        """Cross-check manifest <-> disk. NEVER deletes: orphans/mismatches
        are moved into quarantine/ and reported.

        If the manifest itself is unreadable, orphan detection is SKIPPED:
        we cannot tell a crash leftover from a committed evidence photo, so
        every file stays in place (conservative, evidence-preserving)."""
        report = RecoveryReport(consistent=True)
        manifest: dict = {}
        manifest_ok = True
        try:
            manifest = self._read_manifest()
        except StorageError as e:
            manifest_ok = False
            report.notes.append(f"manifest unreadable: {e}")
            report.consistent = False

        # 1. manifest claims -> disk truth
        for photo_id, meta in manifest.items():
            path = self.photo_path(photo_id)
            if not path.exists():
                report.missing_files.append(photo_id)
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != meta.get("content_hash"):
                report.hash_mismatches.append(photo_id)

        # 2. disk truth -> manifest claims (orphans)
        #    SKIPPED when manifest is unreadable: quarantining then would risk
        #    moving committed evidence we simply cannot see.
        if manifest_ok:
            committed_ids = set(manifest.keys())
            for f in sorted((self.root / self.PHOTOS).iterdir()):
                if f.name.startswith(".tmp_"):
                    report.orphan_files.append(f.name)
                    continue
                stem = f.stem
                if stem not in committed_ids:
                    report.orphan_files.append(f.name)
        else:
            preserved = len(list((self.root / self.PHOTOS).iterdir()))
            report.notes.append(
                f"orphan detection skipped: {preserved} file(s) left in place "
                "because their committed status cannot be determined"
            )

        # 3. quarantine (move, never delete)
        if quarantine and manifest_ok:
            q = self.root / self.QUARANTINE / time.strftime("%Y%m%d-%H%M%S")
            for name in report.orphan_files:
                q.mkdir(parents=True, exist_ok=True)
                shutil.move(str(self.root / self.PHOTOS / name), str(q / name))
                report.quarantined.append(name)
            for photo_id in report.hash_mismatches:
                q.mkdir(parents=True, exist_ok=True)
                shutil.move(str(self.photo_path(photo_id)), str(q / f"{photo_id}.jpg"))
                report.quarantined.append(f"{photo_id}.jpg")
            report.notes.append(f"quarantine dir: {q}")

        report.consistent = report.problems == 0 and manifest_ok
        if report.problems or not manifest_ok:
            report.notes.append(
                "nothing was deleted: all problem artifacts were quarantined "
                "or left in place"
            )
        return report
