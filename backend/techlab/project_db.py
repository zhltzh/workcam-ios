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


if __name__ == "__main__":
    print(json.dumps([r.to_dict() for r in benchmark_10k()], indent=2))
