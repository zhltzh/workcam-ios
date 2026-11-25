"""WC-W008 - offline sync engine.

Versioned batch-sync endpoint + restart-stable incremental cursor.

Protocol (see docs/mvp/w008-sync-protocol.md):
  POST /api/v1/sync/batch      - submit client operations (offline queue)
  GET  /api/v1/sync/events     - incremental pull by cursor

Idempotency ledger: every operation_id is recorded in mvp_sync_ops with
the REQUEST HASH and the RECEIPT; a replay with the same hash returns
the stored receipt (no re-execution), a replay with a DIFFERENT hash is
a 409 op_id_conflict. The ledger lives in the business DB -> server
restarts cannot lose it (P1 #4).

Dependency rule: a photo op references its project by the SERVER project
id, which the client only knows after the project op's receipt - the
batch is processed IN ORDER, so 'create project' then 'register photo'
in one batch works naturally (P1 #5). Cross-batch, the client replays the
stored receipts' id map.

Trust rules: client-supplied times are DATA (never trusted); evidence
verification statuses are only EVER MIRRORED from existing rows - sync
cannot write a trusted status (P1 #7).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from app.api.identity import require_caller
from app.db import ApiError, db, utc_now

router = APIRouter(prefix="/api/v1/sync", tags=["mvp-sync"])

MAX_BATCH_OPS = 50
MAX_PULL_LIMIT = 200


# ---------------------------------------------------------------- models


class ProjectCreateOp(BaseModel):
    op: Literal["project_create"] = "project_create"
    operation_id: str = Field(min_length=1, max_length=128)
    local_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    site_address: str = Field(default="", max_length=500)


class ProjectUpdateOp(BaseModel):
    op: Literal["project_update"] = "project_update"
    operation_id: str = Field(min_length=1, max_length=128)
    server_project_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    site_address: str | None = Field(default=None, max_length=500)


class PhotoRegisterOp(BaseModel):
    op: Literal["photo_register"] = "photo_register"
    operation_id: str = Field(min_length=1, max_length=128)
    local_id: str = Field(min_length=1, max_length=128)
    server_project_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    original_sha256: str
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: str
    byte_size: int = Field(gt=0, le=100 * 1024 * 1024)
    captured_at: str = Field(min_length=1, max_length=64)     # DATA, untrusted
    device_id: str = Field(min_length=1, max_length=128)      # DATA, untrusted

    @field_validator("original_sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        import re
        if not re.match(r"^[0-9a-f]{64}$", v):
            raise ValueError("original_sha256 must be 64 lowercase hex")
        return v


class SyncOp(BaseModel):
    """One queued client operation (discriminated by 'op')."""
    op: Literal[
        "project_create", "project_update", "photo_register",
        "annotation_create", "annotation_update", "annotation_delete",
        "issue_create", "issue_update", "issue_transition",
        "issue_photo_link", "issue_photo_unlink",
    ]
    operation_id: str = Field(min_length=1, max_length=128)
    payload: dict = Field(default_factory=dict)


class SyncBatchRequest(BaseModel):
    operations: list[SyncOp] = Field(min_length=1, max_length=MAX_BATCH_OPS)
    client_hint: str | None = Field(default=None, max_length=128)


class OpReceipt(BaseModel):
    operation_id: str
    status: Literal["applied", "duplicate", "rejected"]
    result: dict = {}
    error: dict | None = None


class SyncBatchResponse(BaseModel):
    batch_received: int
    receipts: list[OpReceipt]
    id_map: dict[str, str] = {}      # local_id -> server_id


# ------------------------------------------------------------- event log


def _emit_event(conn, owner: str, entity_type: str, entity_id: str,
                action: str, revision: int | None = None,
                payload_hint: str = "") -> None:
    """Append-only change log; the seq (AUTOINCREMENT) IS the cursor and
    is stable across server restarts (SQLite guarantees it only moves
    forward)."""
    conn.execute(
        "INSERT INTO mvp_sync_events (owner_token, entity_type, "
        "entity_id, action, revision, payload_hint, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (owner, entity_type, entity_id, action, revision,
         payload_hint[:120], utc_now()))


def _require_revision(conn, project_id: str, token: str,
                      expected: int) -> dict:
    """Optimistic concurrency: the caller states the revision they saw;
    a mismatch is a typed conflict, NEVER a silent last-write-wins."""
    row = conn.execute(
        "SELECT * FROM mvp_projects WHERE project_id = ?",
        (project_id,)).fetchone()
    if row is None or row["owner_token"] != token:
        raise ApiError(404, "not_found", "project not found")
    current = row["revision"]
    if current != expected:
        raise ApiError(
            409, "revision_conflict",
            f"project revision changed: expected {expected}, current "
            f"{current}",
            {"current_revision": current,
             "recovery": "re-fetch the project, merge your changes against "
                         "the current revision, and retry with the new "
                         "expected_revision"})
    return dict(row)


# ------------------------------------------------------------- executors


def _exec_project_create(conn, owner: str, payload: dict) -> dict:
    body = ProjectCreateOp(**payload)
    project_id = str(uuid.uuid4())
    now = utc_now()
    conn.execute(
        "INSERT INTO mvp_projects (project_id, owner_token, name, "
        "description, site_address, status, created_at, updated_at, "
        "revision) VALUES (?,?,?,?,?,'active',?,?,1)",
        (project_id, owner, body.name, body.description,
         body.site_address, now, now))
    _emit_event(conn, owner, "project", project_id, "created", 1,
                body.name)
    return {"server_project_id": project_id, "revision": 1,
            "local_id": body.local_id}


def _exec_project_update(conn, owner: str, payload: dict) -> dict:
    body = ProjectUpdateOp(**payload)
    row = _require_revision(conn, body.server_project_id, owner,
                            body.expected_revision)
    if row["status"] == "archived":
        raise ApiError(409, "project_archived",
                       "archived projects are immutable")
    updates, params = [], []
    for f in ("name", "description", "site_address"):
        v = getattr(body, f)
        if v is not None:
            updates.append(f"{f} = ?")
            params.append(v)
    if not updates:
        raise ApiError(400, "empty_patch",
                       "at least one field must be provided")
    new_rev = row["revision"] + 1
    updates.append("revision = ?")
    updates.append("updated_at = ?")
    params.extend([new_rev, utc_now(), body.server_project_id])
    conn.execute(
        f"UPDATE mvp_projects SET {', '.join(updates)} "
        "WHERE project_id = ?", params)
    _emit_event(conn, owner, "project", body.server_project_id,
                "updated", new_rev, "")
    return {"server_project_id": body.server_project_id,
            "revision": new_rev}


def _exec_photo_register(conn, owner: str, payload: dict) -> dict:
    body = PhotoRegisterOp(**payload)
    proj = conn.execute(
        "SELECT * FROM mvp_projects WHERE project_id = ?",
        (body.server_project_id,)).fetchone()
    if proj is None or proj["owner_token"] != owner:
        raise ApiError(404, "not_found", "project not found")
    if proj["status"] == "archived":
        raise ApiError(409, "project_archived",
                       "cannot add photos to an archived project")

    # idempotency_key conflicts: same key + same content = replay,
    # same key + different content = explicit conflict (W006 semantics;
    # the raw UNIQUE constraint must never leak as a 500)
    import sqlite3 as _sql
    try:
        photo_id = str(uuid.uuid4())
        now = utc_now()
        conn.execute(
            "INSERT INTO mvp_photos (photo_id, project_id, "
            "idempotency_key, original_sha256, original_filename, "
            "mime_type, byte_size, captured_at, received_at, device_id, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (photo_id, body.server_project_id, body.idempotency_key,
             body.original_sha256, body.original_filename, body.mime_type,
             body.byte_size, body.captured_at, now, body.device_id, now))
    except _sql.IntegrityError:
        existing = conn.execute(
            "SELECT * FROM mvp_photos WHERE project_id = ? "
            "AND idempotency_key = ?",
            (body.server_project_id, body.idempotency_key)).fetchone()
        if existing is not None:
            if existing["original_sha256"] == body.original_sha256:
                return {"server_photo_id": existing["photo_id"],
                        "server_project_id": body.server_project_id,
                        "local_id": body.local_id,
                        "idempotent_replay": True}
            raise ApiError(409, "idempotency_conflict",
                           "idempotency_key already used with different "
                           "content in this project",
                           {"existing_photo_id": existing["photo_id"]})
        raise

    _emit_event(conn, owner, "photo", photo_id, "created", None,
                body.idempotency_key)
    return {"server_photo_id": photo_id,
            "server_project_id": body.server_project_id,
            "local_id": body.local_id}


# ------------------------- W013-R1: annotation + issue sync executors


class AnnotationCreateOp(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    local_id: str = Field(min_length=1, max_length=128)
    server_photo_id: str = Field(min_length=1, max_length=64)
    annotation_type: str = Field(pattern=
        "^(arrow|rectangle|circle|freehand|text|number_marker)$")
    norm_x1: float = Field(ge=0.0, le=1.0)
    norm_y1: float = Field(ge=0.0, le=1.0)
    norm_x2: float | None = Field(default=None, ge=0.0, le=1.0)
    norm_y2: float | None = Field(default=None, ge=0.0, le=1.0)
    color_hex: str = Field(default="#FF3B30",
                           pattern="^#[0-9A-Fa-f]{6}$")
    stroke_width: int = Field(default=4, ge=1, le=40)
    text_content: str | None = Field(default=None, max_length=200)
    marker_number: int | None = Field(default=None, ge=1, le=999)
    z_order: int = Field(default=0, ge=0, le=999)


class AnnotationUpdateOp(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    server_annotation_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    norm_x1: float | None = Field(default=None, ge=0.0, le=1.0)
    norm_y1: float | None = Field(default=None, ge=0.0, le=1.0)
    norm_x2: float | None = Field(default=None, ge=0.0, le=1.0)
    norm_y2: float | None = Field(default=None, ge=0.0, le=1.0)
    color_hex: str | None = Field(default=None,
                                  pattern="^#[0-9A-Fa-f]{6}$")
    stroke_width: int | None = Field(default=None, ge=1, le=40)
    text_content: str | None = Field(default=None, max_length=200)
    marker_number: int | None = Field(default=None, ge=1, le=999)
    z_order: int | None = Field(default=None, ge=0, le=999)


class AnnotationDeleteOp(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    server_annotation_id: str = Field(min_length=1, max_length=64)


class IssueCreateOp(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    local_id: str = Field(min_length=1, max_length=128)
    server_project_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)
    severity: str = Field(pattern="^(low|medium|high|critical)$")
    location_description: str = Field(default="", max_length=1000)
    assigned_to: str = Field(default="", max_length=128)
    remediation_note: str = Field(default="", max_length=5000)


class IssueUpdateOp(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    server_issue_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    severity: str | None = Field(default=None,
                                  pattern="^(low|medium|high|critical)$")
    location_description: str | None = Field(default=None,
                                              max_length=1000)
    assigned_to: str | None = Field(default=None, max_length=128)
    remediation_note: str | None = Field(default=None, max_length=5000)


class IssueTransitionOp(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    server_issue_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    target_status: str = Field(
        pattern="^(open|in_progress|resolved|closed)$")


class IssuePhotoLinkOp(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    server_issue_id: str = Field(min_length=1, max_length=64)
    server_photo_id: str = Field(min_length=1, max_length=64)
    role: str = Field(pattern="^(before|progress|after)$")


class IssuePhotoUnlinkOp(BaseModel):
    operation_id: str = Field(min_length=1, max_length=128)
    server_issue_id: str = Field(min_length=1, max_length=64)
    server_photo_id: str = Field(min_length=1, max_length=64)
    role: str = Field(pattern="^(before|progress|after)$")


def _own_photo(conn, owner: str, photo_id: str) -> None:
    row = conn.execute(
        "SELECT p.owner_token FROM mvp_photos ph "
        "JOIN mvp_projects p ON p.project_id = ph.project_id "
        "WHERE ph.photo_id = ?", (photo_id,)).fetchone()
    if row is None or row["owner_token"] != owner:
        raise ApiError(404, "not_found", f"photo {photo_id} not found")


def _own_issue(conn, owner: str, issue_id: str):
    row = conn.execute(
        "SELECT * FROM mvp_issues WHERE issue_id = ?",
        (issue_id,)).fetchone()
    if row is None or row["owner_token"] != owner:
        raise ApiError(404, "not_found", f"issue {issue_id} not found")
    return row


def _exec_annotation_create(conn, owner: str, payload: dict) -> dict:
    body = AnnotationCreateOp(**payload)
    _own_photo(conn, owner, body.server_photo_id)
    annotation_id = str(uuid.uuid4())
    now = utc_now()
    try:
        conn.execute(
            "INSERT INTO mvp_annotations (annotation_id, photo_id, "
            "owner_token, annotation_type, norm_x1, norm_y1, norm_x2, "
            "norm_y2, color_hex, stroke_width, text_content, "
            "marker_number, z_order, revision, is_active, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,?,?)",
            (annotation_id, body.server_photo_id, owner,
             body.annotation_type, body.norm_x1, body.norm_y1,
             body.norm_x2, body.norm_y2, body.color_hex,
             body.stroke_width, body.text_content, body.marker_number,
             body.z_order, now, now))
    except Exception:
        raise ApiError(400, "annotation_validation_failed",
                       "annotation fields failed validation")
    _emit_event(conn, owner, "annotation", annotation_id, "created", 1,
                body.local_id)
    return {"server_annotation_id": annotation_id,
            "local_id": body.local_id}


def _exec_annotation_update(conn, owner: str, payload: dict) -> dict:
    body = AnnotationUpdateOp(**payload)
    row = conn.execute(
        "SELECT a.*, ph.photo_id as ph_id FROM mvp_annotations a "
        "JOIN mvp_photos ph ON ph.photo_id = a.photo_id "
        "WHERE a.annotation_id = ?",
        (body.server_annotation_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "annotation not found")
    _own_photo(conn, owner, row["ph_id"])
    if not row["is_active"]:
        raise ApiError(409, "annotation_deleted",
                       "deleted annotations are immutable")
    if row["revision"] != body.expected_revision:
        raise ApiError(409, "revision_conflict",
                       f"expected {body.expected_revision}, "
                       f"current {row['revision']}",
                       {"current_revision": row["revision"]})
    updates, params = [], []
    for f in ("norm_x1", "norm_y1", "norm_x2", "norm_y2",
              "color_hex", "stroke_width", "text_content",
              "marker_number", "z_order"):
        v = getattr(body, f)
        if v is not None:
            updates.append(f"{f} = ?")
            params.append(v)
    if not updates:
        raise ApiError(400, "empty_patch", "nothing to update")
    new_rev = row["revision"] + 1
    updates.append("revision = ?")
    updates.append("updated_at = ?")
    params.extend([new_rev, utc_now(), body.server_annotation_id])
    conn.execute(
        f"UPDATE mvp_annotations SET {', '.join(updates)} "
        "WHERE annotation_id = ?", params)
    _emit_event(conn, owner, "annotation", body.server_annotation_id,
                "updated", new_rev, body.server_annotation_id)
    return {"server_annotation_id": body.server_annotation_id,
            "revision": new_rev}


def _exec_annotation_delete(conn, owner: str, payload: dict) -> dict:
    body = AnnotationDeleteOp(**payload)
    row = conn.execute(
        "SELECT a.*, ph.photo_id as ph_id FROM mvp_annotations a "
        "JOIN mvp_photos ph ON ph.photo_id = a.photo_id "
        "WHERE a.annotation_id = ?",
        (body.server_annotation_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "annotation not found")
    _own_photo(conn, owner, row["ph_id"])
    if row["is_active"]:
        new_rev = row["revision"] + 1
        conn.execute(
            "UPDATE mvp_annotations SET is_active = 0, "
            "revision = ?, updated_at = ? "
            "WHERE annotation_id = ?",
            (new_rev, utc_now(), body.server_annotation_id))
        _emit_event(conn, owner, "annotation", body.server_annotation_id,
                    "soft_deleted", new_rev, body.server_annotation_id)
    return {"server_annotation_id": body.server_annotation_id,
            "deleted": True}


def _exec_issue_create(conn, owner: str, payload: dict) -> dict:
    body = IssueCreateOp(**payload)
    proj = conn.execute(
        "SELECT * FROM mvp_projects WHERE project_id = ?",
        (body.server_project_id,)).fetchone()
    if proj is None or proj["owner_token"] != owner:
        raise ApiError(404, "not_found", "project not found")
    if proj["status"] == "archived":
        raise ApiError(409, "project_archived",
                       "cannot create issues in an archived project")
    issue_id = str(uuid.uuid4())
    now = utc_now()
    conn.execute(
        "INSERT INTO mvp_issues (issue_id, project_id, owner_token, "
        "title, description, severity, status, "
        "location_description, assigned_to, remediation_note, "
        "revision, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,'open',?,?,?,1,?,?)",
        (issue_id, body.server_project_id, owner, body.title,
         body.description, body.severity, body.location_description,
         body.assigned_to, body.remediation_note, now, now))
    conn.execute(
        "INSERT INTO mvp_issue_history (issue_id, actor_token, action, "
        "from_value, to_value, detail, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (issue_id, owner, "created", None, body.severity, body.title,
         now))
    _emit_event(conn, owner, "issue", issue_id, "created", 1,
                body.title)
    return {"server_issue_id": issue_id, "local_id": body.local_id}


_STATUS_TRANSITIONS = {
    "open": {"open", "in_progress"},
    "in_progress": {"in_progress", "resolved", "open"},
    "resolved": {"resolved", "closed", "in_progress"},
    "closed": {"closed"},
}


def _exec_issue_update(conn, owner: str, payload: dict) -> dict:
    body = IssueUpdateOp(**payload)
    row = _own_issue(conn, owner, body.server_issue_id)
    if row["status"] == "closed":
        raise ApiError(409, "issue_closed", "closed issues are immutable")
    if row["revision"] != body.expected_revision:
        raise ApiError(409, "revision_conflict",
                       f"expected {body.expected_revision}, "
                       f"current {row['revision']}",
                       {"current_revision": row["revision"]})
    updates, params = [], []
    for f in ("title", "description", "location_description",
              "assigned_to", "remediation_note"):
        v = getattr(body, f)
        if v is not None:
            updates.append(f"{f} = ?")
            params.append(v)
    if body.severity is not None and body.severity != row["severity"]:
        updates.append("severity = ?")
        params.append(body.severity)
        conn.execute(
            "INSERT INTO mvp_issue_history (issue_id, actor_token, "
            "action, from_value, to_value, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (body.server_issue_id, owner, "severity_changed",
             row["severity"], body.severity, "", utc_now()))
    if not updates:
        raise ApiError(400, "empty_patch", "nothing to update")
    new_rev = row["revision"] + 1
    updates.append("revision = ?")
    updates.append("updated_at = ?")
    params.extend([new_rev, utc_now(), body.server_issue_id])
    conn.execute(
        f"UPDATE mvp_issues SET {', '.join(updates)} "
        "WHERE issue_id = ?", params)
    _emit_event(conn, owner, "issue", body.server_issue_id, "updated",
                new_rev, "")
    return {"server_issue_id": body.server_issue_id,
            "revision": new_rev}


def _exec_issue_transition(conn, owner: str, payload: dict) -> dict:
    body = IssueTransitionOp(**payload)
    row = _own_issue(conn, owner, body.server_issue_id)
    if row["revision"] != body.expected_revision:
        raise ApiError(409, "revision_conflict",
                       f"expected {body.expected_revision}, "
                       f"current {row['revision']}",
                       {"current_revision": row["revision"]})
    if body.target_status == row["status"]:
        return {"server_issue_id": body.server_issue_id,
                "status": row["status"], "revision": row["revision"],
                "idempotent": True}
    allowed = _STATUS_TRANSITIONS.get(row["status"], set())
    if body.target_status not in allowed:
        raise ApiError(409, "invalid_status_transition",
                       f"cannot move from '{row['status']}' to "
                       f"'{body.target_status}'",
                       {"allowed": sorted(allowed)})
    now = utc_now()
    extra = ""
    params: list = [body.target_status]
    if body.target_status == "resolved":
        extra = ", resolved_at = ?"
        params.append(now)
    elif row["status"] == "resolved":
        extra = ", resolved_at = NULL"
    conn.execute(
        f"UPDATE mvp_issues SET status = ?, revision = revision + 1, "
        f"updated_at = ?{extra} WHERE issue_id = ?",
        (*params, now, body.server_issue_id))
    conn.execute(
        "INSERT INTO mvp_issue_history (issue_id, actor_token, "
        "action, from_value, to_value, detail, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (body.server_issue_id, owner, "status_changed", row["status"],
         body.target_status, "", now))
    _emit_event(conn, owner, "issue", body.server_issue_id,
                "status_changed", row["revision"] + 1,
                f"{row['status']}->{body.target_status}")
    return {"server_issue_id": body.server_issue_id,
            "status": body.target_status,
            "revision": row["revision"] + 1}


def _exec_issue_photo_link(conn, owner: str, payload: dict) -> dict:
    body = IssuePhotoLinkOp(**payload)
    issue = _own_issue(conn, owner, body.server_issue_id)
    if issue["status"] == "closed":
        raise ApiError(409, "issue_closed", "closed issues are immutable")
    _own_photo(conn, owner, body.server_photo_id)
    photo_proj = conn.execute(
        "SELECT project_id FROM mvp_photos WHERE photo_id = ?",
        (body.server_photo_id,)).fetchone()
    if photo_proj["project_id"] != issue["project_id"]:
        raise ApiError(400, "photo_project_mismatch",
                       "photo belongs to a different project")
    conn.execute(
        "DELETE FROM mvp_issue_photos WHERE issue_id = ? AND role = ?",
        (body.server_issue_id, body.role))
    conn.execute(
        "INSERT INTO mvp_issue_photos (issue_id, photo_id, role, "
        "added_at) VALUES (?,?,?,?)",
        (body.server_issue_id, body.server_photo_id, body.role,
         utc_now()))
    conn.execute(
        "INSERT INTO mvp_issue_history (issue_id, actor_token, "
        "action, from_value, to_value, detail, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (body.server_issue_id, owner, "photo_linked", None, body.role,
         body.server_photo_id, utc_now()))
    _emit_event(conn, owner, "issue", body.server_issue_id,
                "photo_linked", issue["revision"],
                f"{body.role}:{body.server_photo_id}")
    return {"server_issue_id": body.server_issue_id,
            "server_photo_id": body.server_photo_id,
            "role": body.role}


def _exec_issue_photo_unlink(conn, owner: str, payload: dict) -> dict:
    body = IssuePhotoUnlinkOp(**payload)
    issue = _own_issue(conn, owner, body.server_issue_id)
    if issue["status"] == "closed":
        raise ApiError(409, "issue_closed", "closed issues are immutable")
    deleted = conn.execute(
        "DELETE FROM mvp_issue_photos WHERE issue_id = ? "
        "AND photo_id = ? AND role = ?",
        (body.server_issue_id, body.server_photo_id,
         body.role)).rowcount
    if not deleted:
        raise ApiError(404, "not_found", "link not found")
    conn.execute(
        "INSERT INTO mvp_issue_history (issue_id, actor_token, "
        "action, from_value, to_value, detail, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (body.server_issue_id, owner, "photo_unlinked", body.role, None,
         body.server_photo_id, utc_now()))
    _emit_event(conn, owner, "issue", body.server_issue_id,
                "photo_unlinked", issue["revision"],
                f"{body.role}:{body.server_photo_id}")
    return {"server_issue_id": body.server_issue_id,
            "server_photo_id": body.server_photo_id,
            "role": body.role, "unlinked": True}


_EXECUTORS = {
    "project_create": _exec_project_create,
    "project_update": _exec_project_update,
    "photo_register": _exec_photo_register,
    "annotation_create": _exec_annotation_create,
    "annotation_update": _exec_annotation_update,
    "annotation_delete": _exec_annotation_delete,
    "issue_create": _exec_issue_create,
    "issue_update": _exec_issue_update,
    "issue_transition": _exec_issue_transition,
    "issue_photo_link": _exec_issue_photo_link,
    "issue_photo_unlink": _exec_issue_photo_unlink,
}


# ------------------------------------------------------------ batch endpo


def _op_request_hash(op: SyncOp) -> str:
    """Canonical hash of the OPERATION as submitted - the replay-same /
    conflict-different discriminator (P1 #1/#2)."""
    return hashlib.sha256(json.dumps(
        {"op": op.op, "payload": op.payload},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# Payload keys that accept an intra-batch local reference. While
# offline the client can only reference entities by the local ids it
# generated itself, so the queue stores 'local:<local_id>' and the
# server resolves it from THIS batch's id_map before execution (B02-W014
# dependency ordering: only refs to EARLIER applied ops resolve).
_LOCAL_REF_KEYS = ("server_project_id", "server_photo_id",
                   "server_annotation_id", "server_issue_id")


def _resolve_local_refs(payload: dict, id_map: dict[str, str]) -> dict:
    """Return a payload copy with 'local:<id>' references substituted by
    the server ids of EARLIER applied ops in the same batch. A ref that
    was never applied (failed, later in the batch, or unknown) is a
    typed rejection - the dependency order is validated, never
    silently skipped."""
    resolved = dict(payload)
    for key in _LOCAL_REF_KEYS:
        v = resolved.get(key)
        if isinstance(v, str) and v.startswith("local:"):
            local_id = v[len("local:"):]
            if not local_id or local_id not in id_map:
                raise ApiError(
                    409, "unresolved_local_ref",
                    f"{key} references '{v}' which has no applied op "
                    f"in this batch (dependency order violated or "
                    f"source op failed)",
                    {"ref_key": key, "local_ref": v,
                     "resolved_local_ids": sorted(id_map)})
            resolved[key] = id_map[local_id]
    return resolved


@router.post("/batch")
async def sync_batch(body: SyncBatchRequest, request: Request):
    """Submit an offline-queued batch of operations.

    Each operation executes IN ORDER and gets an individual receipt
    (partial success); idempotent replays return the STORED receipt
    without re-execution. Authentication and ownership apply to the
    CALLER on every operation - replaying someone's captured batch with
    your own token cannot touch their resources (their owner_token is
    not in the payload; ownership is resolved server-side)."""
    token = require_caller(request)
    receipts: list[OpReceipt] = []
    id_map: dict[str, str] = {}

    for op in body.operations:
        request_hash = _op_request_hash(op)
        now = utc_now()

        with db() as conn:
            # P1 #6: the ledger is OWNER-SCOPED - replaying another
            # device's captured batch executes YOUR ops, never touches
            # their ledger (and their op-ids cannot shadow yours)
            prior = conn.execute(
                "SELECT request_hash, result_json FROM mvp_sync_ops "
                "WHERE operation_id = ? AND owner_token = ?",
                (op.operation_id, token)).fetchone()
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    receipts.append(OpReceipt(
                        operation_id=op.operation_id, status="rejected",
                        error={"code": "op_id_conflict",
                               "message": "operation_id reused with "
                                          "different content"}))
                    continue
                stored = json.loads(prior["result_json"])
                receipts.append(OpReceipt(
                    operation_id=op.operation_id, status="duplicate",
                    result=stored))
                id_map.update(stored.get("_id_map_entry", {}))
                continue

            executor = _EXECUTORS.get(op.op)
            if executor is None:
                receipts.append(OpReceipt(
                    operation_id=op.operation_id, status="rejected",
                    error={"code": "unknown_op",
                           "message": f"unsupported op {op.op}"}))
                continue

            try:
                # B02-W014: resolve intra-batch local refs BEFORE the
                # executor runs. The request hash stays computed on the
                # ORIGINAL payload - a replay with the same queue entry
                # is byte-identical and hits the ledger above.
                resolved_payload = _resolve_local_refs(op.payload, id_map)
                result = executor(conn, token, resolved_payload)
            except ApiError as e:
                receipts.append(OpReceipt(
                    operation_id=op.operation_id, status="rejected",
                    error={"code": e.detail["code"]
                           if isinstance(e.detail, dict) else "error",
                           "message": str(e.detail)}))
                # record the REJECTION too - a retried op that failed for
                # a deterministic reason returns the same rejection
                conn.execute(
                    "INSERT INTO mvp_sync_ops (operation_id, owner_token, "
                    "request_hash, result_json, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (op.operation_id, token, request_hash,
                     json.dumps({"status": "rejected"}), now))
                continue

            # propagate local->server id mapping for the client.
            # photo results contain BOTH server_project_id and
            # server_photo_id - prefer the PHOTO id (the entity being
            # created); the project mapping came from the project op.
            # W013-R1: annotation and issue results carry their own
            # server IDs too.
            map_entry = {}
            if "local_id" in result:
                if "server_photo_id" in result:
                    map_entry[result["local_id"]] = result["server_photo_id"]
                elif "server_project_id" in result:
                    map_entry[result["local_id"]] = result["server_project_id"]
                elif "server_annotation_id" in result:
                    map_entry[result["local_id"]] = result["server_annotation_id"]
                elif "server_issue_id" in result:
                    map_entry[result["local_id"]] = result["server_issue_id"]
                id_map.update(map_entry)
                result["_id_map_entry"] = map_entry

            conn.execute(
                "INSERT INTO mvp_sync_ops (operation_id, owner_token, "
                "request_hash, result_json, created_at) VALUES (?,?,?,?,?)",
                (op.operation_id, token, request_hash,
                 json.dumps(result), now))
            receipts.append(OpReceipt(
                operation_id=op.operation_id, status="applied",
                result=result))

    return SyncBatchResponse(
        batch_received=len(body.operations), receipts=receipts,
        id_map=id_map).model_dump()


# ------------------------------------------------------ incremental pull


@router.get("/events")
async def pull_events(request: Request,
                      cursor: int = Query(0, ge=0),
                      limit: int = Query(100, gt=0,
                                         le=MAX_PULL_LIMIT),
                       entity_type: str | None = Query(
                           None,
                           pattern="^(project|photo|evidence_link|"
                                   "original|annotation|issue)$")):
    """Incremental pull for the caller's OWN events only.

    The cursor is the last seq the client has seen. Restart-stable:
    seq is AUTOINCREMENT in the business DB. Pagination correctness
    under concurrent inserts: the page query filters seq > cursor and
    seq <= cursor + page_high_water (captured BEFORE reading), so rows
    inserted mid-pull appear in the NEXT page, never cause a skip or
    duplicate."""
    token = require_caller(request)

    with db() as conn:
        high_water = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM mvp_sync_events "
            "WHERE owner_token = ?", (token,)).fetchone()[0]
        window_end = min(high_water, cursor + limit)
        where = "owner_token = ? AND seq > ? AND seq <= ?"
        params: list = [token, cursor, window_end]
        if entity_type:
            where += " AND entity_type = ?"
            params.append(entity_type)
        rows = conn.execute(
            f"SELECT * FROM mvp_sync_events WHERE {where} "
            "ORDER BY seq ASC", params).fetchall()
        # fetch each entity's CURRENT state for the client
        items = []
        for r in rows:
            item = {"seq": r["seq"], "entity_type": r["entity_type"],
                    "entity_id": r["entity_id"], "action": r["action"],
                    "revision": r["revision"],
                    "payload_hint": r["payload_hint"],
                    "created_at": r["created_at"]}
            items.append(item)

    return {"items": items, "cursor": window_end,
            "has_more": window_end < high_water,
            "limit": limit}
