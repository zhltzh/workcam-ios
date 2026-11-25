"""WC-W006 P3 - SQLite persistence layer for MVP business data.

Design decisions (audited from techlab/project_db.py lessons, promoted
to production form):

  - WAL journal mode + foreign_keys=ON (enforced PER CONNECTION - a
    process-level default is not inherited reliably across drivers)
  - versioned migrations via PRAGMA user_version (idempotent, replayable)
  - all timestamps are UTC ISO-8601 strings
  - business DB is SEPARATE from the experimental upload store: this is
    evidence-adjacent business data, not a tech-lab artifact
  - backup: VACUUM INTO (SQLite's consistent online backup - never a
    raw file copy of a live database)
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

# NOTE: no module-level DB_PATH constant on purpose - see _db_path().

_schema_v1 = """
CREATE TABLE IF NOT EXISTS mvp_projects (
    project_id   TEXT PRIMARY KEY,
    owner_token  TEXT NOT NULL,              -- P4: owning device token
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    site_address TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'archived')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    archived_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_owner
    ON mvp_projects(owner_token, created_at DESC);

CREATE TABLE IF NOT EXISTS mvp_photos (
    photo_id          TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES mvp_projects(project_id),
    idempotency_key   TEXT NOT NULL,          -- per-project uniqueness
    original_sha256   TEXT NOT NULL,          -- immutable once written
    original_filename TEXT NOT NULL,
    mime_type         TEXT NOT NULL,
    byte_size         INTEGER NOT NULL CHECK (byte_size > 0),
    captured_at       TEXT NOT NULL,          -- client-claimed, untrusted
    received_at       TEXT NOT NULL,          -- server UTC
    device_id         TEXT NOT NULL,          -- client-claimed, untrusted
    evidence_id       TEXT,                   -- nullable legacy link
    created_at        TEXT NOT NULL,
    UNIQUE (project_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_photos_project
    ON mvp_photos(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_photos_sha
    ON mvp_photos(original_sha256);

CREATE TABLE IF NOT EXISTS mvp_evidence_links (
    evidence_link_id     TEXT PRIMARY KEY,
    photo_id             TEXT NOT NULL REFERENCES mvp_photos(photo_id),
    evidence_reference   TEXT NOT NULL,
    evidence_type        TEXT NOT NULL
                         CHECK (evidence_type IN
                                ('signature_manifest', 'time_anchor')),
    original_sha256      TEXT NOT NULL,       -- bound at link time
    verification_status  TEXT NOT NULL
                         CHECK (verification_status IN
                                ('unverified', 'cryptographically_valid',
                                 'verification_failed', 'server_anchored')),
    failure_reason       TEXT,                 -- audit trail on failure
    verified_at          TEXT,
    created_at           TEXT NOT NULL,
    UNIQUE (photo_id, evidence_reference)
);
CREATE INDEX IF NOT EXISTS idx_elinks_photo
    ON mvp_evidence_links(photo_id, created_at DESC);
"""

MIGRATIONS: dict[int, str] = {
    1: _schema_v1,
    # v2 (W006 follow-up): the evidence-link status vocabulary was
    # missing 'signature_valid' - the EXISTING protocol's state for
    # "signature math valid but key not registered" (task brief: align
    # with the existing machine, never invent a second one). SQLite
    # cannot ALTER a CHECK constraint, so the table is rebuilt.
    2: """
CREATE TABLE mvp_evidence_links_v2 (
    evidence_link_id     TEXT PRIMARY KEY,
    photo_id             TEXT NOT NULL REFERENCES mvp_photos(photo_id),
    evidence_reference   TEXT NOT NULL,
    evidence_type        TEXT NOT NULL
                         CHECK (evidence_type IN
                                ('signature_manifest', 'time_anchor')),
    original_sha256      TEXT NOT NULL,
    verification_status  TEXT NOT NULL
                         CHECK (verification_status IN
                                ('unverified', 'signature_valid',
                                 'cryptographically_valid',
                                 'verification_failed', 'server_anchored')),
    failure_reason       TEXT,
    verified_at          TEXT,
    created_at           TEXT NOT NULL,
    UNIQUE (photo_id, evidence_reference)
);
INSERT INTO mvp_evidence_links_v2
    SELECT evidence_link_id, photo_id, evidence_reference, evidence_type,
           original_sha256, verification_status, failure_reason,
           verified_at, created_at
    FROM mvp_evidence_links;
DROP TABLE mvp_evidence_links;
ALTER TABLE mvp_evidence_links_v2
    RENAME TO mvp_evidence_links;
CREATE INDEX IF NOT EXISTS idx_elinks_photo
    ON mvp_evidence_links(photo_id, created_at DESC);
""",
    # v3 (W007): immutable original-photo objects, content-addressed.
    # mvp_originals is the storage ledger (sha256 IS the identity);
    # mvp_photo_originals is the explicit photo->object binding with
    # per-project ownership (an object MAY be referenced by several
    # photos; each binding still belongs to exactly one project).
    3: """
CREATE TABLE mvp_originals (
    sha256       TEXT PRIMARY KEY,
    byte_size    INTEGER NOT NULL CHECK (byte_size > 0),
    status       TEXT NOT NULL DEFAULT 'stored'
                 CHECK (status IN ('stored', 'orphan_suspected',
                                   'corruption_detected')),
    stored_at    TEXT NOT NULL,
    verified_at  TEXT
);
CREATE TABLE mvp_photo_originals (
    photo_id    TEXT NOT NULL REFERENCES mvp_photos(photo_id),
    sha256      TEXT NOT NULL REFERENCES mvp_originals(sha256),
    owner_token TEXT NOT NULL,
    bound_at    TEXT NOT NULL,
    PRIMARY KEY (photo_id, sha256)
);
CREATE INDEX IF NOT EXISTS idx_porig_owner
    ON mvp_photo_originals(owner_token);
""",
    # v4 (W008): offline sync engine. Three additions:
    #  a) mvp_projects.revision - optimistic concurrency counter
    #  b) mvp_sync_ops - idempotency ledger (operation_id PK + request
    #     hash + receipt JSON); survives restarts by living in the DB
    #  c) mvp_sync_events - append-only change log with a restart-stable
    #     monotonic cursor (AUTOINCREMENT seq)
    4: """
ALTER TABLE mvp_projects ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
CREATE TABLE IF NOT EXISTS mvp_sync_ops (
    operation_id    TEXT NOT NULL,
    owner_token     TEXT NOT NULL,
    request_hash    TEXT NOT NULL,
    result_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (operation_id, owner_token)
);
CREATE INDEX IF NOT EXISTS idx_sync_ops_owner
    ON mvp_sync_ops(owner_token, created_at DESC);
CREATE TABLE IF NOT EXISTS mvp_sync_events (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_token     TEXT NOT NULL,
    entity_type     TEXT NOT NULL CHECK (entity_type IN
                      ('project', 'photo', 'evidence_link', 'original')),
    entity_id       TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN
                      ('created', 'updated', 'archived',
                       'bound', 'status_changed')),
    revision        INTEGER,
    payload_hint    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sync_events_owner
    ON mvp_sync_events(owner_token, seq ASC);
""",
    # v5 (W009): SQLite-persisted device identity + challenges + rate
    # limits + structured audit log. Replaces the file-based devices.jsonl
    # (read-compat kept: lookups fall back to the legacy file).
    5: """
CREATE TABLE IF NOT EXISTS mvp_devices (
    device_token    TEXT PRIMARY KEY,
    device_id       TEXT NOT NULL,
    public_key_b64  TEXT NOT NULL,
    key_fingerprint TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'revoked')),
    enrolled_at     TEXT NOT NULL,
    revoked_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_devices_status
    ON mvp_devices(status, device_id);
CREATE TABLE IF NOT EXISTS mvp_auth_challenges (
    nonce           TEXT PRIMARY KEY,
    device_token    TEXT NOT NULL,
    expires_at      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mvp_rate_limits (
    device_token    TEXT NOT NULL,
    window_start   REAL NOT NULL,
    request_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_token, window_start)
);
CREATE TABLE IF NOT EXISTS mvp_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    device_token    TEXT,
    device_id       TEXT,
    detail          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts
    ON mvp_audit_log(ts DESC);
""",
    # v6 (W011): watermark derivatives. Immutable-original rule: the
    # derivative is a SEPARATE artifact; the original's bytes are never
    # touched. Idempotency: (photo, template, render_params_hash, locked
    # original hash) is unique - re-rendering the same request returns
    # the same derivative row.
    6: """
CREATE TABLE IF NOT EXISTS mvp_derivatives (
    derivative_id      TEXT PRIMARY KEY,
    photo_id           TEXT NOT NULL REFERENCES mvp_photos(photo_id),
    owner_token        TEXT NOT NULL,
    template_name      TEXT NOT NULL,
    template_version   TEXT NOT NULL,
    render_params_hash TEXT NOT NULL,
    original_sha256    TEXT NOT NULL,
    derivative_sha256  TEXT NOT NULL,
    image_width        INTEGER NOT NULL,
    image_height       INTEGER NOT NULL,
    created_at         TEXT NOT NULL,
    UNIQUE (photo_id, template_name, render_params_hash, original_sha256)
);
CREATE INDEX IF NOT EXISTS idx_derivatives_photo
    ON mvp_derivatives(photo_id, created_at DESC);
""",
    # v7 (W012): engineering reports. Snapshot model: the report binds
    # the photo/evidence state AT GENERATION TIME; regeneration creates
    # a new row (version), never mutates an existing one.
    7: """
CREATE TABLE IF NOT EXISTS mvp_reports (
    report_id      TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES mvp_projects(project_id),
    owner_token    TEXT NOT NULL,
    report_number  TEXT NOT NULL,
    report_type    TEXT NOT NULL
                   CHECK (report_type IN
                          ('site_photo', 'inspection', 'evidence')),
    template_version TEXT NOT NULL,
    page_size      TEXT NOT NULL CHECK (page_size IN ('a4', 'letter')),
    per_page       INTEGER NOT NULL CHECK (per_page IN (1, 2, 4)),
    photo_ids_json TEXT NOT NULL,
    snapshot_json  TEXT NOT NULL,
    pdf_sha256     TEXT NOT NULL,
    page_count     INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'complete'
                   CHECK (status IN ('generating', 'complete', 'failed')),
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_project
    ON mvp_reports(project_id, created_at DESC);
""",
    # v8 (W013): photo annotations + inspection issues + issue history +
    # before/after comparison links. Annotations use NORMALIZED coords
    # on the EXIF-transposed display frame. Issues carry optimistic
    # revision + append-only history.
    8: """
CREATE TABLE IF NOT EXISTS mvp_annotations (
    annotation_id    TEXT PRIMARY KEY,
    photo_id         TEXT NOT NULL REFERENCES mvp_photos(photo_id),
    owner_token      TEXT NOT NULL,
    annotation_type  TEXT NOT NULL CHECK (annotation_type IN
                     ('arrow', 'rectangle', 'circle', 'freehand',
                      'text', 'number_marker')),
    norm_x1          REAL NOT NULL CHECK (norm_x1 >= 0 AND norm_x1 <= 1),
    norm_y1          REAL NOT NULL CHECK (norm_y1 >= 0 AND norm_y1 <= 1),
    norm_x2          REAL CHECK (norm_x2 IS NULL OR
                        (norm_x2 >= 0 AND norm_x2 <= 1)),
    norm_y2          REAL CHECK (norm_y2 IS NULL OR
                        (norm_y2 >= 0 AND norm_y2 <= 1)),
    color_hex        TEXT NOT NULL DEFAULT '#FF3B30',
    stroke_width     INTEGER NOT NULL DEFAULT 4 CHECK (stroke_width >= 1
                        AND stroke_width <= 40),
    text_content     TEXT CHECK (text_content IS NULL OR
                        length(text_content) <= 200),
    marker_number    INTEGER CHECK (marker_number IS NULL OR
                        (marker_number >= 1 AND marker_number <= 999)),
    z_order         INTEGER NOT NULL DEFAULT 0,
    revision         INTEGER NOT NULL DEFAULT 1,
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_photo
    ON mvp_annotations(photo_id, z_order ASC);
CREATE TABLE IF NOT EXISTS mvp_issues (
    issue_id          TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES mvp_projects(project_id),
    owner_token       TEXT NOT NULL,
    title             TEXT NOT NULL CHECK (length(title) >= 1
                        AND length(title) <= 300),
    description       TEXT NOT NULL DEFAULT '',
    severity          TEXT NOT NULL CHECK (severity IN
                        ('low', 'medium', 'high', 'critical')),
    status            TEXT NOT NULL DEFAULT 'open' CHECK (status IN
                        ('open', 'in_progress', 'resolved', 'closed')),
    location_description TEXT NOT NULL DEFAULT '',
    assigned_to       TEXT NOT NULL DEFAULT '',
    revision          INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    resolved_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_issues_project
    ON mvp_issues(project_id, status);
CREATE TABLE IF NOT EXISTS mvp_issue_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id         TEXT NOT NULL REFERENCES mvp_issues(issue_id),
    actor_token      TEXT NOT NULL,
    action           TEXT NOT NULL,
    from_value       TEXT,
    to_value         TEXT,
    detail           TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issue_history_issue
    ON mvp_issue_history(issue_id, id ASC);
CREATE TABLE IF NOT EXISTS mvp_issue_photos (
    issue_id         TEXT NOT NULL REFERENCES mvp_issues(issue_id),
    photo_id         TEXT NOT NULL REFERENCES mvp_photos(photo_id),
    role             TEXT NOT NULL CHECK (role IN
                        ('before', 'progress', 'after')),
    added_at         TEXT NOT NULL,
    PRIMARY KEY (issue_id, photo_id, role)
);
CREATE TABLE IF NOT EXISTS mvp_issue_links (
    issue_id         TEXT NOT NULL REFERENCES mvp_issues(issue_id),
    annotation_id    TEXT NOT NULL REFERENCES
                        mvp_annotations(annotation_id),
    linked_at        TEXT NOT NULL,
    PRIMARY KEY (issue_id, annotation_id)
);
""",
    # v10 (B02-W017): photo batch management. Numbered 10 (not 9) on
    # purpose: the B02-W014 branch already defines a v9 event-stream
    # rebuild; keeping this migration at 10 avoids a duplicate dict
    # key when the branches merge (migrate() applies versions in
    # sorted order, gaps are fine).
    #
    # DESIGN RULE (task constraint): business metadata is stored in
    # SEPARATE tables - mvp_photos keeps its evidence-adjacent fields
    # untouched (original_sha256 / captured_at / device_id ...), the
    # signature/verification chain is never written by batch ops.
    #
    #  a) mvp_photo_tags - per-photo tag set (PK photo+tag: idempotent
    #     add, remove = delete row)
    #  b) mvp_photo_business - business metadata (category/note) with
    #     optimistic revision; NOT an evidence field
    #  c) mvp_batch_ops - per-(batch, photo) idempotency ledger.
    #     SUCCESSFUL items are recorded so retries of a partial batch
    #     replay the stored result (no re-execution); FAILED items
    #     are deliberately NOT recorded - a failed photo stays
    #     retryable under the same batch id (resumable partial-failure
    #     handling, task requirement). Same-hash replay = duplicate
    #     receipt, different-hash replay = typed conflict (W008
    #     operation-id discipline, applied per photo).
    #  d) mvp_photo_exports - generated export packages (ZIP artifact
    #     metadata; bytes live in the export store, sha-256 pinned)
    10: """
CREATE TABLE IF NOT EXISTS mvp_photo_tags (
    photo_id    TEXT NOT NULL REFERENCES mvp_photos(photo_id),
    tag         TEXT NOT NULL CHECK (length(tag) >= 1 AND length(tag) <= 64),
    added_at    TEXT NOT NULL,
    PRIMARY KEY (photo_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_photo_tags_tag
    ON mvp_photo_tags(tag);
CREATE TABLE IF NOT EXISTS mvp_photo_business (
    photo_id    TEXT PRIMARY KEY REFERENCES mvp_photos(photo_id),
    category    TEXT NOT NULL DEFAULT '' CHECK (length(category) <= 100),
    note        TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 5000),
    updated_by  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    revision    INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS mvp_batch_ops (
    batch_id    TEXT NOT NULL,
    owner_token TEXT NOT NULL,
    op_type     TEXT NOT NULL CHECK (op_type IN
                  ('tag_add', 'tag_remove', 'category', 'note',
                   'move', 'watermark', 'export')),
    photo_id    TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (batch_id, owner_token, photo_id)
);
CREATE INDEX IF NOT EXISTS idx_batch_ops_owner
    ON mvp_batch_ops(owner_token, created_at DESC);
CREATE TABLE IF NOT EXISTS mvp_photo_exports (
    export_id   TEXT PRIMARY KEY,
    owner_token TEXT NOT NULL,
    batch_id    TEXT NOT NULL,
    photo_count INTEGER NOT NULL,
    include_originals INTEGER NOT NULL CHECK (include_originals IN (0, 1)),
    zip_sha256  TEXT NOT NULL,
    file_count  INTEGER NOT NULL,
    byte_size   INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
""",
}

_write_lock = threading.Lock()


def utc_now() -> str:
    """UTC ISO-8601 with timezone - every business timestamp uses this."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    """Lazy resolution: tests override WORKCAM_BUSINESS_DB per-test via
    monkeypatch AFTER import - a module-level constant would freeze the
    first value and silently write to the wrong database (found by the
    backup-restore roundtrip test)."""
    return Path(os.environ.get("WORKCAM_BUSINESS_DB",
                               "data/business/workcam.db"))


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")     # per-connection, NOT inherited
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def migrate(db_path: Path | None = None) -> int:
    """Apply pending migrations in order. Idempotent: replaying on a
    database at version N is a no-op. Returns the applied count.

    NOTE: sqlite3.executescript() IMPLICITLY COMMITS before running -
    inside an explicit transaction the COMMIT would fire on an empty
    transaction ('cannot commit - no transaction is active', found by
    the startup smoke test). Statements therefore execute one by one
    inside the same BEGIN IMMEDIATE."""
    conn = connect(db_path)
    applied = 0
    try:
        with _write_lock:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            for version in sorted(MIGRATIONS):
                if version > current:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        for stmt in _split_statements(MIGRATIONS[version]):
                            conn.execute(stmt)
                        conn.execute(f"PRAGMA user_version = {version}")
                        conn.execute("COMMIT")
                        applied += 1
                        current = version
                    except Exception:
                        try:
                            conn.execute("ROLLBACK")
                        except sqlite3.OperationalError:
                            pass    # transaction already gone
                        raise
    finally:
        conn.close()
    return applied


def _split_statements(script: str) -> list[str]:
    """Naive but sufficient splitter: our schema uses no triggers and
    no statements containing embedded semicolons in strings."""
    return [s.strip() for s in script.split(";") if s.strip()]


@contextmanager
def db(db_path: Path | None = None):
    """Transaction-scoped connection: COMMIT on success, ROLLBACK on
    any exception (P3 requirement: no inconsistent data on errors)."""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ------------------------------------------------------------ domain errors

class ApiError(HTTPException):
    """Unified JSON error envelope for every business endpoint."""

    def __init__(self, status: int, code: str, message: str,
                 details: dict | None = None):
        super().__init__(
            status_code=status,
            detail={"code": code, "message": message,
                    "details": details or {}})


def row_to_project(row: sqlite3.Row) -> dict:
    return {
        "project_id": row["project_id"],
        "name": row["name"],
        "description": row["description"],
        "site_address": row["site_address"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "archived_at": row["archived_at"],
    }


def row_to_photo(row: sqlite3.Row) -> dict:
    return {
        "photo_id": row["photo_id"],
        "project_id": row["project_id"],
        "original_sha256": row["original_sha256"],
        "original_filename": row["original_filename"],
        "mime_type": row["mime_type"],
        "byte_size": row["byte_size"],
        "captured_at": row["captured_at"],
        "received_at": row["received_at"],
        "device_id": row["device_id"],
        "evidence_id": row["evidence_id"],
        "created_at": row["created_at"],
        # trust annotations (P1 #6): client claims are DATA, not trust
        "trust_notes": {
            "captured_at": "client-claimed, untrusted",
            "device_id": "client-claimed, untrusted",
        },
    }


def row_to_evidence_link(row: sqlite3.Row) -> dict:
    return {
        "evidence_link_id": row["evidence_link_id"],
        "photo_id": row["photo_id"],
        "evidence_reference": row["evidence_reference"],
        "evidence_type": row["evidence_type"],
        "original_sha256": row["original_sha256"],
        "verification_status": row["verification_status"],
        "failure_reason": row["failure_reason"],
        "verified_at": row["verified_at"],
        "created_at": row["created_at"],
    }


# ------------------------------------------------------------ backup / restore

def backup_to(target: Path, db_path: Path | None = None) -> int:
    """SQLite-consistent online backup via VACUUM INTO - NEVER a raw
    copy of a live database file. Returns the page count."""
    conn = connect(db_path)
    try:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        conn.execute("VACUUM INTO ?", (str(target),))
        pages = conn.execute(
            "SELECT page_count FROM pragma_page_count()").fetchone()[0]
        return pages
    finally:
        conn.close()


def verify_backup(backup_path: Path) -> dict:
    """Open a backup read-only: integrity check + row counts + foreign
    key audit. Raises on corruption - a backup that cannot be verified
    is not a backup."""
    conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"backup integrity_check: {integrity}")
        fka = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fka:
            raise ValueError(f"backup foreign_key_check violations: {fka[:5]}")
        counts = {
            "projects": conn.execute(
                "SELECT COUNT(*) FROM mvp_projects").fetchone()[0],
            "photos": conn.execute(
                "SELECT COUNT(*) FROM mvp_photos").fetchone()[0],
            "evidence_links": conn.execute(
                "SELECT COUNT(*) FROM mvp_evidence_links").fetchone()[0],
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        return {"version": version, "counts": counts}
    finally:
        conn.close()


def restore_from(backup_path: Path, target_db: Path) -> None:
    """Restore into a FRESH target path (must not exist or must be an
    empty/absent file) - restore never silently overwrites live data."""
    target_db = Path(target_db)
    if target_db.exists() and target_db.stat().st_size > 0:
        raise ValueError("refusing to restore onto a non-empty database - "
                         "restore must target a fresh path")
    shutil_path = backup_path
    import shutil
    target_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(shutil_path, target_db)
    # verify the restored copy is loadable and consistent
    info = verify_backup(target_db)
    return info
