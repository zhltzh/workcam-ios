"""M03-B07/B08: batch photo operations + offline photo search engine.

B07: batch-operation PLANNER on the client side. The SERVER already
     runs the real batch endpoints (W017); this engine builds the
     operation list, aggregates per-photo results, and produces the
     RETRY SUBSET for partial failures. Originals are NEVER touched
     (only business metadata) - enforced by the operation vocabulary.

B08: offline photo search over local photo documents: project, date,
     tags, note text, building/floor/room/device (from naming or
     metadata), issue ids, evidence status. Inverted index + range
     scan; keyset pagination. HONEST LIMIT: plain word matching on
     notes, not linguistic full-text search (stated).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum


# ============================================================ B07: batches

class BatchAction(str, Enum):
    """What a batch can do to BUSINESS metadata. The vocabulary is
    the enforcement: there is no delete-original, no overwrite-
    original, nothing that touches evidence fields."""
    add_tags = "add_tags"
    remove_tags = "remove_tags"
    set_category = "set_category"
    set_note = "set_note"
    move_to_project = "move_to_project"
    export = "export"
    link_issue = "link_issue"


class BatchPlanError(ValueError):
    pass


@dataclass(frozen=True)
class BatchOperation:
    photo_id: str
    action: BatchAction
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PhotoOpResult:
    photo_id: str
    ok: bool
    error_code: str | None = None
    detail: str = ""


@dataclass
class BatchExecution:
    """Aggregates per-photo results. NEVER silently drops a failure
    - every photo gets a row."""
    operations: list[BatchOperation]
    results: list[PhotoOpResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.operations)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> list[PhotoOpResult]:
        return [r for r in self.results if not r.ok]

    def record(self, result: PhotoOpResult) -> None:
        self.results.append(result)

    def summary(self) -> dict:
        return {"total": self.total, "succeeded": self.succeeded,
                "failed": len(self.failed)}

    def retry_subset(self) -> list[BatchOperation]:
        """The RESUMABLE failure subset: only the failed photo ops,
        in original order - retrying re-executes exactly those, the
        succeeded ones are NOT re-run (server ledger makes even that
        safe, but the client avoids needless traffic)."""
        failed_ids = {r.photo_id for r in self.failed}
        return [op for op in self.operations
                if op.photo_id in failed_ids]


def build_batch_plan(action: BatchAction,
                     photo_ids: list[str],
                     **params) -> list[BatchOperation]:
    """Build a homogeneous batch (one action over many photos).
    Validation happens HERE so an invalid batch never leaves the
    client."""
    if not photo_ids:
        raise BatchPlanError("batch needs at least one photo")
    if len(photo_ids) != len(set(photo_ids)):
        raise BatchPlanError("duplicate photo ids in batch")
    if action == BatchAction.add_tags:
        tags = params.get("tags") or []
        if not tags:
            raise BatchPlanError("add_tags needs tags")
        for t in tags:
            if not t or len(t) > 64:
                raise BatchPlanError(f"invalid tag {t!r}")
    elif action == BatchAction.set_category:
        if not params.get("category"):
            raise BatchPlanError("set_category needs a category")
    elif action == BatchAction.move_to_project:
        if not params.get("target_project_id"):
            raise BatchPlanError("move needs target_project_id")
    elif action == BatchAction.set_note:
        note = params.get("note", "")
        if len(note) > 5000:
            raise BatchPlanError("note longer than 5000 chars")
    return [BatchOperation(photo_id=p, action=action,
                           params=dict(params))
            for p in photo_ids]


# ==================================================== B08: search engine

@dataclass
class PhotoDocument:
    """One photo's searchable metadata. building/floor/room/device
    come from naming rules (B06) or explicit metadata; OCR text is a
    CANDIDATE field that only enters the index after human
    confirmation (B09 rule)."""
    photo_id: str
    project_id: str
    captured_at: str                    # ISO sortable
    tags: set[str] = field(default_factory=set)
    category: str = ""
    note: str = ""
    building: str = ""
    floor: str = ""
    room: str = ""
    device_number: str = ""
    issue_ids: set[str] = field(default_factory=set)
    evidence_status: str = "unknown"    # unverified/verified/...
    confirmed_ocr_text: str = ""        # HUMAN-CONFIRMED only

    def __post_init__(self):
        if not self.photo_id or not self.project_id:
            raise ValueError("document needs photo_id and project_id")


class SearchEngineError(ValueError):
    pass


@dataclass(frozen=True)
class SearchQuery:
    project_id: str | None = None
    tag: str | None = None              # single tag (AND semantics
    #                                    # via repeated queries)
    tags_all: set[str] | None = None    # all-of
    category: str | None = None
    building: str | None = None
    floor: str | None = None
    room: str | None = None
    device_number: str | None = None
    issue_id: str | None = None
    evidence_status: str | None = None
    note_contains: str | None = None    # plain word match (honest)
    ocr_contains: str | None = None     # searches CONFIRMED text only
    date_from: str | None = None       # ISO date prefix compare
    date_to: str | None = None
    limit: int = 50
    cursor: tuple[str, str] | None = None   # (captured_at, photo_id)


class PhotoSearchIndex:
    """In-memory reference index. The SQLite/GRDB production version
    mirrors the same query semantics (documented schema in the test
    docstring); this pure-Python one is the portable reference and
    the Swift port target."""

    def __init__(self) -> None:
        self._docs: dict[str, PhotoDocument] = {}
        # inverted indexes (sets of photo_ids)
        self._by_tag: dict[str, set[str]] = {}
        self._by_project: dict[str, set[str]] = {}
        self._by_category: dict[str, set[str]] = {}
        self._by_location: dict[tuple[str, str, str, str],
                                set[str]] = {}    # (bldg,floor,room,dev)
        self._by_issue: dict[str, set[str]] = {}
        self._by_evidence: dict[str, set[str]] = {}

    # ---- indexing --------------------------------------------------
    def upsert(self, doc: PhotoDocument) -> None:
        self.remove(doc.photo_id)
        self._docs[doc.photo_id] = doc
        for t in doc.tags:
            self._by_tag.setdefault(t, set()).add(doc.photo_id)
        self._by_project.setdefault(doc.project_id,
                                     set()).add(doc.photo_id)
        if doc.category:
            self._by_category.setdefault(doc.category,
                                         set()).add(doc.photo_id)
        loc = (doc.building, doc.floor, doc.room, doc.device_number)
        self._by_location.setdefault(loc, set()).add(doc.photo_id)
        for iid in doc.issue_ids:
            self._by_issue.setdefault(iid,
                                      set()).add(doc.photo_id)
        self._by_evidence.setdefault(doc.evidence_status,
                                     set()).add(doc.photo_id)

    def remove(self, photo_id: str) -> None:
        doc = self._docs.pop(photo_id, None)
        if doc is None:
            return
        for idx in (self._by_tag, self._by_project,
                    self._by_category, self._by_issue,
                    self._by_evidence):
            for s in idx.values():
                s.discard(photo_id)
        for s in self._by_location.values():
            s.discard(photo_id)

    # ---- querying --------------------------------------------------
    def search(self, q: SearchQuery) -> list[PhotoDocument]:
        """ALL conditions AND together; empty query = all photos.
        Results ordered (captured_at, photo_id) ASC with keyset
        pagination - the server's stable-sort contract."""
        candidates: set[str] | None = None

        def intersect(s: set[str] | None, other: set[str]) -> None:
            nonlocal candidates
            candidates = other.copy() if s is None \
                else (s & other if s is not None else other.copy())

        if q.project_id is not None:
            intersect(candidates,
                      self._by_project.get(q.project_id, set()))
        if q.tag is not None:
            intersect(candidates, self._by_tag.get(q.tag, set()))
        if q.tags_all:
            for t in q.tags_all:
                intersect(candidates,
                          self._by_tag.get(t, set()))
        if q.category is not None:
            intersect(candidates,
                      self._by_category.get(q.category, set()))
        if q.building is not None or q.floor is not None \
                or q.room is not None or q.device_number is not None:
            locs = {loc: ids for loc, ids in
                    self._by_location.items()
                    if (q.building is None
                        or loc[0] == q.building)
                    and (q.floor is None or loc[1] == q.floor)
                    and (q.room is None or loc[2] == q.room)
                    and (q.device_number is None
                         or loc[3] == q.device_number)}
            hit: set[str] = set()
            for ids in locs.values():
                hit |= ids
            intersect(candidates, hit)
        if q.issue_id is not None:
            intersect(candidates,
                      self._by_issue.get(q.issue_id, set()))
        if q.evidence_status is not None:
            intersect(candidates,
                      self._by_evidence.get(q.evidence_status,
                                            set()))

        if candidates is None:
            candidates = set(self._docs)

        # text conditions: plain word containment (case-folded)
        if q.note_contains is not None:
            needle = q.note_contains.casefold()
            candidates = {pid for pid in candidates
                          if needle in self._docs[pid].note.casefold()}
        if q.ocr_contains is not None:
            needle = q.ocr_contains.casefold()
            candidates = {
                pid for pid in candidates
                if needle in self._docs[
                    pid].confirmed_ocr_text.casefold()}

        # date range: compare the DATE PART (ISO dates compare
        # correctly as strings; date_to is inclusive of that day)
        if q.date_from is not None:
            candidates = {pid for pid in candidates
                          if self._docs[pid].captured_at[:10]
                          >= q.date_from}
        if q.date_to is not None:
            candidates = {pid for pid in candidates
                          if self._docs[pid].captured_at[:10]
                          <= q.date_to}

        # sort + keyset pagination
        ordered = sorted(
            (self._docs[pid] for pid in candidates),
            key=lambda d: (d.captured_at, d.photo_id))
        if q.cursor is not None:
            c_at, c_id = q.cursor
            ordered = [d for d in ordered
                       if (d.captured_at, d.photo_id) > (c_at, c_id)]
        return ordered[:q.limit]
