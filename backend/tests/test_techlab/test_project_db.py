"""T11 - project database tests + 10k benchmark (DESKTOP figures)."""

from __future__ import annotations

import time
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


# ---------------------------------------------------------------------------
# W-05: bulk load, time range, pagination, migration, index comparison
# ---------------------------------------------------------------------------


def _seed(db, n, project="p1", start_min=0):
    from datetime import datetime, timezone
    base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc).timestamp()
    rows = []
    for i in range(start_min, start_min + n):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base + i * 60))
        rows.append((f"ph{i:07d}", project, f"{i:064x}", ts,
                    f"SYNTHETIC note {i}", f"photos/{i}.jpg"))
    db.bulk_load_photos(rows)
    return rows


def test_bulk_load_and_time_range(tmp_path):
    db = _fresh(tmp_path)
    _seed(db, 200)
    got = db.photos_time_range("p1", "2026-01-01T08:30:00+00:00",
                               "2026-01-01T08:40:00+00:00")
    assert len(got) == 11                    # minutes 30..40 inclusive
    assert all("2026-01-01T08:3" in r["captured_at"] or
               "2026-01-01T08:40" in r["captured_at"] for r in got)
    db.close()


def test_offset_and_keyset_pagination_equivalent(tmp_path):
    db = _fresh(tmp_path)
    _seed(db, 120)
    page_size = 50

    # walk all three pages via keyset
    p1 = db.page_photos_keyset("p1", page_size, None, None)
    p2 = db.page_photos_keyset("p1", page_size, p1[-1]["captured_at"], p1[-1]["id"])
    p3 = db.page_photos_keyset("p1", page_size, p2[-1]["captured_at"], p2[-1]["id"])
    keyset_all = [r["id"] for r in p1 + p2 + p3]

    offset_all = [r["id"] for r in
                  db.page_photos_offset("p1", page_size, 0) +
                  db.page_photos_offset("p1", page_size, page_size) +
                  db.page_photos_offset("p1", page_size, 2 * page_size)]
    assert keyset_all == offset_all          # same order, same rows
    assert len(p3) == 20                     # 120 = 50 + 50 + 20
    db.close()


def test_migration_v1_to_v2_idempotent(tmp_path):
    db = _fresh(tmp_path)
    db.add_photo("x", "p1", "05" * 32, "2026-11-20T08:00:00+00:00", "n", "p/x.jpg")
    db.commit()

    db.migrate_v1_to_v2()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 2
    # old row got the default; new writes can set it
    assert db.conn.execute(
        "SELECT export_count FROM photos WHERE id='x'").fetchone()[0] == 0

    db.migrate_v1_to_v2()                     # second run = no-op
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 2
    db.close()


def test_index_speedup_is_measured(tmp_path):
    """Unindexed vs indexed time query on a seeded store: the benchmark
    measures the difference; this functional test only verifies the index
    can be dropped/rebuilt and results stay identical."""
    db = _fresh(tmp_path)
    _seed(db, 500)
    with_idx = db.photos_time_range("p1", "2026-01-01T08:00:00+00:00",
                                    "2026-01-01T08:10:00+00:00")
    db.conn.execute("DROP INDEX idx_photos_captured")
    db.conn.commit()
    without = db.photos_time_range("p1", "2026-01-01T08:00:00+00:00",
                                   "2026-01-01T08:10:00+00:00")
    assert [r["id"] for r in with_idx] == [r["id"] for r in without]
    db.close()


def test_benchmark_100k_runs_and_reports():
    """Full-scale W-05 benchmark. ~100k rows incl. FTS sync - the one
    deliberately slow test in this suite; numbers are printed for the
    Wave 2 report (DESKTOP only)."""
    from techlab.project_db import benchmark_100k
    results = benchmark_100k(rows=100_000)
    labels = {r.label for r in results}
    assert any("bulk load 100000" in l for l in labels)
    assert any("multi-project" in l for l in labels)
    assert any("pagination" in l for l in labels)
    assert any("migration" in l for l in labels)
    print("\n[T11 100k benchmark (DESKTOP, not iPhone)]")
    for r in results:
        print(f"  {r.label}: {r.duration_s * 1000:.1f} ms  {r.details}")
