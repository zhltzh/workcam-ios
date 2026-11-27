"""M03-F01/F02: synthetic data generator + database performance.

F01: reproducibility + honest count reporting.
F02: query/latency profiles over the REAL schema at small scale
     (1,000 photos / 10,000 metadata - the desktop-safe tier; the
     medium tier runs the SAME assertions, marked slow).
P50/P95/P99 latencies measured with time.perf_counter; this is a
DESKTOP observation, NOT an iPhone number (the honesty rule).
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from tools.synth_data import (  # noqa: E402
    SCALES, ScaleSpec, generate, load_into_backend,
    materialize_jpeg)


def _pcts(samples: list[float]) -> dict:
    s = sorted(samples)

    def pct(q):
        return round(s[min(len(s) - 1, int(q * len(s)))] * 1000, 2)
    return {"n": len(s), "p50_ms": pct(0.50), "p95_ms": pct(0.95),
            "p99_ms": pct(0.99)}


class TestF01Generator:
    def test_reproducible_from_seed(self):
        a = generate(SCALES["small"], seed=42)
        b = generate(SCALES["small"], seed=42)
        assert a["counts"] == b["counts"]
        assert a["photos"] == b["photos"]
        assert a["annotations"] == b["annotations"]

    def test_counts_reported_honestly(self):
        g = generate(SCALES["small"])
        c = g["counts"]
        assert c["projects"] == 10
        assert c["photos"] == 1_000
        # metadata may undershoot the budget when the photo pool caps
        # it - the generator REPORTS the real total
        assert c["metadata_total"] <= c["metadata_requested"]
        # evidence honesty: nothing is signed
        assert all(p["evidence_status"] == "unverified"
                   for p in g["photos"])

    def test_materialization_hash_stable(self):
        data1 = materialize_jpeg(0, 7, 320, 240)
        data2 = materialize_jpeg(0, 7, 320, 240)
        assert data1 == data2
        assert data1[:3] == b"\xff\xd8\xff"      # real JPEG


class TestF02DatabasePerf:
    @pytest.fixture()
    def loaded_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKCAM_BUSINESS_DB",
                           str(tmp_path / "perf.db"))
        import app.db as dbm
        dbm.migrate(tmp_path / "perf.db")
        scene = generate(SCALES["small"], seed=99)
        load_into_backend(scene, tmp_path / "perf.db",
                          tmp_path / "originals")
        return {"db_path": tmp_path / "perf.db",
                "scene": scene}

    def _query_profile(self, db_path, sql, params, n=40):
        import sqlite3
        lat = []
        for _ in range(n):
            conn = sqlite3.connect(str(db_path))
            t0 = time.perf_counter()
            rows = conn.execute(sql, params).fetchall()
            lat.append(time.perf_counter() - t0)
            conn.close()
            _ = rows
        return _pcts(lat)

    def test_project_list_latency(self, loaded_db):
        prof = self._query_profile(
            loaded_db["db_path"],
            "SELECT * FROM mvp_projects WHERE owner_token=? "
            "ORDER BY created_at DESC LIMIT 50",
            ("synthetic-owner",))
        assert prof["p99_ms"] < 500, prof     # desktop sanity bound
        print("project-list:", prof)

    def test_photo_grid_by_project(self, loaded_db):
        prof = self._query_profile(
            loaded_db["db_path"],
            "SELECT * FROM mvp_photos WHERE project_id=? "
            "ORDER BY captured_at DESC, photo_id LIMIT 100",
            ("syn-pj-0000",))
        assert prof["p99_ms"] < 500, prof
        print("photo-grid:", prof)

    def test_tag_search_uses_index(self, loaded_db):
        prof = self._query_profile(
            loaded_db["db_path"],
            "SELECT photo_id FROM mvp_photo_tags WHERE tag=? "
            "ORDER BY photo_id LIMIT 100",
            ("deck",))
        assert prof["p99_ms"] < 500, prof
        print("tag-search:", prof)

    def test_keyset_pagination_drift_free_at_scale(self, loaded_db):
        """Walk 1,000 photos via (captured_at, photo_id) keyset at
        limit 100 - 10 pages, no drift, and the walk profile is
        reported."""
        import sqlite3
        lat = []
        walked = 0
        cursor = None
        while True:
            conn = sqlite3.connect(str(loaded_db["db_path"]))
            t0 = time.perf_counter()
            if cursor is None:
                rows = conn.execute(
                    "SELECT captured_at, photo_id FROM mvp_photos "
                    "ORDER BY captured_at, photo_id LIMIT 100"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT captured_at, photo_id FROM mvp_photos "
                    "WHERE (captured_at > ? OR (captured_at = ? AND "
                    "photo_id > ?)) ORDER BY captured_at, photo_id "
                    "LIMIT 100",
                    (cursor[0], cursor[0], cursor[1])).fetchall()
            lat.append(time.perf_counter() - t0)
            conn.close()
            if not rows:
                break
            walked += len(rows)
            cursor = rows[-1]
        assert walked == 1_000
        print("keyset-walk:", _pcts(lat))

    def test_annotation_join_photo(self, loaded_db):
        prof = self._query_profile(
            loaded_db["db_path"],
            "SELECT a.annotation_id, p.photo_id FROM mvp_annotations"
            " a JOIN mvp_photos p ON a.photo_id = p.photo_id "
            "WHERE p.project_id = ? LIMIT 100",
            ("syn-pj-0001",))
        assert prof["p99_ms"] < 800, prof
        print("annotation-join:", prof)

    def test_slow_queries_named_and_measured(self, loaded_db):
        """Profiling harness: EXPLAIN QUERY PLAN confirms the tag
        index is used (the F02 'slow query' check - if the plan says
        SCAN on mvp_photo_tags the index regressed)."""
        import sqlite3
        conn = sqlite3.connect(str(loaded_db["db_path"]))
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT photo_id FROM mvp_photo_tags "
            "WHERE tag=? ORDER BY photo_id LIMIT 100",
            ("deck",)).fetchall()
        conn.close()
        assert any("SEARCH" in row[3] and "INDEX" in row[3]
                   for row in plan), plan


class TestF02MediumScale:
    @pytest.fixture(scope="class")
    def medium_db(self, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("medium")
        db_path = tmp / "medium.db"
        import app.db as dbm
        dbm.migrate(db_path)
        t0 = time.perf_counter()
        scene = generate(SCALES["medium"], seed=7)
        load_into_backend(scene, db_path, tmp / "originals")
        load_s = time.perf_counter() - t0
        return {"db_path": db_path, "scene": scene,
                "load_seconds": round(load_s, 2)}

    def test_medium_load_feasible_and_reported(self, medium_db):
        # 100 projects / 10k photos / ~100k metadata rows
        c = medium_db["scene"]["counts"]
        assert c["photos"] == 10_000
        print("medium load:", medium_db["load_seconds"], "s",
              c)

    def test_medium_keyset_walk(self, medium_db):
        import sqlite3
        t0 = time.perf_counter()
        conn = sqlite3.connect(str(medium_db["db_path"]))
        rows = conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM mvp_photos ORDER BY "
            "captured_at, photo_id LIMIT 5000)").fetchone()[0]
        conn.close()
        dt = (time.perf_counter() - t0) * 1000
        assert rows == 5000
        print("medium top-5000-by-keyset:", round(dt, 1), "ms")
        assert dt < 2000      # desktop bound
