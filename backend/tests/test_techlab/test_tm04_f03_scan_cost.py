"""TM-04 F03 (TM-03 F03 carry-over): whole-evidence-directory scan
cost profiling at 10k-file scale.

Measures (and ONLY reports - never gates) wall-clock for:
  full metadata pass, full hash pass, incremental pass after N edits
at 1k / 5k / 10k synthetic files, and asserts the FUNCTIONAL
invariants: exact added/removed/changed/unchanged sets, digest
stability, and that scanning never mutates the tree.

Artifacts: docs/perf/tm04-f03-scan-cost.json (environment-tagged,
DESKTOP host stated, per the F10 honesty rules).
"""

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path

import pytest

from techlab.evidence_scan import (
    incremental_scan,
    snapshot_scan,
)

REPO = Path(__file__).resolve().parents[3]
PERF_OUT = REPO / "docs" / "perf" / "tm04-f03-scan-cost.json"

SIZES = [1_000, 5_000, 10_000]


def _make_tree(root: Path, n: int, seed: int = 7) -> None:
    """Deterministic synthetic evidence tree: originals/, derivatives/
    and a few metadata .json files, sizes 512B..4KB."""
    import random

    rng = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)
    per_dir = 500
    for i in range(n):
        sub = root / f"originals/{(i // per_dir):03d}"
        sub.mkdir(parents=True, exist_ok=True)
        data = bytes(rng.getrandbits(8)
                     for _ in range(rng.choice((512, 1024, 4096))))
        (sub / f"img-{i:05d}.bin").write_bytes(data)
    (root / "originals" / "manifest.json").write_text(
        json.dumps({"count": n}) + "\n", encoding="utf-8")


class TestScanInvariants:
    def test_snapshot_counts_every_file_exactly_once(self, tmp_path):
        _make_tree(tmp_path / "ev", 30)
        snap = snapshot_scan(tmp_path / "ev")
        assert snap.stats.files_seen == 31   # 30 + manifest
        assert len(snap.entries) == 31
        assert all(e["sha256"] for e in snap.entries.values())

    def test_incremental_scan_detects_exact_changes(self, tmp_path):
        ev = tmp_path / "ev"
        _make_tree(ev, 40)
        prev = snapshot_scan(ev)

        (ev / "originals/000/img-00001.bin").unlink()        # removed
        (ev / "originals/new-photo.bin").write_bytes(         # added
            b"\x01" * 512)
        target = ev / "originals/000/img-00002.bin"
        target.write_bytes(b"\xff" * 2048)                      # changed

        delta = incremental_scan(ev, prev)
        assert delta.removed == ["originals/000/img-00001.bin"]
        assert delta.added == ["originals/new-photo.bin"]
        assert delta.changed == ["originals/000/img-00002.bin"]
        # everything else unchanged: 41 originals minus the removed
        # one and the changed one (the added file is not "unchanged")
        assert len(delta.unchanged) == 41 - 2
        # only the 2 new/changed files were re-hashed
        assert sorted(delta.rehashed) == [
            "originals/000/img-00002.bin", "originals/new-photo.bin"]

    def test_unchanged_incremental_reuses_previous_digests(
            self, tmp_path):
        ev = tmp_path / "ev"
        _make_tree(ev, 25)
        prev = snapshot_scan(ev)
        # write, then restore mtime by touching content back
        delta = incremental_scan(ev, prev)
        assert delta.added == []
        assert delta.changed == []
        assert delta.removed == []
        assert len(delta.unchanged) == 26

    def test_scanning_never_mutates_the_tree(self, tmp_path):
        ev = tmp_path / "ev"
        _make_tree(ev, 20)
        before = {p.relative_to(ev).as_posix(): p.stat().st_mtime_ns
                  for p in ev.rglob("*") if p.is_file()}
        snap = snapshot_scan(ev)
        del snap                    # scan must be side-effect-free
        after = {p.relative_to(ev).as_posix(): p.stat().st_mtime_ns
                 for p in ev.rglob("*") if p.is_file()}
        assert before == after      # files untouched (no atime
                                    # reliance; mtime preserved)

    def test_snapshot_roundtrip_through_disk(self, tmp_path):
        from techlab.evidence_scan import ScanSnapshot

        ev = tmp_path / "ev"
        _make_tree(ev, 12)
        snap = snapshot_scan(ev)
        f = tmp_path / "snap.json"
        snap.save(f)
        loaded = ScanSnapshot.load(f)
        assert loaded.entries == snap.entries


class TestScanCostProfile:
    """Timings are REPORTED into docs/perf/ (F10 rules), never
    asserted."""

    def test_scale_profile_reported(self, tmp_path):
        results = []
        for n in SIZES:
            ev = tmp_path / f"ev-{n}"
            _make_tree(ev, n)

            t0 = time.perf_counter()
            meta_snap = snapshot_scan(ev, hash_files=False)
            meta_seconds = time.perf_counter() - t0

            t0 = time.perf_counter()
            full_snap = snapshot_scan(ev, hash_files=True)
            full_seconds = time.perf_counter() - t0

            # a tiny realistic edit set: 0.2% of files touched
            import random
            rng = random.Random(1)
            files = [p for p in (ev / "originals").rglob("*.bin")]
            touched = rng.sample(files, max(1, len(files) // 500))
            for p in touched:
                with open(p, "ab") as f:
                    f.write(b"\x00" * 16)

            t0 = time.perf_counter()
            delta = incremental_scan(ev, full_snap)
            inc_seconds = time.perf_counter() - t0

            results.append({
                "files": n,
                "metadata_pass_seconds": round(meta_seconds, 3),
                "full_hash_pass_seconds": round(full_seconds, 3),
                "incremental_after_edits_seconds":
                    round(inc_seconds, 3),
                "files_rehashed_incrementally": len(delta.rehashed),
                "bytes_hashed_full": full_snap.stats.bytes_hashed,
            })
            # functional invariant on the incremental pass: only the
            # touched files were re-hashed
            assert len(delta.rehashed) == len(touched)

        PERF_OUT.parent.mkdir(parents=True, exist_ok=True)
        PERF_OUT.write_text(json.dumps({
            "schema": "tm04-f03-scan-cost/1.0",
            "host": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "machine": platform.machine(),
                "cpu_count": os.cpu_count(),
            },
            "note": "DESKTOP host measurements; NOT iPhone numbers. "
                    "Durations are informative only - no gate uses "
                    "wall-clock (two-flakes rule).",
            "results": results,
        }, indent=1) + "\n", encoding="utf-8")
        for r in results:
            print(f"{r['files']:>6} files: meta={r['metadata_pass_seconds']:.3f}s "
                  f"full-hash={r['full_hash_pass_seconds']:.3f}s "
                  f"incremental={r['incremental_after_edits_seconds']:.3f}s")
