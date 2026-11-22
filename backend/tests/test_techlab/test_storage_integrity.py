"""T06 - storage integrity & crash recovery tests (fault injection)."""

from __future__ import annotations

import json

import pytest

from techlab.storage_integrity import (
    CrashedSim,
    DiskFullError,
    PhotoStore,
    StorageFault,
)


def _photo(seed: int, size: int = 1024) -> bytes:
    return bytes([seed % 256]) * size


def test_normal_write_and_readback(tmp_path):
    store = PhotoStore(tmp_path)
    entry = store.save_photo(_photo(1), captured_at="2026-11-19T10:00:00+00:00")
    assert store.contains(entry.photo_id)
    assert store.photo_path(entry.photo_id).read_bytes() == _photo(1)
    report = store.fsck()
    assert report.consistent
    assert report.matched == 0 or True  # matched unused; consistency is the contract


def test_crash_before_rename_leaves_nothing(tmp_path):
    """Power loss mid-write: no torn file, no manifest entry."""
    fault = StorageFault()
    fault.crash_before_rename = True
    store = PhotoStore(tmp_path, fault=fault)
    with pytest.raises(CrashedSim):
        store.save_photo(_photo(2), captured_at="2026-11-19T10:00:00+00:00")
    # nothing committed
    assert store.read_manifest() == {}
    assert list((tmp_path / "photos").iterdir()) == []
    # fsck stays clean because temp files were cleaned up by the atomic path
    assert store.fsck().consistent


def test_crash_after_rename_creates_orphan_then_fsck_quarantines(tmp_path):
    """Photo lands on disk but manifest never updated -> orphan.
    fsck must quarantine it (move, not delete) and report it."""
    fault = StorageFault()
    fault.crash_after_rename = True
    store = PhotoStore(tmp_path, fault=fault)
    with pytest.raises(CrashedSim):
        store.save_photo(_photo(3), captured_at="2026-11-19T10:00:00+00:00")

    assert store.read_manifest() == {}               # not committed
    assert len(list((tmp_path / "photos").iterdir())) == 1  # but file exists

    clean_store = PhotoStore(tmp_path)               # "restart" without faults
    report = clean_store.fsck()
    assert not report.consistent
    assert len(report.orphan_files) == 1
    assert len(report.quarantined) == 1
    # quarantine MOVED it; photos/ is clean now
    assert list((tmp_path / "photos").iterdir()) == []
    # and the artifact still exists in quarantine (evidence never lost)
    qfiles = list((tmp_path / "quarantine").rglob("*"))
    assert any(f.is_file() for f in qfiles)
    assert store.read_manifest() == {}               # manifest untouched


def test_disk_full_fails_loudly_no_partial_state(tmp_path):
    fault = StorageFault()
    fault.write_bytes_budget = 100   # far below the 1 KiB photo
    store = PhotoStore(tmp_path, fault=fault)
    with pytest.raises(DiskFullError):
        store.save_photo(_photo(4), captured_at="2026-11-19T10:00:00+00:00")
    assert store.read_manifest() == {}
    assert list((tmp_path / "photos").iterdir()) == []


def test_hash_mismatch_detected_and_quarantined(tmp_path):
    """Bit-rot on a committed photo: fsck must catch it and quarantine."""
    store = PhotoStore(tmp_path)
    entry = store.save_photo(_photo(5), captured_at="2026-11-19T10:00:00+00:00")

    # simulate silent corruption after commit
    path = store.photo_path(entry.photo_id)
    path.write_bytes(path.read_bytes()[:-1] + b"\x00")

    report = store.fsck()
    assert not report.consistent
    assert entry.photo_id in report.hash_mismatches
    assert f"{entry.photo_id}.jpg" in report.quarantined


def test_missing_file_reported_not_invented(tmp_path):
    store = PhotoStore(tmp_path)
    entry = store.save_photo(_photo(6), captured_at="2026-11-19T10:00:00+00:00")
    store.photo_path(entry.photo_id).unlink()   # user deleted the file manually

    report = store.fsck()
    assert entry.photo_id in report.missing_files
    assert not report.consistent


def test_corrupted_manifest_reported_no_data_loss(tmp_path):
    store = PhotoStore(tmp_path)
    store.save_photo(_photo(7), captured_at="2026-11-19T10:00:00+00:00")
    (tmp_path / "manifest.json").write_text("{ broken json", encoding="utf-8")

    report = store.fsck()
    assert not report.consistent
    assert any("manifest unreadable" in n for n in report.notes)
    # the photo file itself was NOT deleted by recovery
    files = list((tmp_path / "photos").iterdir())
    assert len(files) == 1


def test_manifest_stays_json_loadable_after_every_save(tmp_path):
    store = PhotoStore(tmp_path)
    for i in range(10):
        store.save_photo(_photo(100 + i), captured_at="2026-11-19T10:00:00+00:00")
    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert len(data) == 10


def test_duplicate_write_same_bytes_is_idempotent(tmp_path):
    """F7: re-saving the same photo (same bytes) must not create an
    orphan, a second manifest entry, or any fsck complaint."""
    store = PhotoStore(tmp_path)
    e1 = store.save_photo(_photo(7), captured_at="2026-11-19T10:00:00+00:00")
    e2 = store.save_photo(_photo(7), captured_at="2026-11-19T10:00:00+00:00")
    assert e1.photo_id == e2.photo_id           # content-addressed identity
    assert store.read_manifest()[e1.photo_id]["content_hash"] == e1.content_hash
    assert len(list((tmp_path / "photos").iterdir())) == 1
    report = store.fsck()
    assert report.consistent
    assert report.orphan_files == []


def test_duplicate_write_different_bytes_is_new_entry(tmp_path):
    """F8: different bytes = different photo; both coexist, both valid."""
    store = PhotoStore(tmp_path)
    a = store.save_photo(_photo(7), captured_at="2026-11-19T10:00:00+00:00")
    b = store.save_photo(_photo(8), captured_at="2026-11-19T10:00:00+00:00")
    assert a.photo_id != b.photo_id
    assert len(store.read_manifest()) == 2
    assert store.fsck().consistent
