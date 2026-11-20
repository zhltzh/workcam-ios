"""T11 - Offline project database & search (tech-lab experiment).

SQLite-based project store (SQLite is a system library on iOS, so the
Windows-side findings transfer to the iPhone directly).

Covers:
- WAL journal mode (offline-first: no server, crash-safe)
- projects / photos / annotations / tags / exports schema
- 10k-row insert & query benchmark (indexed vs unindexed, FTS5 search)
- backup via VACUUM INTO + restore verification
- bilingual (CJK + ASCII) full-text search

EXPERIMENTAL - not wired into the production `app` package.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS projects(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS photos(
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    content_hash TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    note TEXT,
    path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags(
    photo_id TEXT NOT NULL REFERENCES photos(id),
    tag TEXT NOT NULL,
    PRIMARY KEY (photo_id, tag)
);

CREATE TABLE IF NOT EXISTS annotations(
    id TEXT PRIMARY KEY,
    photo_id TEXT NOT NULL REFERENCES photos(id),
    kind TEXT NOT NULL,          -- e.g. arrow / text / measurement
    payload TEXT NOT NULL        -- JSON
);

CREATE TABLE IF NOT EXISTS export_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id TEXT NOT NULL REFERENCES photos(id),
    format TEXT NOT NULL,
    exported_at TEXT NOT NULL
);

-- FTS5 over notes, bilingual content (CJK tokenizer: unicode61 handles
-- CJK chars as individual tokens; trigram improves substring search).
CREATE VIRTUAL TABLE IF NOT EXISTS photos_fts USING fts5(
    photo_id UNINDEXED, note, tokenize='trigram'
);

CREATE INDEX IF NOT EXISTS idx_photos_project ON photos(project_id);
CREATE INDEX IF NOT EXISTS idx_photos_captured ON photos(captured_at);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
"""


class ProjectDB:
    """Thin, honest wrapper: no ORM, SQL visible for benchmark clarity."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ProjectDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ----------------------------------------------------------------- writes
    def add_project(self, project_id: str, name: str, created_at: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO projects(id, name, created_at) VALUES (?,?,?)",
            (project_id, name, created_at),
        )

    def add_photo(
        self,
        photo_id: str,
        project_id: str,
        content_hash: str,
        captured_at: str,
        note: str | None,
        path: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO photos(id, project_id, content_hash, captured_at, note, path) "
            "VALUES (?,?,?,?,?,?)",
            (photo_id, project_id, content_hash, captured_at, note, path),
        )
        if note:
            self.conn.execute(
                "INSERT INTO photos_fts(photo_id, note) VALUES (?,?)", (photo_id, note)
            )

    def add_tag(self, photo_id: str, tag: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO tags(photo_id, tag) VALUES (?,?)", (photo_id, tag)
        )

    def log_export(self, photo_id: str, fmt: str, exported_at: str) -> None:
        self.conn.execute(
            "INSERT INTO export_log(photo_id, format, exported_at) VALUES (?,?,?)",
            (photo_id, fmt, exported_at),
        )

    def commit(self) -> None:
        self.conn.commit()

    # ------------------------------------------------------------------ reads
    def photos_in_project(self, project_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM photos WHERE project_id = ?", (project_id,)
        ).fetchall()

    def photos_by_tag(self, tag: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT p.* FROM photos p JOIN tags t ON t.photo_id = p.id "
            "WHERE t.tag = ?",
            (tag,),
        ).fetchall()

    def search_notes(self, query: str) -> list[sqlite3.Row]:
        """FTS5 trigram search: works for CJK substrings and ASCII words."""
        return self.conn.execute(
            "SELECT p.* FROM photos_fts f JOIN photos p ON p.id = f.photo_id "
            "WHERE photos_fts MATCH ?",
            (query,),
        ).fetchall()

    def counts(self) -> dict:
        row = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM photos) AS photos, "
            "(SELECT COUNT(*) FROM tags) AS tags, "
            "(SELECT COUNT(*) FROM annotations) AS ann, "
            "(SELECT COUNT(*) FROM export_log) AS exports"
        ).fetchone()
        return dict(row)

    # ------------------------------------------------------- backup / restore
    def backup_to(self, target: Path) -> None:
        """Online, consistent backup without blocking writers (WAL)."""
        self.conn.execute("VACUUM INTO ?", (str(target),))

    @staticmethod
    def verify_backup(backup_path: Path) -> int:
        """Restore check: open the backup read-only and count photos.
        Returns row count; raises on corruption."""
        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT COUNT(*) FROM photos").fetchone()
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError(f"integrity_check: {integrity}")
            return int(row[0])
        finally:
            conn.close()

    # --------------------------------------------- W-05: bulk/time/page/migrate
    def bulk_load_photos(self, rows: list[tuple]) -> None:
        """Bulk insert (id, project_id, content_hash, captured_at, note, path)
        with FTS sync in ONE transaction - the fast path for seeding and
        device restores. Compare with per-row add_photo in benchmarks."""
        self.conn.executemany(
            "INSERT OR REPLACE INTO photos(id, project_id, content_hash, "
            "captured_at, note, path) VALUES (?,?,?,?,?,?)",
            rows,
        )
        self.conn.executemany(
            "INSERT INTO photos_fts(photo_id, note) SELECT id, note FROM photos "
            "WHERE id = ?",
            [(r[0],) for r in rows],
        )
        self.conn.commit()

    def photos_time_range(self, project_id: str, start: str, end: str) -> list[sqlite3.Row]:
        """Time-range query (uses idx_photos_captured). ISO 8601 strings
        sort chronologically when the offset is fixed/absent (we store
        the device-captured UTC-stamped value)."""
        return self.conn.execute(
            "SELECT * FROM photos WHERE project_id = ? AND captured_at >= ? "
            "AND captured_at <= ? ORDER BY captured_at",
            (project_id, start, end),
        ).fetchall()

    def page_photos_offset(self, project_id: str, limit: int, offset: int) -> list[sqlite3.Row]:
        """Classic OFFSET pagination (simple, degrades on deep pages)."""
        return self.conn.execute(
            "SELECT * FROM photos WHERE project_id = ? "
            "ORDER BY captured_at, id LIMIT ? OFFSET ?",
            (project_id, limit, offset),
        ).fetchall()

    def page_photos_keyset(self, project_id: str, limit: int,
                           last_captured: str | None, last_id: str | None) -> list[sqlite3.Row]:
        """Keyset pagination: stable and O(1) at any depth."""
        if last_captured is None:
            return self.conn.execute(
                "SELECT * FROM photos WHERE project_id = ? "
                "ORDER BY captured_at, id LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM photos WHERE project_id = ? AND (captured_at, id) > (?, ?) "
            "ORDER BY captured_at, id LIMIT ?",
            (project_id, last_captured, last_id, limit),
        ).fetchall()

    def migrate_v1_to_v2(self) -> None:
        """Schema migration drill: v1 -> v2 adds photos.export_count with a
        DEFAULT, guarded by PRAGMA user_version (idempotent). Demonstrates
        the online-ALTER pattern the iOS store will use."""
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= 2:
            return
        self.conn.execute(
            "ALTER TABLE photos ADD COLUMN export_count INTEGER NOT NULL DEFAULT 0"
        )
        self.conn.execute("PRAGMA user_version = 2")
        self.conn.commit()


@dataclass
class BenchmarkResult:
    label: str
    duration_s: float
    details: dict

    def to_dict(self) -> dict:
        return {"label": self.label, "seconds": round(self.duration_s, 4),
                "details": self.details}


def benchmark_10k(rows: int = 10_000) -> list[BenchmarkResult]:
    """Insert & query 10k+ metadata rows, report timings. Numbers land in
    the Wave 2 report; they are DESKTOP figures, not iPhone figures."""
    import tempfile
    import uuid

    results: list[BenchmarkResult] = []
    with tempfile.TemporaryDirectory() as tmp:
        db = ProjectDB(Path(tmp) / "bench.db")

        # bulk insert
        t0 = time.perf_counter()
        db.add_project("proj-1", "Site A", "2026-11-19T08:00:00+00:00")
        notes = [
            f"梁柱裂缝检查 row {i} 混凝土裂缝宽度复核" if i % 3 == 0
            else f"rebar inspection row {i} column face"
            for i in range(rows)
        ]
        for i, note in enumerate(notes):
            db.add_photo(
                photo_id=uuid.uuid4().hex,
                project_id="proj-1",
                content_hash=uuid.uuid4().hex,
                captured_at="2026-11-19T08:00:00+00:00",
                note=note,
                path=f"photos/{i}.jpg",
            )
            if i % 5 == 0:
                db.add_tag(f"{i}", "column")  # placeholder, replaced below
        db.commit()
        t1 = time.perf_counter()

        # tags referencing real ids
        ids = [r["id"] for r in db.photos_in_project("proj-1")]
        for pid in ids[::10]:
            db.add_tag(pid, "column")
        db.commit()

        results.append(BenchmarkResult(
            f"insert {rows} rows", t1 - t0, {"rows": rows}
        ))

        # indexed project query
        t0 = time.perf_counter()
        got = db.photos_in_project("proj-1")
        t1 = time.perf_counter()
        results.append(BenchmarkResult(
            "query by project (indexed)", t1 - t0, {"rows_returned": len(got)}
        ))

        # tag join
        t0 = time.perf_counter()
        got = db.photos_by_tag("column")
        t1 = time.perf_counter()
        results.append(BenchmarkResult(
            "query by tag (join)", t1 - t0, {"rows_returned": len(got)}
        ))

        # FTS5 CJK substring search
        t0 = time.perf_counter()
        got = db.search_notes("裂缝宽度")
        t1 = time.perf_counter()
        results.append(BenchmarkResult(
            "FTS5 CJK substring search", t1 - t0, {"rows_returned": len(got)}
        ))

        # FTS5 ASCII search
        t0 = time.perf_counter()
        got = db.search_notes("rebar")
        t1 = time.perf_counter()
        results.append(BenchmarkResult(
            "FTS5 ASCII search", t1 - t0, {"rows_returned": len(got)}
        ))

        # backup + verify
        backup = Path(tmp) / "backup.db"
        t0 = time.perf_counter()
        db.backup_to(backup)
        n = ProjectDB.verify_backup(backup)
        t1 = time.perf_counter()
        results.append(BenchmarkResult(
            "VACUUM INTO backup + verify", t1 - t0,
            {"rows_in_backup": n, "bytes": backup.stat().st_size},
        ))

        db.close()
    return results


def benchmark_100k(rows: int = 100_000) -> list[BenchmarkResult]:
    """W-05 scale benchmark: 100k rows across 5 projects.

    Measures: bulk load, multi-project query, time-range query, tag
    search, OFFSET vs keyset pagination (deep page), unindexed vs
    indexed time query, v1->v2 migration, backup+verify of a 100k-row
    store. DESKTOP figures, never iPhone claims."""
    import random
    import tempfile
    import uuid

    random.seed(20261120)          # deterministic data across runs
    results: list[BenchmarkResult] = []
    with tempfile.TemporaryDirectory() as tmp:
        db = ProjectDB(Path(tmp) / "bench100k.db")

        projects = [f"proj-{i}" for i in range(5)]
        for i, p in enumerate(projects):
            db.add_project(p, f"Site {i}", "2026-11-20T08:00:00+00:00")

        rows_data = []
        from datetime import datetime as _dt, timezone as _tz
        base = _dt(2026, 1, 1, 8, 0, 0, tzinfo=_tz.utc).timestamp()
        for i in range(rows):
            pid = projects[i % len(projects)]
            ts = time.strftime(
                "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base + (i % 86400) * 60)
            )
            note = (f"SYNTHETIC 巡检记录 {i} 混凝土复核" if i % 3 == 0
                    else f"SYNTHETIC inspection {i} rebar")
            rows_data.append((uuid.uuid4().hex, pid, uuid.uuid4().hex, ts, note, f"photos/{i}.jpg"))

        t_bulk = time.perf_counter()
        db.bulk_load_photos(rows_data)
        results.append(BenchmarkResult(
            f"bulk load {rows} rows (1 tx)", time.perf_counter() - t_bulk,
            {"rows": rows, "projects": len(projects)},
        ))

        t0 = time.perf_counter()
        counts = {p: len(db.photos_in_project(p)) for p in projects}
        results.append(BenchmarkResult(
            "multi-project query (5 projects)",
            time.perf_counter() - t0, {"rows_returned": sum(counts.values())},
        ))

        mid = projects[rows // 2 % 5]
        t0 = time.perf_counter()
        got = db.photos_time_range(
            mid, "2026-01-01T09:00:00+00:00", "2026-01-01T10:00:00+00:00")
        results.append(BenchmarkResult(
            "time-range query (indexed)",
            time.perf_counter() - t0, {"rows_returned": len(got), "project": mid},
        ))

        ids = [r["id"] for r in db.photos_in_project(mid)[:1000]]
        t0 = time.perf_counter()
        for pid in ids[::10]:
            db.add_tag(pid, "column")
        db.commit()
        got = db.photos_by_tag("column")
        results.append(BenchmarkResult(
            "tag insert + join search (100 tags)",
            time.perf_counter() - t0, {"rows_returned": len(got)},
        ))

        page_size = 50
        n_in_mid = counts[mid]
        deep_offset = max(0, n_in_mid - page_size)
        t0 = time.perf_counter()
        page_offset = db.page_photos_offset(mid, page_size, deep_offset)
        t_offset = time.perf_counter() - t0
        t0 = time.perf_counter()
        prev = db.page_photos_offset(mid, page_size, max(0, deep_offset - page_size))
        boundary = prev[-1]["captured_at"], prev[-1]["id"]
        page_keyset = db.page_photos_keyset(mid, page_size, *boundary)
        t_keyset = time.perf_counter() - t0
        # correctness: both methods return the same final page
        assert [r["id"] for r in page_offset] == [r["id"] for r in page_keyset]
        results.append(BenchmarkResult(
            "deep-page pagination offset vs keyset",
            t_offset, {
                "offset_page_rows": len(page_offset),
                "keyset_page_rows": len(page_keyset),
                "offset_s": round(t_offset, 4),
                "keyset_walk_s": round(t_keyset, 4),
            },
        ))

        db.conn.execute("DROP INDEX idx_photos_captured")
        db.conn.commit()
        t0 = time.perf_counter()
        db.photos_time_range(mid, "2026-01-01T09:00:00+00:00",
                              "2026-01-01T10:00:00+00:00")
        t_unindexed = time.perf_counter() - t0
        db.conn.execute("CREATE INDEX idx_photos_captured ON photos(captured_at)")
        db.conn.commit()
        t0 = time.perf_counter()
        db.photos_time_range(mid, "2026-01-01T09:00:00+00:00",
                             "2026-01-01T10:00:00+00:00")
        t_indexed = time.perf_counter() - t0
        results.append(BenchmarkResult(
            "time-range unindexed vs indexed",
            t_unindexed, {
                "unindexed_s": round(t_unindexed, 4),
                "indexed_s": round(t_indexed, 4),
            },
        ))

        t0 = time.perf_counter()
        db.migrate_v1_to_v2()
        results.append(BenchmarkResult(
            "v1->v2 migration (ALTER + user_version)",
            time.perf_counter() - t0,
            {"user_version": db.conn.execute("PRAGMA user_version").fetchone()[0]},
        ))
        db.migrate_v1_to_v2()   # idempotent no-op
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 2

        backup = Path(tmp) / "backup100k.db"
        t0 = time.perf_counter()
        db.backup_to(backup)
        n = ProjectDB.verify_backup(backup)
        results.append(BenchmarkResult(
            "VACUUM INTO backup + verify (100k)",
            time.perf_counter() - t0,
            {"rows_in_backup": n, "bytes": backup.stat().st_size},
        ))
        db.close()
    return results


if __name__ == "__main__":
    print(json.dumps([r.to_dict() for r in benchmark_10k()], indent=2))
    print(json.dumps([r.to_dict() for r in benchmark_100k()], indent=2))
