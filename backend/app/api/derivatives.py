"""WC-W011 P3 - watermark derivative management API.

/api/v1/photo-derivatives surface, reusing the W006 identity layer and
W007 storage discipline:
  POST /api/v1/photos/{photo_id}/derivatives/watermark
       -> render + persist + register (idempotent on same request)
  GET  /api/v1/photos/{photo_id}/derivatives
       -> list this photo's derivatives (owner-scoped)
  GET  /api/v1/derivatives/{derivative_id}/download
       -> authorized download of the derivative image bytes

Immutability: rendering reads the ORIGINAL bytes; the derivative is
written to its own content-addressed store. The original file is never
opened for writing. A derivative is never the trusted evidence - the
API responses say so.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.api.identity import require_caller
from app.db import ApiError, db, utc_now
from app.api.mvp import _get_owned_photo, _require_uuid
from app.services.watermark_render import (
    WatermarkFields, render_watermark, get_template, TemplateName)

router = APIRouter(prefix="/api/v1", tags=["mvp-derivatives"])


class WatermarkRequest(BaseModel):
    template: str = Field(min_length=1, max_length=32)
    fields: WatermarkFields
    company_logo_b64: str | None = None       # optional logo (base64)


def derivative_root() -> Path:
    import app.db as dbm
    return dbm._db_path().parent / "derivatives"


# P0 SECURITY (W012): server-side evidence-state resolution for
# watermarks. The RENDERED status is always the server's own truth.
_STATUS_RANK = {
    "no_evidence": 0, "unverified": 1, "signature_valid": 2,
    "verification_failed": 2, "cryptographically_valid": 3,
    "server_anchored": 4,
}


def _resolve_actual_evidence_status(conn, photo_id: str,
                                   original_sha256: str) -> str:
    """Derive the photo's REAL evidence state from the evidence links
    the server itself verified. Also enforces hash binding: a link
    whose original_sha256 does not match this photo is EXCLUDED from
    consideration (anti cross-photo misbinding). No links at all ->
    'no_evidence'."""
    rows = conn.execute(
        "SELECT original_sha256, verification_status "
        "FROM mvp_evidence_links WHERE photo_id = ?",
        (photo_id,)).fetchall()
    best = "no_evidence"
    best_rank = 0
    for row in rows:
        if row["original_sha256"] != original_sha256:
            continue                       # bound to a different photo
        rank = _STATUS_RANK.get(row["verification_status"], 0)
        if rank > best_rank:
            best = row["verification_status"]
            best_rank = rank
    return best


def derivative_path(sha256: str) -> Path:
    if not sha256 or len(sha256) != 64 or any(
            c not in "0123456789abcdef" for c in sha256):
        raise ApiError(500, "invalid_hash", "corrupt derivative hash")
    p = derivative_root() / sha256[:2] / f"{sha256}.jpg"
    return p


# ----------------------------------------------------------------- create


def render_watermark_for_photo(token: str, photo_id: str, template: str,
                               fields: WatermarkFields) -> dict:
    """Shared render+persist path for the W011 single-photo endpoint and
    the B02-W017 batch watermark engine. Ownership, server-side evidence
    override, idempotency probe, rendering and content-addressed persist
    all live HERE so both entry points cannot drift apart.

    Raises ApiError (not_found / original_not_uploaded / ...) - callers
    decide whether that is an HTTP error or a per-photo failure item.
    Returns the derivative row dict + idempotent_replay + note + geometry.
    """
    original_sha = None
    with db() as conn:
        photo = _get_owned_photo(conn, photo_id, token)
        # ---- P0 SECURITY FIX (W012): the evidence status on a
        # derivative MUST come from the SERVER's own evidence-link
        # state, never from the client's claim. A closed enum prevents
        # free-text forgery but NOT a false claim of the highest
        # status - so we resolve the photo's ACTUAL highest verified
        # state from mvp_evidence_links and override the submitted
        # field outright.
        fields.evidence_status = _resolve_actual_evidence_status(
            conn, photo_id, photo["original_sha256"])
        original_sha = photo["original_sha256"]

    binding_row = None
    with db() as conn:
        binding_row = conn.execute(
            "SELECT sha256 FROM mvp_photo_originals WHERE photo_id = ?",
            (photo_id,)).fetchone()
    if binding_row is None:
        raise ApiError(404, "original_not_uploaded",
                       "upload the original first (metadata-then-bytes "
                       "rule): a watermark renders the REAL bytes")

    from app.services.original_store import read_original
    original_bytes = read_original(original_sha)

    params_hash = hashlib.sha256(json.dumps(
        {"template": template,
         "fields_hash": fields.render_params_hash()}
    ).encode()).hexdigest()

    # idempotency probe BEFORE rendering
    with db() as conn:
        existing = conn.execute(
            "SELECT * FROM mvp_derivatives WHERE photo_id = ? "
            "AND template_name = ? AND render_params_hash = ? "
            "AND original_sha256 = ?",
            (photo_id, template, params_hash, original_sha)).fetchone()
    if existing is not None and derivative_path(
            existing["derivative_sha256"]).exists():
        return _row_to_derivative(existing) | {
            "idempotent_replay": True,
            "note": "derivative is a DERIVATIVE image, not the trusted "
                    "original evidence"}

    result = render_watermark(original_bytes, fields, template)
    d_sha = result["derivative_sha256"]

    # integrity guard: rendered output must hash to what we store
    assert hashlib.sha256(result["image_bytes"]).hexdigest() == d_sha

    # persist the derivative bytes (W007 discipline: tmp -> rename)
    target = derivative_path(d_sha)
    if not target.exists():        # same-bytes duplicates share the object
        import os
        import tempfile
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                                   prefix=".w011_", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(result["image_bytes"])
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    derivative_id = hashlib.sha256(
        f"{photo_id}|{template}|{params_hash}|{original_sha}"
        .encode()).hexdigest()[:36]
    geo = result["geometry"]
    with db() as conn:
        _get_owned_photo(conn, photo_id, token)     # re-check under tx
        conn.execute(
            "INSERT INTO mvp_derivatives (derivative_id, photo_id, "
            "owner_token, template_name, template_version, "
            "render_params_hash, original_sha256, derivative_sha256, "
            "image_width, image_height, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(derivative_id) DO NOTHING",
            (derivative_id, photo_id, token, template,
             geo["template_version"], params_hash, original_sha, d_sha,
             geo["image_width"], geo["image_height"], utc_now()))
        row = conn.execute(
            "SELECT * FROM mvp_derivatives WHERE derivative_id = ?",
            (derivative_id,)).fetchone()
    return _row_to_derivative(row) | {
        "idempotent_replay": False,
        "note": "derivative is a DERIVATIVE image, not the trusted "
                "original evidence",
        "geometry": geo}


@router.post("/photos/{photo_id}/derivatives/watermark", status_code=201)
async def create_watermark_derivative(photo_id: str, body: WatermarkRequest,
                                      request: Request):
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    try:
        get_template(body.template)
    except ValueError as e:
        raise ApiError(400, "unknown_template", str(e))

    fields = body.fields
    if fields.show_company_logo and body.company_logo_b64:
        import base64
        try:
            fields.company_logo = base64.b64decode(body.company_logo_b64,
                                                   validate=True)
        except Exception:
            raise ApiError(400, "invalid_logo",
                           "company_logo_b64 is not valid base64")

    return render_watermark_for_photo(token, photo_id, body.template,
                                      fields)


# -------------------------------------------------------------- list / get


def _row_to_derivative(row) -> dict:
    return {
        "derivative_id": row["derivative_id"],
        "photo_id": row["photo_id"],
        "template_name": row["template_name"],
        "template_version": row["template_version"],
        "original_sha256": row["original_sha256"],
        "derivative_sha256": row["derivative_sha256"],
        "image_width": row["image_width"],
        "image_height": row["image_height"],
        "created_at": row["created_at"],
    }


@router.get("/photos/{photo_id}/derivatives")
async def list_derivatives(photo_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    with db() as conn:
        _get_owned_photo(conn, photo_id, token)
        rows = conn.execute(
            "SELECT * FROM mvp_derivatives WHERE photo_id = ? "
            "ORDER BY created_at DESC, derivative_id",
            (photo_id,)).fetchall()
    return {"items": [_row_to_derivative(r) for r in rows],
            "total": len(rows),
            "note": "derivatives are RENDERED copies - the trusted "
                    "evidence is the immutable original + its evidence "
                    "links"}


@router.get("/derivatives/{derivative_id}/download")
async def download_derivative(derivative_id: str, request: Request):
    token = require_caller(request)
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM mvp_derivatives WHERE derivative_id = ?",
            (derivative_id,)).fetchone()
        if row is None or row["owner_token"] != token:
            raise ApiError(404, "not_found", "derivative not found")
    path = derivative_path(row["derivative_sha256"])
    if not path.exists():
        raise ApiError(404, "object_missing",
                       "derivative bytes missing from storage")
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == row["derivative_sha256"]
    return Response(
        content=data, media_type="image/jpeg",
        headers={
            "X-Derivative-Sha256": row["derivative_sha256"],
            "X-Original-Sha256": row["original_sha256"],
            "X-Derivative-Note":
                "derivative image - not the trusted original evidence",
            "Content-Disposition":
                f'attachment; filename="watermark-{derivative_id[:12]}.jpg"'})
