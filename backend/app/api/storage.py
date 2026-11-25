"""B02-W025 - storage operations API surface.

Endpoints (all under /api/v1, all behind the W009 identity layer):

  PUT  /photos/{photo_id}/original-stream   streaming large-object
                                             ingest (peak memory =
                                             one chunk; the RISK-
                                             W021-R1 answer)
  GET  /photos/{photo_id}/thumbnail         get-or-generate thumbnail
                                             (?edge=256|512|1024)
  GET  /storage/stats                       space accounting (global
                                             physical / per-project
                                             logical; ?project_id=)
  GET  /storage/dedup                      refcount dedup report
                                             (owner-scoped)
  GET  /storage/audit                       FS<->DB consistency audit
                                             in both directions
  POST /storage/cache/prune                 regenerable-cache pruning
                                             (never originals)
  GET  /storage/quota                       quota model evaluation
                                             (?project_id=, required)
  PUT  /storage/quota                        upsert quota (model only)

Red lines:
  - the streaming path is byte-identical in semantics to the W007
    legacy upload (same error codes, same atomic commit)
  - the quota surface is a MODEL: nothing here rejects an upload
  - prune can only ever walk thumbnails/ and derivatives/ roots
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.api.identity import require_caller
from app.api.mvp import _get_owned_photo, _require_uuid
from app.db import ApiError, db, utc_now
from app.services import storage_ops

router = APIRouter(prefix="/api/v1", tags=["b02-w025-storage"])


# --------------------------------------------------- streaming upload

@router.put("/photos/{photo_id}/original-stream")
async def upload_original_stream(photo_id: str, request: Request):
    """Store the original bytes WITHOUT buffering the whole body in
    memory (the legacy `await request.body()` path must hold the full
    file; this endpoint hashes and writes chunk-by-chunk, so a 500 MB
    photo costs ~256 KB of heap instead of 500 MB).

    Semantics identical to the legacy PUT: claimed hash from the
    photo's registered original_sha256, mismatch -> 400 + nothing
    persisted, idempotent replay on identical bytes, never overwrites
    an existing object."""
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")

    with db() as conn:
        photo = _get_owned_photo(conn, photo_id, token)
        claimed = photo["original_sha256"]
        existing = conn.execute(
            "SELECT sha256 FROM mvp_photo_originals WHERE photo_id = ?",
            (photo_id,)).fetchone()
        if existing is not None and existing["sha256"] != claimed:
            raise ApiError(409, "binding_conflict",
                           "photo is bound to a different original object")

    async def _chunks():
        async for chunk in request.stream():
            yield chunk

    result = await storage_ops.store_streaming_async(_chunks(), claimed)

    with db() as conn:
        _get_owned_photo(conn, photo_id, token)      # re-check under tx
        conn.execute(
            "INSERT INTO mvp_photo_originals (photo_id, sha256, "
            "owner_token, bound_at) VALUES (?,?,?,?) "
            "ON CONFLICT(photo_id, sha256) DO NOTHING",
            (photo_id, claimed, token, utc_now()))
    return {"photo_id": photo_id, "sha256": claimed,
            "byte_size": result["byte_size"],
            "stored": True,
            "idempotent_replay": result["idempotent_replay"],
            "streamed": True,
            "note": "streamed ingest: file integrity verified while "
                    "streaming; photo authenticity is NOT claimed by "
                    "storage"}


# ------------------------------------------------------ thumbnail

@router.get("/photos/{photo_id}/thumbnail")
async def get_thumbnail(photo_id: str, request: Request,
                        edge: int = storage_ops.DEFAULT_THUMBNAIL_EDGE):
    """Get-or-generate this photo's thumbnail. Content-addressed by the
    ORIGINAL's hash: photos sharing the same original share one cache
    file (free dedup). The original is opened read-only."""
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    with db() as conn:
        photo = _get_owned_photo(conn, photo_id, token)
        sha = photo["original_sha256"]
        bound = conn.execute(
            "SELECT sha256 FROM mvp_photo_originals WHERE photo_id = ?",
            (photo_id,)).fetchone()
    if bound is None:
        raise ApiError(404, "original_not_uploaded",
                       "upload the original before requesting a "
                       "thumbnail")
    info = storage_ops.ensure_thumbnail(sha, edge)
    data = Path(info["path"]).read_bytes()
    return Response(
        content=data, media_type="image/jpeg",
        headers={
            "X-Thumbnail-Of": sha,
            "X-Thumbnail-Edge": str(edge),
            "X-Cache-Hit": str(info["idempotent_replay"]),
            "X-Thumbnail-Note": "regenerable cache image - not the "
                                "trusted original evidence",
            "Content-Disposition":
                f'attachment; filename="thumb-{sha[:16]}-{edge}.jpg"'})


# ------------------------------------------------------ stats / dedup

@router.get("/storage/stats")
async def storage_stats_api(request: Request,
                            project_id: str | None = None):
    token = require_caller(request)
    return storage_ops.storage_stats(project_id, owner_token=token)


@router.get("/storage/dedup")
async def storage_dedup_api(request: Request,
                            project_id: str | None = None):
    token = require_caller(request)
    return storage_ops.dedup_report(owner_token=token,
                                    project_id=project_id)


@router.get("/storage/audit")
async def storage_audit_api(request: Request,
                            fix_temps: bool = False):
    require_caller(request)
    return storage_ops.audit_storage(fix_temps=fix_temps)


# ------------------------------------------------------ cache prune

class PruneRequest(BaseModel):
    scope: str = Field(default="all",
                       description="thumbnails | derivatives | all")
    dry_run: bool = False


@router.post("/storage/cache/prune")
async def cache_prune_api(request: Request, body: PruneRequest):
    require_caller(request)
    return storage_ops.prune_cache(scope=body.scope,
                                   dry_run=body.dry_run)


# ------------------------------------------------------ quota model

class QuotaUpsert(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    quota_bytes: int = Field(ge=0)
    policy: str = Field(default="report_only")


@router.put("/storage/quota")
async def quota_upsert_api(request: Request, body: QuotaUpsert):
    token = require_caller(request)
    return storage_ops.set_quota(
        project_id=body.project_id, quota_bytes=body.quota_bytes,
        policy=body.policy, updated_by=token, owner_token=token)


@router.get("/storage/quota")
async def quota_check_api(request: Request, project_id: str):
    token = require_caller(request)
    return storage_ops.check_quota(project_id, owner_token=token)
