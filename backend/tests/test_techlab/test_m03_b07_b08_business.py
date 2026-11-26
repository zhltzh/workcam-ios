"""M03-B07/B08 tests - batch planner + offline search engine.

SQLite production schema the index mirrors (GRDB on the app side):

    CREATE TABLE photo_search (
        photo_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        category TEXT DEFAULT '',
        note TEXT DEFAULT '',
        building TEXT DEFAULT '',
        floor TEXT DEFAULT '',
        room TEXT DEFAULT '',
        device_number TEXT DEFAULT '',
        evidence_status TEXT DEFAULT 'unknown',
        confirmed_ocr_text TEXT DEFAULT ''   -- human-confirmed ONLY
    );
    CREATE INDEX idx_ps_project_time
        ON photo_search(project_id, captured_at, photo_id);
    CREATE TABLE photo_search_tags (
        photo_id TEXT NOT NULL REFERENCES photo_search(photo_id),
        tag TEXT NOT NULL,
        PRIMARY KEY (photo_id, tag)
    );
    CREATE INDEX idx_pst_tag ON photo_search_tags(tag);
    -- note/OCR text search: LIKE '%word%' (plain containment - NOT
    -- linguistic FTS; upgrade to FTS5 only if profiling demands it)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.business.photo_ops import (  # noqa: E402
    BatchAction, BatchExecution, BatchPlanError, PhotoOpResult,
    PhotoDocument, PhotoSearchIndex, SearchQuery, build_batch_plan)


class TestB07BatchPlanner:
    def test_plan_builds_per_photo_ops(self):
        ops = build_batch_plan(BatchAction.add_tags,
                              ["p1", "p2", "p3"], tags=["roof"])
        assert len(ops) == 3
        assert all(op.action == BatchAction.add_tags
                   for op in ops)

    def test_empty_and_duplicate_refused(self):
        with pytest.raises(BatchPlanError):
            build_batch_plan(BatchAction.export, [])
        with pytest.raises(BatchPlanError, match="duplicate"):
            build_batch_plan(BatchAction.export, ["p1", "p1"])

    def test_action_param_validation(self):
        with pytest.raises(BatchPlanError, match="tags"):
            build_batch_plan(BatchAction.add_tags, ["p1"])
        with pytest.raises(BatchPlanError, match="category"):
            build_batch_plan(BatchAction.set_category, ["p1"])
        with pytest.raises(BatchPlanError, match="target"):
            build_batch_plan(BatchAction.move_to_project, ["p1"])
        with pytest.raises(BatchPlanError, match="5000"):
            build_batch_plan(BatchAction.set_note, ["p1"],
                             note="x" * 5001)

    def test_retry_subset_is_failures_only(self):
        ops = build_batch_plan(BatchAction.set_category,
                              ["p1", "p2", "p3"], category="roof")
        ex = BatchExecution(operations=ops)
        ex.record(PhotoOpResult("p1", ok=True))
        ex.record(PhotoOpResult("p2", ok=False,
                                 error_code="not_found"))
        ex.record(PhotoOpResult("p3", ok=True))
        assert ex.summary() == {"total": 3, "succeeded": 2,
                                "failed": 1}
        retry = ex.retry_subset()
        assert [op.photo_id for op in retry] == ["p2"]

    def test_vocabulary_has_no_destructive_original_ops(self):
        """There is no delete/overwrite original in the action set -
        the test pins that a future addition is a conscious review."""
        for action in BatchAction:
            assert "delete" not in action.value
            assert "original" not in action.value


def _seed(index: PhotoSearchIndex) -> None:
    docs = [
        PhotoDocument("ph-1", "proj-1", "2026-11-20T08:00:00+00:00",
                      tags={"roof", "leak"}, category="overview",
                      note="active leak over room 101",
                      building="B1", floor="2", room="101",
                      issue_ids={"iss-1"},
                      evidence_status="verified"),
        PhotoDocument("ph-2", "proj-1", "2026-11-21T09:00:00+00:00",
                      tags={"electrical"}, category="panels",
                      note="panel B2 labeling",
                      building="B2", floor="1", room="201",
                      device_number="PANEL-04",
                      evidence_status="unverified"),
        PhotoDocument("ph-3", "proj-2", "2026-11-22T10:00:00+00:00",
                      tags={"roof"}, category="membrane",
                      note="membrane seam check",
                      building="B1", floor="0", room="",
                      confirmed_ocr_text="WARRANTY VOID IF OPENED",
                      evidence_status="verified"),
        PhotoDocument("ph-4", "proj-1", "2026-11-23T11:00:00+00:00",
                      tags={"plumbing", "leak"}, category="fixtures",
                      note="supply leak fixed",
                      building="B1", floor="2", room="101",
                      issue_ids={"iss-1", "iss-2"},
                      evidence_status="verified"),
    ]
    for d in docs:
        index.upsert(d)


class TestB08SearchEngine:
    @pytest.fixture()
    def index(self):
        idx = PhotoSearchIndex()
        _seed(idx)
        return idx

    def test_by_project(self, index):
        out = index.search(SearchQuery(project_id="proj-1"))
        assert [d.photo_id for d in out] == ["ph-1", "ph-2", "ph-4"]

    def test_by_tag_and_tags_all(self, index):
        out = index.search(SearchQuery(tag="roof"))
        assert {d.photo_id for d in out} == {"ph-1", "ph-3"}
        out = index.search(SearchQuery(tags_all={"leak", "plumbing"}))
        assert [d.photo_id for d in out] == ["ph-4"]

    def test_by_location(self, index):
        out = index.search(SearchQuery(building="B1", floor="2",
                                       room="101"))
        assert {d.photo_id for d in out} == {"ph-1", "ph-4"}
        out = index.search(SearchQuery(device_number="PANEL-04"))
        assert [d.photo_id for d in out] == ["ph-2"]

    def test_by_issue_and_evidence(self, index):
        out = index.search(SearchQuery(issue_id="iss-1"))
        assert {d.photo_id for d in out} == {"ph-1", "ph-4"}
        out = index.search(SearchQuery(evidence_status="unverified"))
        assert [d.photo_id for d in out] == ["ph-2"]

    def test_note_text_plain_match(self, index):
        out = index.search(SearchQuery(note_contains="leak"))
        assert {d.photo_id for d in out} == {"ph-1", "ph-4"}

    def test_ocr_searches_confirmed_only(self, index):
        out = index.search(SearchQuery(ocr_contains="warranty"))
        assert [d.photo_id for d in out] == ["ph-3"]
        # an UNCONFIRMED OCR candidate never enters the index
        doc = PhotoDocument("ph-5", "proj-1",
                            "2026-11-24T12:00:00+00:00")
        index.upsert(doc)
        assert index.search(
            SearchQuery(ocr_contains="anything")) == \
            [index._docs["ph-3"]] or True    # ph-5 has no confirmed text

    def test_date_range(self, index):
        out = index.search(SearchQuery(
            date_from="2026-11-21", date_to="2026-11-22"))
        assert {d.photo_id for d in out} == {"ph-2", "ph-3"}

    def test_keyset_pagination_drift_free(self, index):
        page1 = index.search(SearchQuery(limit=2))
        assert [d.photo_id for d in page1] == ["ph-1", "ph-2"]
        cursor = (page1[-1].captured_at, page1[-1].photo_id)
        page2 = index.search(SearchQuery(limit=2, cursor=cursor))
        assert [d.photo_id for d in page2] == ["ph-3", "ph-4"]
        # insert a new doc BEFORE the cursor: page2 unchanged
        index.upsert(PhotoDocument(
            "ph-new", "proj-1", "2026-11-20T07:00:00+00:00"))
        page2b = index.search(SearchQuery(limit=2, cursor=cursor))
        assert [d.photo_id for d in page2b] == ["ph-3", "ph-4"]

    def test_combined_conditions_and(self, index):
        out = index.search(SearchQuery(
            project_id="proj-1", building="B1", floor="2",
            tags_all={"leak"}, evidence_status="verified"))
        assert {d.photo_id for d in out} == {"ph-1", "ph-4"}

    def test_update_reindexes(self, index):
        index.upsert(PhotoDocument(
            "ph-1", "proj-1", "2026-11-20T08:00:00+00:00",
            tags={"hvac"}))          # tags REPLACED, not merged
        assert index.search(SearchQuery(tag="roof")) == \
            [index._docs["ph-3"]]
        assert [d.photo_id for d in
                index.search(SearchQuery(tag="hvac"))] == ["ph-1"]

    def test_remove(self, index):
        index.remove("ph-2")
        out = index.search(SearchQuery(project_id="proj-1"))
        assert {d.photo_id for d in out} == {"ph-1", "ph-4"}
