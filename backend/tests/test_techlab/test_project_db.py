"""T11 - project database tests + 10k benchmark (DESKTOP figures)."""

from __future__ import annotations

import uuid

import pytest

from techlab.project_db import ProjectDB, benchmark_10k


def _fresh(tmp_path):
    db = ProjectDB(tmp_path / "t.db")
    db.add_project("p1", "Site A", "2026-11-19T08:00:00+00:00")
    return db


def test_wal_mode_enabled(tmp_path):
    db = _fresh(tmp_path)
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    db.close()


def test_crud_and_counts(tmp_path):
    db = _fresh(tmp_path)
    pid = uuid.uuid4().hex
    db.add_photo(pid, "p1", "ab" * 32, "2026-11-19T08:00:00+00:00",
                 "梁柱裂缝 check", "photos/x.jpg")
    db.add_tag(pid, "column")
    db.commit()

    assert db.counts()["photos"] == 1
    assert len(db.photos_in_project("p1")) == 1
    assert len(db.photos_by_tag("column")) == 1
    db.close()


def test_fts_cjk_and_ascii(tmp_path):
    db = _fresh(tmp_path)
    db.add_photo("a1", "p1", "01" * 32, "2026-11-19T08:00:00+00:00",
                 "梁柱裂缝宽度复核", "photos/a.jpg")
    db.add_photo("a2", "p1", "02" * 32, "2026-11-19T08:00:00+00:00",
                 "rebar inspection on column", "photos/b.jpg")
    db.add_photo("a3", "p1", "03" * 32, "2026-11-19T08:00:00+00:00", None, "photos/c.jpg")
    db.commit()

    assert [r["id"] for r in db.search_notes("裂缝宽度")] == ["a1"]
    assert [r["id"] for r in db.search_notes("rebar")] == ["a2"]
    assert db.search_notes("不存在的词") == []
    db.close()


def test_backup_and_restore(tmp_path):
    db = _fresh(tmp_path)
    for i in range(50):
        db.add_photo(f"p{i:03d}", "p1", f"{i:064x}", "2026-11-19T08:00:00+00:00",
                     f"note {i}", f"photos/{i}.jpg")
    db.commit()

    backup = tmp_path / "backup.db"
    db.backup_to(backup)
    assert backup.exists()
    assert ProjectDB.verify_backup(backup) == 50
    db.close()


def test_integrity_check_on_corrupted_backup_fails(tmp_path):
    db = _fresh(tmp_path)
    db.add_photo("x", "p1", "04" * 32, "2026-11-19T08:00:00+00:00", "n", "p/x.jpg")
    db.commit()
    backup = tmp_path / "backup.db"
    db.backup_to(backup)
    db.close()

    # corrupt the backup HEADER (magic string) -> deterministic corruption.
    # (Flipping mid-file bytes can land on a free page and go unnoticed -
    #  that would be a false PASS, which this suite must never produce.)
    raw = bytearray(backup.read_bytes())
    raw[0:16] = b"NOT-SQLITE-BYTES"
    backup.write_bytes(bytes(raw))
    with pytest.raises(Exception):
        ProjectDB.verify_backup(backup)


def test_benchmark_10k_reports_all_phases():
    """Benchmarks are DESKTOP figures (documented); the test pins the
    STRUCTURE of the results, and records numbers for the report."""
    results = benchmark_10k(rows=10_000)
    labels = [r.label for r in results]
    assert f"insert 10000 rows" in labels
    assert any("FTS5 CJK" in l for l in labels)
    assert any("backup" in l for l in labels)

    insert = next(r for r in results if r.label.startswith("insert"))
    cjk = next(r for r in results if "CJK" in r.label)
    assert insert.details["rows"] == 10_000
    assert cjk.details["rows_returned"] > 0    # found the CJK notes
    print("\n[T11 benchmark (DESKTOP, not iPhone)]")
    for r in results:
        print(f"  {r.label}: {r.duration_s * 1000:.1f} ms  {r.details}")
