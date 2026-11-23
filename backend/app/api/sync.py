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
    op: Literal["project_create", "project_update", "photo_register"]
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


_EXECUTORS = {
    "project_create": _exec_project_create,
    "project_update": _exec_project_update,
    "photo_register": _exec_photo_register,
}


# ------------------------------------------------------------ batch endpo


def _op_request_hash(op: SyncOp) -> str:
    """Canonical hash of the OPERATION as submitted - the replay-same /
    conflict-different discriminator (P1 #1/#2)."""
    return hashlib.sha256(json.dumps(
        {"op": op.op, "payload": op.payload},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
                result = executor(conn, token, op.payload)
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
            map_entry = {}
            if "local_id" in result:
                if "server_photo_id" in result:
                    map_entry[result["local_id"]] = result["server_photo_id"]
                elif "server_project_id" in result:
                    map_entry[result["local_id"]] = result["server_project_id"]
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
                                  "original)$")):
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
