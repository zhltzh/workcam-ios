"""TM-04 F03: evidence-directory snapshot + incremental scanner.

ISOLATED techlab module (the quality gate's isolation check keeps
app routes free of it). Purpose: measure and bound the cost of the
Night05-style whole-evidence-directory audit at 10k-file scale:

  - snapshot_scan: one pass over the tree recording
    (relpath, size, mtime_ns) and optional content digests
  - incremental_scan: compare against the previous snapshot;
    UNCHANGED files (size+mtime match) are reported and SKIPPED
    from re-hashing; added/removed/changed sets are exact.

RED LINES: the scanner is READ-ONLY over the evidence tree (it
never deletes, renames or writes inside it; snapshots live outside).
Timings are REPORTED by tests, never asserted (two-flakes rule).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

SNAPSHOT_SCHEMA = "evidence-scan/1.0"
_CHUNK = 1 << 16


@dataclass
class ScanStats:
    files_seen: int = 0
    hashed: int = 0          # files whose content was digested
    bytes_hashed: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class ScanSnapshot:
    schema: str = SNAPSHOT_SCHEMA
    root: str = ""
    entries: dict[str, dict] = field(default_factory=dict)
    # entries: relpath -> {"size": int, "mtime_ns": int,
    #                      "sha256": str | None}
    stats: ScanStats = field(default_factory=ScanStats)

    def save(self, path: Path) -> None:
        import json

        path.write_text(json.dumps({
            "schema": self.schema,
            "root": self.root,
            "entries": self.entries,
        }, indent=1) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "ScanSnapshot":
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
        snap = cls(root=raw["root"])
        snap.entries = raw["entries"]
        return snap


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(_CHUNK):
            h.update(block)
    return h.hexdigest()


def snapshot_scan(root: Path, *, hash_files: bool = True,
                  include_suffixes: tuple[str, ...] | None = None,
                  ) -> ScanSnapshot:
    """Full pass. hash_files=False records metadata only (the cheap
    tier used between hash-verified audits)."""
    snap = ScanSnapshot(root=str(root))
    t0 = time.perf_counter()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if include_suffixes is not None and \
                p.suffix not in include_suffixes:
            continue
        rel = p.relative_to(root).as_posix()
        st = p.stat()
        entry = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
        if hash_files:
            entry["sha256"] = _digest(p)
            snap.stats.hashed += 1
            snap.stats.bytes_hashed += st.st_size
        snap.entries[rel] = entry
        snap.stats.files_seen += 1
    snap.stats.elapsed_seconds = time.perf_counter() - t0
    return snap


@dataclass
class ScanDelta:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    rehashed: list[str] = field(default_factory=list)
    stats: ScanStats = field(default_factory=ScanStats)


def incremental_scan(root: Path, previous: ScanSnapshot,
                     *, hash_changed: bool = True) -> ScanDelta:
    """Compare the CURRENT tree against `previous` WITHOUT a full
    pass over unchanged content: metadata equal (size+mtime_ns)
    => unchanged, previous digest reused; otherwise changed and
    (by default) re-hashed. Files new to the tree are hashed."""
    delta = ScanDelta()
    t0 = time.perf_counter()
    current: dict[str, dict] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        st = p.stat()
        current[rel] = {"size": st.st_size,
                       "mtime_ns": st.st_mtime_ns}
        delta.stats.files_seen += 1

    prev = previous.entries
    for rel, meta in current.items():
        old = prev.get(rel)
        if old is None:
            delta.added.append(rel)
            if hash_changed:
                delta.rehashed.append(rel)
                delta.stats.hashed += 1
                delta.stats.bytes_hashed += meta["size"]
            current[rel]["sha256"] = _digest(root / rel) \
                if hash_changed else None
        elif (old.get("size") == meta["size"]
              and old.get("mtime_ns") == meta["mtime_ns"]):
            delta.unchanged.append(rel)
            current[rel]["sha256"] = old.get("sha256")   # reuse
        else:
            delta.changed.append(rel)
            if hash_changed:
                delta.rehashed.append(rel)
                delta.stats.hashed += 1
                delta.stats.bytes_hashed += meta["size"]
                current[rel]["sha256"] = _digest(root / rel)

    for rel in prev:
        if rel not in current:
            delta.removed.append(rel)

    delta.stats.elapsed_seconds = time.perf_counter() - t0
    delta.stats.files_seen = len(current)
    return delta
