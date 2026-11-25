"""B02-W017 - engineering photo batch management API.

Batch operations over the caller's OWN photos with per-photo receipts,
a batch-idempotency ledger and resumable partial-failure handling.

  POST /api/v1/photos/batch/tags        add/remove tags (per-photo receipts)
  POST /api/v1/photos/batch/category    set business category
  POST /api/v1/photos/batch/note        set business note
  POST /api/v1/photos/batch/move         move to an authorized project
                                         (WHOLE-BATCH pre-validation)
  POST /api/v1/photos/batch/watermark   render watermark derivatives
                                         (W011 engine; originals read-only)
  POST /api/v1/photos/batch/export       export package (ZIP + manifest)
  GET  /api/v1/photos/search            keyset-paginated filter/search
  GET  /api/v1/photos/duplicates        content-addressed duplicate groups
  GET  /api/v1/photos/{pid}/business    business-metadata view
  GET  /api/v1/photo-batches/{bid}      stored batch receipts (resume view)
  GET  /api/v1/photo-exports/{eid}/download

Hard rules (task constraints, enforced and test-pinned):
  - originals and signed evidence stay IMMUTABLE: no batch op writes
    mvp_photos.original_sha256 / captured_at / device_id, nor any
    evidence-link row. Business metadata lives in its OWN tables
    (mvp_photo_business / mvp_photo_tags) - separate from evidence.
  - batch idempotency: the caller supplies a batch_id; SUCCESSFUL items
    are recorded in mvp_batch_ops and replayed as stored receipts
    (never re-executed); FAILED items are NOT recorded so a retry of
    the failed subset under the same batch_id re-executes exactly the
    failed photos. A replay with the same (batch_id, photo) but
    DIFFERENT parameters is a typed per-photo conflict (W008 op-id
    discipline applied per photo).
  - own batch limit: MAX_BATCH_PHOTOS (500) per request - independent
    of the 50-op W008 sync batch, whose ledger and protocol stay
    untouched.
  - batch ops emit 'photo'/'updated' events into the W008 change log
    (v8 CHECK-compatible vocabulary) so offline clients aligned with
    the B02-W014 event stream see the changes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.api.identity import require_caller
from app.api.mvp import _get_owned_photo, _require_uuid
from app.api.sync import _emit_event
from app.db import ApiError, db, row_to_photo, utc_now
from app.services.watermark_render import WatermarkFields, get_template

router = APIRouter(prefix="/api/v1", tags=["b02-photo-batch"])

MAX_BATCH_PHOTOS = 500
MAX_TAGS_PER_REQUEST = 50
MAX_SEARCH_LIMIT = 200
DEFAULT_SEARCH_LIMIT = 50


# ----------------------------------------------------------- request models


class BatchId(BaseModel):
    batch_id: str = Field(min_length=1, max_length=128)
    photo_ids: list[str] = Field(min_length=1, max_length=MAX_BATCH_PHOTOS)

    @field_validator("photo_ids")
    @classmethod
    def _no_dupe(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("photo_ids must be unique within a batch")
        return v


class TagsBatchRequest(BatchId):
    action: Literal["add", "remove"]
    tags: list[str] = Field(min_length=1, max_length=MAX_TAGS_PER_REQUEST)

    @field_validator("tags")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        cleaned = [t.strip() for t in v]
        if any(not t for t in cleaned):
            raise ValueError("tags must be non-blank after trimming")
        if any(len(t) > 64 for t in cleaned):
            raise ValueError("tags must be at most 64 characters")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("tags must be unique within a request")
        return cleaned


class CategoryBatchRequest(BatchId):
    category: str = Field(default="", max_length=100)


class NoteBatchRequest(BatchId):
    note: str = Field(max_length=5000)


class MoveBatchRequest(BatchId):
    target_project_id: str = Field(min_length=1, max_length=64)


class WatermarkBatchRequest(BatchId):
    template: str = Field(min_length=1, max_length=32)
    fields: WatermarkFields
    company_logo_b64: str | None = None


class ExportBatchRequest(BatchId):
    include_originals: bool = True


# --------------------------------------------------------- ledger + engine


def _item_hash(op_type: str, photo_id: str, params: dict) -> str:
    """Per-PHOTO request hash (not per batch): a retry that carries only
    the failed subset produces the same hash for the remaining photos,
    so stored receipts still match (W008 request-hash discipline, scoped
    to the single photo)."""
    return hashlib.sha256(json.dumps(
        {"op": op_type, "photo_id": photo_id, "params": params},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _receipt(photo_id: str, status: str, data: dict | None = None,
             code: str | None = None, message: str | None = None) -> dict:
    r: dict[str, Any] = {"photo_id": photo_id, "status": status}
    if data is not None:
        r["data"] = data
    if code is not None:
        r["error"] = {"code": code, "message": message or code}
    return r


def _ledger_probe(conn, batch_id: str, token: str, photo_id: str):
    return conn.execute(
        "SELECT request_hash, result_json, op_type FROM mvp_batch_ops "
        "WHERE batch_id = ? AND owner_token = ? AND photo_id = ?",
        (batch_id, token, photo_id)).fetchone()


def _ledger_record(conn, batch_id: str, token: str, op_type: str,
                   photo_id: str, request_hash: str, result: dict) -> None:
    conn.execute(
        "INSERT INTO mvp_batch_ops (batch_id, owner_token, op_type, "
        "photo_id, request_hash, result_json, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (batch_id, token, op_type, photo_id, request_hash,
         json.dumps(result), utc_now()))


def _batch_summary(batch_id: str, op_type: str, results: list[dict],
                   extra: dict | None = None) -> dict:
    out = {
        "batch_id": batch_id,
        "op": op_type,
        "total": len(results),
        "succeeded": sum(1 for r in results if r["status"] == "success"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "replayed": sum(1 for r in results if r["status"] == "duplicate"),
        "results": results,
        "resume_hint": ("retry failed photo_ids under the SAME batch_id "
                        "with identical parameters: succeeded photos are "
                        "replayed from the ledger, failed photos are "
                        "re-executed; changing parameters for a photo "
                        "already recorded under this batch_id is a "
                        "batch_id_conflict"),
    }
    if extra:
        out.update(extra)
    return out


def _run_ledger_batch(token: str, batch_id: str, op_type: str,
                     photo_ids: list[str], params: dict,
                     execute) -> list[dict]:
    """Per-photo ledger-driven execution for DB-transaction executors.

    execute(conn, token, photo_id) -> result dict; raises ApiError on a
    typed per-photo failure. Each photo gets its own transaction: a
    failure never rolls back other photos (W008 per-op discipline).

    SUCCESS  -> ledger row written (replay returns stored receipt)
    FAILURE  -> NO ledger row (photo stays retryable under same batch)
    """
    results: list[dict] = []
    for pid in photo_ids:
        req_hash = _item_hash(op_type, pid, params)
        with db() as conn:
            prior = _ledger_probe(conn, batch_id, token, pid)
            if prior is not None:
                if prior["request_hash"] != req_hash:
                    results.append(_receipt(
                        pid, "failed", code="batch_id_conflict",
                        message=("photo already processed under batch_id "
                                 "with DIFFERENT parameters - use a new "
                                 "batch_id to change it")))
                else:
                    stored = json.loads(prior["result_json"])
                    results.append(_receipt(pid, "duplicate",
                                            data=stored))
                continue
            try:
                data = execute(conn, token, pid)
            except ApiError as e:
                code = (e.detail.get("code")
                        if isinstance(e.detail, dict) else "error")
                results.append(_receipt(pid, "failed", code=code,
                                        message=str(
                                            e.detail.get("message", code)
                                            if isinstance(e.detail, dict)
                                            else code)))
                continue
            _ledger_record(conn, batch_id, token, op_type, pid,
                           req_hash, data)
            results.append(_receipt(pid, "success", data=data))
    return results


# ------------------------------------------------------ business helpers


def _owned_photo_row(conn, token: str, photo_id: str):
    """Photo row + ownership chain through the project (404-identical
    cross-subject access, no existence oracle)."""
    row = conn.execute(
        "SELECT ph.*, p.owner_token AS owner_token "
        "FROM mvp_photos ph "
        "JOIN mvp_projects p ON p.project_id = ph.project_id "
        "WHERE ph.photo_id = ?", (photo_id,)).fetchone()
    if row is None or row["owner_token"] != token:
        raise ApiError(404, "not_found", f"photo {photo_id} not found")
    return row


def _owned_project_row(conn, token: str, project_id: str):
    row = conn.execute(
        "SELECT * FROM mvp_projects WHERE project_id = ?",
        (project_id,)).fetchone()
    if row is None or row["owner_token"] != token:
        raise ApiError(404, "not_found", "project not found")
    return row


def _upsert_business(conn, token: str, photo_id: str,
                     set_fields: dict) -> dict:
    """Insert-or-update the business-metadata row, bumping revision and
    stamping updated_at/updated_by. Only touches mvp_photo_business -
    evidence-adjacent mvp_photos columns are never written here."""
    now = utc_now()
    conn.execute(
        "INSERT INTO mvp_photo_business (photo_id, category, note, "
        "updated_by, updated_at, revision) VALUES (?,?,?,?,?,1) "
        "ON CONFLICT(photo_id) DO UPDATE SET "
        + ", ".join(f"{f} = excluded.{f}" for f in set_fields)
        + ", updated_by = excluded.updated_by, "
          "updated_at = excluded.updated_at, "
          "revision = mvp_photo_business.revision + 1",
        (photo_id, set_fields.get("category", ""),
         set_fields.get("note", ""), token, now))
    row = conn.execute(
        "SELECT * FROM mvp_photo_business WHERE photo_id = ?",
        (photo_id,)).fetchone()
    return {
        "category": row["category"], "note": row["note"],
        "revision": row["revision"], "updated_at": row["updated_at"],
    }


def _photo_tags(conn, photo_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT tag FROM mvp_photo_tags WHERE photo_id = ? "
        "ORDER BY tag", (photo_id,)).fetchall()
    return [r["tag"] for r in rows]


# --------------------------------------------------------------- endpoints


@router.post("/photos/batch/tags")
async def batch_tags(body: TagsBatchRequest, request: Request):
    token = require_caller(request)
    op_type = "tag_add" if body.action == "add" else "tag_remove"
    params = {"action": body.action, "tags": sorted(body.tags)}

    def execute(conn, tok, pid):
        _owned_photo_row(conn, tok, pid)
        now = utc_now()
        changed = 0
        if body.action == "add":
            for tag in body.tags:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO mvp_photo_tags "
                    "(photo_id, tag, added_at) VALUES (?,?,?)",
                    (pid, tag, now))
                changed += cur.rowcount
            result = {"tags": _photo_tags(conn, pid),
                      "added": changed}
        else:
            for tag in body.tags:
                cur = conn.execute(
                    "DELETE FROM mvp_photo_tags "
                    "WHERE photo_id = ? AND tag = ?", (pid, tag))
                changed += cur.rowcount
            result = {"tags": _photo_tags(conn, pid),
                      "removed": changed}
        _emit_event(conn, tok, "photo", pid, "updated", None,
                    f"batch_tags:{body.action}:{len(body.tags)}")
        return result

    results = _run_ledger_batch(token, body.batch_id, op_type,
                                body.photo_ids, params, execute)
    return _batch_summary(body.batch_id, op_type, results)


@router.post("/photos/batch/category")
async def batch_category(body: CategoryBatchRequest, request: Request):
    token = require_caller(request)
    params = {"category": body.category}

    def execute(conn, tok, pid):
        _owned_photo_row(conn, tok, pid)
        result = _upsert_business(conn, tok, pid,
                                  {"category": body.category})
        _emit_event(conn, tok, "photo", pid, "updated", None,
                    f"batch_category:{body.category[:40]}")
        return result

    results = _run_ledger_batch(token, body.batch_id, "category",
                                body.photo_ids, params, execute)
    return _batch_summary(body.batch_id, "category", results)


@router.post("/photos/batch/note")
async def batch_note(body: NoteBatchRequest, request: Request):
    token = require_caller(request)
    params = {"note": body.note}

    def execute(conn, tok, pid):
        _owned_photo_row(conn, tok, pid)
        result = _upsert_business(conn, tok, pid, {"note": body.note})
        _emit_event(conn, tok, "photo", pid, "updated", None,
                    f"batch_note:{len(body.note)}")
        return result

    results = _run_ledger_batch(token, body.batch_id, "note",
                                body.photo_ids, params, execute)
    return _batch_summary(body.batch_id, "note", results)


@router.post("/photos/batch/move")
async def batch_move(body: MoveBatchRequest, request: Request):
    """Move photos to an AUTHORIZED project (caller-owned + active).

    WHOLE-BATCH PRE-VALIDATION (task constraint): every photo and the
    target project are checked BEFORE any row is written; any violation
    rejects the entire batch with a typed error and per-photo details -
    a rejected move never leaves a half-moved set. After pre-validation
    the per-photo ledger flow takes over (idempotent replays, retryable
    failures).

    Guard rails (evidence discipline):
      - photos linked to an inspection issue are NOT movable (moving
        would strand a cross-project before/after association, which
        the W013 single-link path forbids)
      - photos sitting in an ARCHIVED project are NOT movable
        (archived = frozen work product)
      - moving to the photo's current project is an idempotent no-op
      - only mvp_photos.project_id changes - evidence links, originals
        bindings, annotations and their verification state are untouched
    """
    token = require_caller(request)
    params = {"target_project_id": body.target_project_id}

    errors: list[dict] = []
    with db() as conn:
        target = _owned_project_row(conn, token, body.target_project_id)
        if target["status"] == "archived":
            raise ApiError(409, "project_archived",
                          "cannot move photos into an archived project")
        for pid in body.photo_ids:
            try:
                photo = _owned_photo_row(conn, token, pid)
            except ApiError as e:
                errors.append({"photo_id": pid, "code": e.detail["code"],
                               "message": e.detail["message"]})
                continue
            if photo["project_id"] == body.target_project_id:
                continue          # no-op target, not an error
            if photo["project_id"] is not None:
                src = conn.execute(
                    "SELECT status FROM mvp_projects "
                    "WHERE project_id = ?",
                    (photo["project_id"],)).fetchone()
                if src is not None and src["status"] == "archived":
                    errors.append({
                        "photo_id": pid, "code": "source_project_archived",
                        "message": "photos in an archived project are "
                                   "frozen and cannot be moved"})
                    continue
            linked = conn.execute(
                "SELECT 1 FROM mvp_issue_photos WHERE photo_id = ? "
                "LIMIT 1", (pid,)).fetchone()
            if linked is not None:
                errors.append({
                    "photo_id": pid, "code": "photo_linked_to_issues",
                    "message": "photo is linked to inspection issues - "
                               "unlink it before moving between projects"})
    if errors:
        raise ApiError(
            400, "batch_precondition_failed",
            "batch move rejected without executing ANY move",
            {"errors": errors,
             "recovery": "fix or drop the listed photos, or use a new "
                         "batch_id"})

    def execute(conn, tok, pid):
        photo = _owned_photo_row(conn, tok, pid)
        if photo["project_id"] == body.target_project_id:
            return {"photo_id": pid,
                    "project_id": body.target_project_id,
                    "moved": False, "reason": "already in target project"}
        conn.execute(
            "UPDATE mvp_photos SET project_id = ? WHERE photo_id = ?",
            (body.target_project_id, pid))
        _emit_event(conn, tok, "photo", pid, "updated", None,
                    f"batch_moved:{body.target_project_id}")
        return {"photo_id": pid, "project_id": body.target_project_id,
                "moved": True, "previous_project_id": photo["project_id"]}

    results = _run_ledger_batch(token, body.batch_id, "move",
                                body.photo_ids, params, execute)
    return _batch_summary(body.batch_id, "move", results)


@router.post("/photos/batch/watermark")
async def batch_watermark(body: WatermarkBatchRequest, request: Request):
    """Render the SAME watermark template+fields over a set of photos.

    Reuses the W011 render path (render_watermark_for_photo): the
    original bytes are read-only, the server resolves each photo's
    evidence status itself, and per-photo idempotency is the W011
    (photo, template, params-hash, original-sha) key - re-running an
    already-rendered photo returns the SAME derivative.

    Executions happen OUTSIDE the ledger transaction (rendering is
    CPU-heavy; the W011 engine manages its own transactions). The
    ledger row is written after a successful render, so an interrupted
    batch replays stored receipts and re-renders only the missing ones
    (the W011 idempotency probe makes even a double render harmless)."""
    token = require_caller(request)
    try:
        spec = get_template(body.template)
    except ValueError as e:
        raise ApiError(400, "unknown_template", str(e))

    fields = body.fields
    logo_sha = ""
    if fields.show_company_logo and body.company_logo_b64:
        import base64
        try:
            logo_bytes = base64.b64decode(body.company_logo_b64,
                                          validate=True)
            logo_sha = hashlib.sha256(logo_bytes).hexdigest()[:16]
            fields.company_logo = logo_bytes
        except Exception:
            raise ApiError(400, "invalid_logo",
                           "company_logo_b64 is not valid base64")
    # request-level pre-validation: the SAME fields render over every
    # photo - a combination the template renders NOTHING from must
    # reject the whole batch up front (not 500 times per photo)
    from app.services.watermark_render import template_lines
    if not template_lines(spec, fields):
        raise ApiError(400, "nothing_to_render",
                       "the template renders no line with these field "
                       "toggles - enable at least one supported field")
    params = {"template": body.template,
              "fields_hash": fields.render_params_hash(),
              "logo_sha256": logo_sha}

    from app.api.derivatives import render_watermark_for_photo
    results: list[dict] = []
    for pid in body.photo_ids:
        req_hash = _item_hash("watermark", pid, params)
        with db() as conn:
            prior = _ledger_probe(conn, body.batch_id, token, pid)
            if prior is not None:
                if prior["request_hash"] != req_hash:
                    results.append(_receipt(
                        pid, "failed", code="batch_id_conflict",
                        message=("photo already processed under batch_id "
                                 "with DIFFERENT parameters - use a new "
                                 "batch_id to change it")))
                else:
                    results.append(_receipt(
                        pid, "duplicate",
                        data=json.loads(prior["result_json"])))
                continue
        try:
            _require_uuid(pid, "photo_id")
            data = render_watermark_for_photo(token, pid, body.template,
                                               fields.model_copy(deep=True))
        except ApiError as e:
            code = (e.detail.get("code")
                    if isinstance(e.detail, dict) else "error")
            results.append(_receipt(pid, "failed", code=code,
                                    message=str(
                                        e.detail.get("message", code)
                                        if isinstance(e.detail, dict)
                                        else code)))
            continue
        except ValueError as e:
            # defensive: render-engine validation failures are typed
            # per-photo failures, never a batch-wide 500
            results.append(_receipt(pid, "failed", code="render_error",
                                    message=str(e)[:200]))
            continue
        with db() as conn:
            _ledger_record(conn, body.batch_id, token, "watermark", pid,
                           req_hash, data)
        results.append(_receipt(pid, "success", data=data))
    return _batch_summary(body.batch_id, "watermark", results)


# ---------------------------------------------------------- batch export


def _export_entry(conn, token: str, photo_id: str) -> dict:
    """Assemble one photo's export manifest entry (READ-ONLY). Raises
    the typed 404 for foreign/unknown photos."""
    from app.api.derivatives import _resolve_actual_evidence_status
    photo = _owned_photo_row(conn, token, photo_id)
    tags = _photo_tags(conn, photo_id)
    business = conn.execute(
        "SELECT * FROM mvp_photo_business WHERE photo_id = ?",
        (photo_id,)).fetchone()
    issues = conn.execute(
        "SELECT i.issue_id, i.title, i.status, i.severity, ip.role "
        "FROM mvp_issue_photos ip JOIN mvp_issues i "
        "ON i.issue_id = ip.issue_id WHERE ip.photo_id = ? "
        "ORDER BY i.issue_id", (photo_id,)).fetchall()
    derivative = conn.execute(
        "SELECT derivative_sha256, template_name FROM mvp_derivatives "
        "WHERE photo_id = ? ORDER BY created_at DESC LIMIT 1",
        (photo_id,)).fetchone()
    bound = conn.execute(
        "SELECT 1 FROM mvp_photo_originals WHERE photo_id = ?",
        (photo_id,)).fetchone() is not None
    return {
        "photo_id": photo_id,
        "project_id": photo["project_id"],
        "original_sha256": photo["original_sha256"],
        "original_filename": photo["original_filename"],
        "mime_type": photo["mime_type"],
        "byte_size": photo["byte_size"],
        "captured_at": photo["captured_at"],
        "received_at": photo["received_at"],
        "device_id": photo["device_id"],
        "tags": tags,
        "category": business["category"] if business else "",
        "note": business["note"] if business else "",
        "issues": [{"issue_id": i["issue_id"], "title": i["title"],
                     "status": i["status"], "severity": i["severity"],
                     "role": i["role"]} for i in issues],
        "derivative_sha256": (derivative["derivative_sha256"]
                              if derivative else None),
        "original_uploaded": bound,
        "evidence_status": _resolve_actual_evidence_status(
            conn, photo_id, photo["original_sha256"]),
        "trust_notes": {
            "captured_at": "client-claimed, untrusted",
            "device_id": "client-claimed, untrusted",
            "evidence_status": "server-resolved from evidence links",
        },
    }


def _export_store():
    import app.db as dbm
    from pathlib import Path
    return dbm._db_path().parent / "exports"


@router.post("/photos/batch/export")
async def batch_export(body: ExportBatchRequest, request: Request):
    """Build an export package (ZIP: manifest.json + originals by
    content hash + guide) and register it for download.

    Per-photo resume semantics: a photo's manifest ENTRY is the batch
    ledger's stored result. Retrying the failed subset under the same
    batch_id re-uses the stored entries (no re-execution) and builds a
    NEW export containing every photo - the client ends up with one
    complete package. Duplicate content is stored ONCE (content-addressed
    originals/{sha256}.jpg), mirroring the duplicate-detection view.
    The export NEVER contains tokens, enrollment codes or key material.
    """
    token = require_caller(request)
    params = {"include_originals": body.include_originals}

    # resolve every photo against the ledger first (stored entries are
    # re-used as-is - snapshot semantics, same as W012 reports)
    entries: list[dict] = []
    failed: list[dict] = []
    replayed_ids: set[str] = set()
    for pid in body.photo_ids:
        req_hash = _item_hash("export", pid, params)
        with db() as conn:
            prior = _ledger_probe(conn, body.batch_id, token, pid)
            if prior is not None:
                if prior["request_hash"] != req_hash:
                    failed.append(_receipt(
                        pid, "failed", code="batch_id_conflict",
                        message=("photo already exported under batch_id "
                                 "with DIFFERENT parameters - use a new "
                                 "batch_id")))
                else:
                    entry = json.loads(prior["result_json"])
                    entries.append(entry)
                    replayed_ids.add(pid)
                continue
            try:
                entry = _export_entry(conn, token, pid)
            except ApiError as e:
                failed.append(_receipt(pid, "failed",
                                       code=e.detail["code"],
                                       message=e.detail["message"]))
                continue
        with db() as conn:
            _ledger_record(conn, body.batch_id, token, "export", pid,
                           req_hash, entry)
        entries.append(entry)

    # build the ZIP artifact
    import io
    import zipfile
    from app.services.original_store import read_original

    export_id = str(uuid.uuid4())
    now = utc_now()
    buf = io.BytesIO()
    manifest: dict = {
        "export_id": export_id,
        "generated_at": now,
        "photo_count": len(entries),
        "include_originals": body.include_originals,
        "photos": entries,
        "files": [],
        "note": "export snapshot at generation time; business metadata "
                "is separate from the immutable evidence chain",
    }
    seen_sha: set[str] = set()

    def add_bytes(zf, arcname, data: bytes):
        manifest["files"].append({
            "path": arcname,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data)})
        zf.writestr(arcname, data)

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        if body.include_originals:
            for e in entries:
                sha = e["original_sha256"]
                if sha in seen_sha or not e["original_uploaded"]:
                    continue
                seen_sha.add(sha)
                try:
                    add_bytes(zf, f"originals/{sha}.jpg",
                              read_original(sha))
                except Exception:
                    manifest["files"].append({
                        "path": f"originals/{sha}.jpg", "missing": True})
        add_bytes(zf, "manifest.json",
                  json.dumps(manifest, indent=2, ensure_ascii=False)
                  .encode())
        add_bytes(zf, "verification-guide.txt", (
            "WORKCAM PHOTO EXPORT - VERIFICATION GUIDE\n"
            "=========================================\n\n"
            "1. manifest.json lists every exported file with SHA-256.\n"
            "2. originals/ are stored BY CONTENT HASH: two photos with "
            "the same hash share ONE file (duplicate content is not "
            "repeated).\n"
            "3. originals/ hold the immutable original photos; watermarked "
            "derivatives are NOT the trusted original evidence.\n"
            "4. evidence_status values are the SERVER's verification "
            "state at export time; they do not prove the capture scene "
            "was authentic.\n"
            "5. This package contains NO private keys, tokens, or "
            "enrollment codes.\n").encode())

    zip_bytes = buf.getvalue()
    zip_sha = hashlib.sha256(zip_bytes).hexdigest()

    store = _export_store()
    store.mkdir(parents=True, exist_ok=True)
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(store), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(zip_bytes)
        os.replace(tmp, store / f"{export_id}.zip")
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    with db() as conn:
        conn.execute(
            "INSERT INTO mvp_photo_exports (export_id, owner_token, "
            "batch_id, photo_count, include_originals, zip_sha256, "
            "file_count, byte_size, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (export_id, token, body.batch_id, len(entries),
             1 if body.include_originals else 0, zip_sha,
             len(manifest["files"]), len(zip_bytes), now))

    results = ([_receipt(e["photo_id"],
                         "duplicate" if e["photo_id"] in replayed_ids
                         else "success", data=e)
                for e in entries] + failed)
    # keep the caller's original ordering
    order = {pid: i for i, pid in enumerate(body.photo_ids)}
    results.sort(key=lambda r: order.get(r["photo_id"], 1 << 30))
    return _batch_summary(
        body.batch_id, "export", results,
        extra={"export_id": export_id,
               "zip_sha256": zip_sha,
               "download_path": f"/api/v1/photo-exports/{export_id}"
                                "/download",
               "snapshot_note": "stored entries replay as-is; newly "
                                "retried photos use their CURRENT state"})


@router.get("/photo-exports/{export_id}/download")
async def download_export(export_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(export_id, "export_id")
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM mvp_photo_exports WHERE export_id = ?",
            (export_id,)).fetchone()
        if row is None or row["owner_token"] != token:
            raise ApiError(404, "not_found", "export not found")
    path = _export_store() / f"{export_id}.zip"
    if not path.exists():
        raise ApiError(404, "object_missing", "export package missing")
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == row["zip_sha256"]
    return Response(
        content=data, media_type="application/zip",
        headers={
            "X-Export-Sha256": row["zip_sha256"],
            "Content-Disposition":
                f'attachment; filename="workcam-export-{export_id[:8]}.zip"'})


# ------------------------------------------------------------ batch view


@router.get("/photo-batches/{batch_id}")
async def batch_receipts(batch_id: str, request: Request,
                         op_type: str | None = Query(
                             None,
                             pattern="^(tag_add|tag_remove|category|note|"
                                     "move|watermark|export)$")):
    """Stored receipts for one batch (the resume view). Only
    SUCCEEDED/replayed photos live in the ledger; a photo absent here
    was either never processed or failed - both stay retryable under
    the same batch_id."""
    token = require_caller(request)
    with db() as conn:
        rows = conn.execute(
            "SELECT op_type, photo_id, result_json, created_at "
            "FROM mvp_batch_ops WHERE batch_id = ? AND owner_token = ? "
            + ("AND op_type = ?" if op_type else "")
            + " ORDER BY photo_id",
            ((batch_id, token, op_type) if op_type
             else (batch_id, token))).fetchall()
    return {
        "batch_id": batch_id,
        "op": op_type,
        "count": len(rows),
        "items": [{"op_type": r["op_type"], "photo_id": r["photo_id"],
                   "status": "success",
                   "result": json.loads(r["result_json"]),
                   "created_at": r["created_at"]} for r in rows],
        "note": "ledger holds SUCCESSFUL items only; missing photos are "
                "unprocessed-or-failed and remain retryable under the "
                "same batch_id",
    }


# ------------------------------------------------------- business view


@router.get("/photos/{photo_id}/business")
async def photo_business(photo_id: str, request: Request):
    """Business-metadata view for one photo: tags, category, note.
    Deliberately SEPARATE from the photo/evidence representation - the
    evidence view (GET /photos/{id}) never mixes these in."""
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    with db() as conn:
        _owned_photo_row(conn, token, photo_id)
        tags = _photo_tags(conn, photo_id)
        row = conn.execute(
            "SELECT * FROM mvp_photo_business WHERE photo_id = ?",
            (photo_id,)).fetchone()
    return {
        "photo_id": photo_id,
        "tags": tags,
        "category": row["category"] if row else "",
        "note": row["note"] if row else "",
        "revision": row["revision"] if row else None,
        "updated_at": row["updated_at"] if row else None,
        "note_trust": "business metadata is HUMAN-managed content, "
                      "kept separate from the immutable evidence chain",
    }


# ------------------------------------------------------------------ search

# sort key -> (SQL expression over mvp_photos, result-row column name)
_SORT_EXPR = {
    "captured_at": ("ph.captured_at", "captured_at"),
    "created_at": ("ph.created_at", "created_at"),
    "filename": ("ph.original_filename", "original_filename"),
    "byte_size": ("ph.byte_size", "byte_size"),
}


def _encode_cursor(sort: str, order: str, key_val, photo_id: str) -> str:
    raw = json.dumps({"k": key_val, "id": photo_id, "s": sort,
                      "o": order}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str, sort: str, order: str):
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = json.loads(base64.urlsafe_b64decode(cursor + pad).decode())
        if raw.get("s") != sort or raw.get("o") != order:
            raise ValueError("cursor/sort mismatch")
        return raw["k"], raw["id"]
    except Exception:
        raise ApiError(400, "invalid_cursor",
                      "cursor is malformed or does not match the current "
                      "sort/order - restart pagination from the first page")


def _like_escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%") \
               .replace("_", "\\_")


@router.get("/photos/search")
async def search_photos(request: Request,
                        project_id: str | None = Query(None,
                                                      max_length=64),
                        tag: str | None = Query(None, min_length=1,
                                                max_length=64),
                        category: str | None = Query(None, max_length=100),
                        issue_status: str | None = Query(
                            None, pattern="^(open|in_progress|resolved|"
                                          "closed|none|linked)$"),
                        captured_from: str | None = Query(
                            None, max_length=64),
                        captured_to: str | None = Query(
                            None, max_length=64),
                        q: str | None = Query(None, min_length=1,
                                              max_length=200),
                        sort: str = Query(
                            "captured_at",
                            pattern="^(captured_at|created_at|filename|"
                                    "byte_size)$"),
                        order: str = Query("desc",
                                           pattern="^(asc|desc)$"),
                        limit: int = Query(DEFAULT_SEARCH_LIMIT, gt=0,
                                          le=MAX_SEARCH_LIMIT),
                        cursor: str | None = Query(None, max_length=512)):
    """Filter + search across ALL of the caller's photos with a STABLE
    keyset pagination.

    Stability (task requirement): the page key is (sort_value,
    photo_id) - a total order on the result set. New rows inserted
    between page pulls can never shift already-returned rows: they
    either sort before the current cursor (already passed - the client
    sees them on the NEXT query) or after (normal forward flow). No
    offset is used, so there is no skip/duplicate window at all.

    Filters compose with AND. `q` matches the original filename, the
    business note or any tag (case-insensitive substring, LIKE
    metacharacters escaped). captured_from/to compare the
    client-claimed captured_at lexically (ISO-8601) - sorting by it is
    allowed, trusting it is not.
    """
    token = require_caller(request)

    where = ["p.owner_token = ?"]
    params: list[Any] = [token]
    if project_id:
        where.append("ph.project_id = ?")
        params.append(project_id)
    if tag:
        where.append("EXISTS (SELECT 1 FROM mvp_photo_tags t "
                     "WHERE t.photo_id = ph.photo_id AND t.tag = ?)")
        params.append(tag)
    if category is not None:
        where.append("COALESCE(b.category, '') = ?")
        params.append(category)
    if issue_status is not None:
        if issue_status == "none":
            where.append("NOT EXISTS (SELECT 1 FROM mvp_issue_photos ip "
                         "JOIN mvp_issues i ON i.issue_id = ip.issue_id "
                         "WHERE ip.photo_id = ph.photo_id)")
        elif issue_status == "linked":
            where.append("EXISTS (SELECT 1 FROM mvp_issue_photos ip "
                         "JOIN mvp_issues i ON i.issue_id = ip.issue_id "
                         "WHERE ip.photo_id = ph.photo_id)")
        else:
            where.append("EXISTS (SELECT 1 FROM mvp_issue_photos ip "
                         "JOIN mvp_issues i ON i.issue_id = ip.issue_id "
                         "WHERE ip.photo_id = ph.photo_id "
                         "AND i.status = ?)")
            params.append(issue_status)
    if captured_from:
        where.append("ph.captured_at >= ?")
        params.append(captured_from)
    if captured_to:
        where.append("ph.captured_at <= ?")
        params.append(captured_to)
    if q:
        term = f"%{_like_escape(q)}%"
        where.append("(ph.original_filename LIKE ? ESCAPE '\\' "
                     "OR COALESCE(b.note, '') LIKE ? ESCAPE '\\' "
                     "OR EXISTS (SELECT 1 FROM mvp_photo_tags t WHERE "
                     "t.photo_id = ph.photo_id AND t.tag LIKE ? "
                     "ESCAPE '\\'))")
        params.extend([term, term, term])

    if cursor:
        key_val, cur_pid = _decode_cursor(cursor, sort, order)
        # row-value comparison keeps the (key, id) pair atomic
        sort_expr = _SORT_EXPR[sort][0]
        where.append(
            f"({sort_expr}, ph.photo_id) "
            f"{'>' if order == 'asc' else '<'} (?, ?)")
        params.extend([key_val, cur_pid])

    where_sql = " AND ".join(where)
    cmp_dir = "ASC" if order == "asc" else "DESC"
    limit = max(1, min(limit, MAX_SEARCH_LIMIT))
    sort_col = _SORT_EXPR[sort][1]

    with db() as conn:
        rows = conn.execute(
            f"SELECT ph.*, b.category AS biz_category, "
            f"b.note AS biz_note, b.revision AS biz_revision "
            f"FROM mvp_photos ph "
            f"JOIN mvp_projects p ON p.project_id = ph.project_id "
            f"LEFT JOIN mvp_photo_business b ON b.photo_id = ph.photo_id "
            f"WHERE {where_sql} "
            f"ORDER BY ph.{sort_col} {cmp_dir}, ph.photo_id LIMIT ?",
            (*params, limit + 1)).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM mvp_photos ph "
            f"JOIN mvp_projects p ON p.project_id = ph.project_id "
            f"LEFT JOIN mvp_photo_business b ON b.photo_id = ph.photo_id "
            f"WHERE {where_sql}", params).fetchone()[0]

        has_more = len(rows) > limit
        rows = rows[:limit]
        items = []
        for r in rows:
            item = row_to_photo(r)
            item["tags"] = _photo_tags(conn, r["photo_id"])
            item["category"] = r["biz_category"] or ""
            item["note"] = r["biz_note"] or ""
            items.append(item)

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = _encode_cursor(sort, order, last[sort_col],
                                     last["photo_id"])
    return {
        "items": items,
        "total": total,
        "total_note": "snapshot count at query time (keyset pages stay "
                      "drift-free; total may change as rows are added)",
        "next_cursor": next_cursor,
        "has_more": has_more,
        "sort": sort, "order": order,
        "pagination_note": "stable keyset pagination on (sort_value, "
                           "photo_id) - inserted rows never shift "
                           "already-returned pages",
    }


# -------------------------------------------------------------- duplicates


@router.get("/photos/duplicates")
async def find_duplicates(request: Request,
                          limit: int = Query(50, gt=0,
                                             le=MAX_SEARCH_LIMIT),
                          cursor: str | None = Query(
                              None, max_length=512)):
    """Content-addressed duplicate detection: photos are grouped by
    their original_sha256 (SHA-256 IS the identity, W007 rule) across
    ALL of the caller's projects. Duplicates are REPORTED, never
    deleted - originals are immutable evidence and deletion is a human
    decision outside this API.

    Group-level keyset pagination keyed on original_sha256."""
    token = require_caller(request)
    limit = max(1, min(limit, MAX_SEARCH_LIMIT))

    with db() as conn:
        groups = conn.execute(
            """
            WITH grouped AS (
                SELECT ph.original_sha256 AS sha, COUNT(*) AS c,
                       MIN(ph.byte_size) AS byte_size
                FROM mvp_photos ph
                JOIN mvp_projects p ON p.project_id = ph.project_id
                WHERE p.owner_token = ?
                GROUP BY ph.original_sha256 HAVING COUNT(*) >= 2
            )
            SELECT g.sha, g.c, g.byte_size
            FROM grouped g
            WHERE (? IS NULL OR g.sha > ?)
            ORDER BY g.sha LIMIT ?
            """,
            (token, cursor, cursor, limit + 1)).fetchall()

        has_more = len(groups) > limit
        groups = groups[:limit]
        items = []
        for g in groups:
            photos = conn.execute(
                "SELECT ph.photo_id, ph.project_id, "
                "ph.original_filename, ph.captured_at, "
                "ph.created_at FROM mvp_photos ph "
                "JOIN mvp_projects p ON p.project_id = ph.project_id "
                "WHERE ph.original_sha256 = ? AND p.owner_token = ? "
                "ORDER BY ph.created_at DESC, ph.photo_id",
                (g["sha"], token)).fetchall()
            items.append({
                "original_sha256": g["sha"],
                "count": g["c"],
                "byte_size": g["byte_size"],
                "photos": [dict(p) for p in photos],
            })

    next_cursor = groups[-1]["sha"] if has_more and groups else None
    return {
        "groups": items,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "note": "duplicates are REPORTED only - originals are immutable "
                "evidence; this API never deletes anything",
    }
