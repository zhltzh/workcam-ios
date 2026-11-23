"""WC-W006 P0/P1/P2 - /api/v1 business endpoints.

Projects, photos, evidence links. Every route requires an enrolled
device token (identity.py); resources are owner-scoped; cross-subject
access is 404-identical to not-found.

Frozen protocol boundary: this module does NOT touch /verify, the
signature payload, or the time-anchor protocol. Evidence verification
status transitions occur ONLY from a real server-side verification
result (see _resolve_evidence_status); clients can never POST a trusted
status directly.
"""

from __future__ import annotations

import re
import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from app.api.identity import require_caller
from app.db import (
    ApiError, db, migrate, row_to_evidence_link, row_to_photo,
    row_to_project, utc_now, backup_to, verify_backup, restore_from,
)

router = APIRouter(prefix="/api/v1", tags=["mvp-v1"])

_NAME_RE = re.compile(r"\S")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MIME_RE = re.compile(r"^image/[a-z0-9.+-]+$")

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


# --------------------------------------------------------------- validation


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    site_address: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not _NAME_RE.search(v):
            raise ValueError("name must contain non-whitespace characters")
        return v.strip()


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    site_address: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _NAME_RE.search(v):
            raise ValueError("name must contain non-whitespace characters")
        return v.strip()


class PhotoRegister(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    original_sha256: str
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: str
    byte_size: int = Field(gt=0, le=100 * 1024 * 1024)
    captured_at: str = Field(min_length=1, max_length=64)
    device_id: str = Field(min_length=1, max_length=128)

    @field_validator("original_sha256")
    @classmethod
    def sha_lowercase_hex(cls, v: str) -> str:
        if not _SHA256_RE.match(v):
            raise ValueError("original_sha256 must be 64 lowercase hex chars")
        return v

    @field_validator("mime_type")
    @classmethod
    def image_mime(cls, v: str) -> str:
        if not _MIME_RE.match(v):
            raise ValueError("mime_type must look like an image/* type")
        return v


class EvidenceLinkCreate(BaseModel):
    evidence_reference: str = Field(min_length=1, max_length=200)
    evidence_type: Literal["signature_manifest", "time_anchor"]

    # NOTE: no verification_status field here ON PURPOSE - clients
    # cannot submit trusted statuses (P2 requirement #2).


def _require_uuid(value: str, field_name: str) -> str:
    try:
        uuid.UUID(value, version=4)
    except (ValueError, AttributeError):
        raise ApiError(400, "invalid_uuid",
                       f"{field_name} must be a valid UUIDv4")
    return value


def _get_owned_project(conn, project_id: str, token: str,
                       for_write_photo: bool = False) -> dict:
    """Fetch a project ENFORCING ownership; cross-owner reads are 404
    (identical response to a nonexistent id - no existence oracle)."""
    row = conn.execute(
        "SELECT * FROM mvp_projects WHERE project_id = ?",
        (project_id,)).fetchone()
    if row is None or row["owner_token"] != token:
        raise ApiError(404, "not_found", "project not found")
    if for_write_photo and row["status"] == "archived":
        raise ApiError(409, "project_archived",
                       "cannot add photos to an archived project")
    return row_to_project(row)


# ------------------------------------------------------------ startup hook


def ensure_ready() -> None:
    """Run migrations once at app startup (idempotent)."""
    migrate()


# ----------------------------------------------------------------- P0: projects


@router.post("/projects", status_code=201)
async def create_project(body: ProjectCreate, request: Request):
    token = require_caller(request)
    project_id = str(uuid.uuid4())
    now = utc_now()
    with db() as conn:
        conn.execute(
            "INSERT INTO mvp_projects (project_id, owner_token, name, "
            "description, site_address, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,'active',?,?)",
            (project_id, token, body.name, body.description,
             body.site_address, now, now))
    return {"project_id": project_id, "name": body.name,
            "description": body.description,
            "site_address": body.site_address,
            "status": "active", "created_at": now, "updated_at": now,
            "archived_at": None}


def _page_params(limit: int, offset: int) -> tuple[int, int]:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    return limit, offset


@router.get("/projects")
async def list_projects(request: Request,
                        limit: int = Query(DEFAULT_PAGE_SIZE, gt=0),
                        offset: int = Query(0, ge=0),
                        status: str = Query("active",
                                             pattern="^(active|archived|all)$")):
    token = require_caller(request)
    limit, offset = _page_params(limit, offset)
    # stable ordering: created_at DESC, project_id tiebreaker
    with db() as conn:
        where = "owner_token = ?"
        params: list = [token]
        if status != "all":
            where += " AND status = ?"
            params.append(status)
        rows = conn.execute(
            f"SELECT * FROM mvp_projects WHERE {where} "
            "ORDER BY created_at DESC, project_id LIMIT ? OFFSET ?",
            (*params, limit, offset)).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM mvp_projects WHERE {where}",
            params).fetchone()[0]
    return {"items": [row_to_project(r) for r in rows],
            "total": total, "limit": limit, "offset": offset}


@router.get("/projects/{project_id}")
async def get_project(project_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(project_id, "project_id")
    with db() as conn:
        proj = _get_owned_project(conn, project_id, token)
    return proj


@router.patch("/projects/{project_id}")
async def patch_project(project_id: str, body: ProjectPatch,
                        request: Request):
    token = require_caller(request)
    _require_uuid(project_id, "project_id")
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM mvp_projects WHERE project_id = ?",
            (project_id,)).fetchone()
        if row is None or row["owner_token"] != token:
            raise ApiError(404, "not_found", "project not found")
        if row["status"] == "archived":
            raise ApiError(409, "project_archived",
                           "archived projects are immutable")
        updates, params = [], []
        for field in ("name", "description", "site_address"):
            value = getattr(body, field)
            if value is not None:
                updates.append(f"{field} = ?")
                params.append(value)
        if not updates:
            raise ApiError(400, "empty_patch",
                           "at least one field must be provided")
        updates.append("updated_at = ?")
        params.append(utc_now())
        params.append(project_id)
        conn.execute(
            f"UPDATE mvp_projects SET {', '.join(updates)} "
            "WHERE project_id = ?", params)
        row = conn.execute(
            "SELECT * FROM mvp_projects WHERE project_id = ?",
            (project_id,)).fetchone()
    return row_to_project(row)


@router.post("/projects/{project_id}/archive")
async def archive_project(project_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(project_id, "project_id")
    now = utc_now()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM mvp_projects WHERE project_id = ?",
            (project_id,)).fetchone()
        if row is None or row["owner_token"] != token:
            raise ApiError(404, "not_found", "project not found")
        if row["status"] == "archived":
            # IDEMPOTENT archive: re-archiving returns the same state, 200
            return row_to_project(row)
        conn.execute(
            "UPDATE mvp_projects SET status='archived', archived_at=?, "
            "updated_at=? WHERE project_id=?", (now, now, project_id))
        row = conn.execute(
            "SELECT * FROM mvp_projects WHERE project_id = ?",
            (project_id,)).fetchone()
    return row_to_project(row)


# ----------------------------------------------------------------- P1: photos


@router.post("/projects/{project_id}/photos", status_code=201)
async def register_photo(project_id: str, body: PhotoRegister,
                         request: Request):
    token = require_caller(request)
    _require_uuid(project_id, "project_id")
    photo_id = str(uuid.uuid4())
    now = utc_now()
    with db() as conn:
        _get_owned_project(conn, project_id, token, for_write_photo=True)
        existing = conn.execute(
            "SELECT * FROM mvp_photos WHERE project_id = ? "
            "AND idempotency_key = ?",
            (project_id, body.idempotency_key)).fetchone()
        if existing is not None:
            # idempotent replay: SAME request content returns the SAME
            # photo (200 semantics); DIFFERENT content is a conflict.
            if (existing["original_sha256"] == body.original_sha256
                    and existing["original_filename"] == body.original_filename
                    and existing["byte_size"] == body.byte_size):
                return {**row_to_photo(existing), "_idempotent_replay": True}
            raise ApiError(409, "idempotency_conflict",
                           "idempotency_key already used with different "
                           "content in this project",
                           {"existing_photo_id": existing["photo_id"]})
        conn.execute(
            "INSERT INTO mvp_photos (photo_id, project_id, idempotency_key, "
            "original_sha256, original_filename, mime_type, byte_size, "
            "captured_at, received_at, device_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (photo_id, project_id, body.idempotency_key,
             body.original_sha256, body.original_filename, body.mime_type,
             body.byte_size, body.captured_at, now, body.device_id, now))
        row = conn.execute(
            "SELECT * FROM mvp_photos WHERE photo_id = ?",
            (photo_id,)).fetchone()
    return row_to_photo(row)


@router.get("/projects/{project_id}/photos")
async def list_photos(project_id: str, request: Request,
                      limit: int = Query(DEFAULT_PAGE_SIZE, gt=0),
                      offset: int = Query(0, ge=0)):
    token = require_caller(request)
    _require_uuid(project_id, "project_id")
    limit, offset = _page_params(limit, offset)
    with db() as conn:
        _get_owned_project(conn, project_id, token)
        rows = conn.execute(
            "SELECT * FROM mvp_photos WHERE project_id = ? "
            "ORDER BY created_at DESC, photo_id LIMIT ? OFFSET ?",
            (project_id, limit, offset)).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM mvp_photos WHERE project_id = ?",
            (project_id,)).fetchone()[0]
    return {"items": [row_to_photo(r) for r in rows],
            "total": total, "limit": limit, "offset": offset}


def _get_owned_photo(conn, photo_id: str, token: str) -> dict:
    """Ownership chains THROUGH the project: a photo belongs to its
    project's owner. Cross-owner reads are 404 (no existence oracle)."""
    row = conn.execute(
        "SELECT p.* FROM mvp_photos ph "
        "JOIN mvp_projects p ON p.project_id = ph.project_id "
        "WHERE ph.photo_id = ?", (photo_id,)).fetchone()
    if row is None or row["owner_token"] != token:
        raise ApiError(404, "not_found", "photo not found")
    photo = conn.execute(
        "SELECT * FROM mvp_photos WHERE photo_id = ?",
        (photo_id,)).fetchone()
    return row_to_photo(photo)


@router.get("/photos/{photo_id}")
async def get_photo(photo_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    with db() as conn:
        return _get_owned_photo(conn, photo_id, token)


# ------------------------------------------------------- P2: evidence links


def _resolve_evidence_status(conn, photo_sha256: str,
                              evidence_reference: str,
                              evidence_type: str) -> tuple[str, str | None]:
    """Server-side-only status resolution (P2 #2/#3): call the EXISTING
    trusted systems and translate their real result. Clients never
    supply a status.

    W006 MVP boundary: business links start 'unverified' - a trusted
    status can ONLY be written after the server itself has run the real
    verification (/verify for signature manifests, the anchor service
    for time anchors). Until then the reason field keeps a traceable
    'awaiting...' note. Status transitions happen exclusively through
    server-side verification paths; there is no API that accepts a
    trusted status from a client.
    """
    if evidence_type == "signature_manifest":
        return "unverified", (
            f"awaiting server-side /verify result for record "
            f"{evidence_reference}")
    return "unverified", (
        f"awaiting time-anchor verification for {evidence_reference}")


# ------------------------------------------------ P2-followup: server verify


class SignatureManifestVerifyBody(BaseModel):
    """Verification MATERIAL for a signature_manifest evidence link.
    The client submits the manifest (and optionally the photo bytes);
    the STATUS is computed server-side and written back - the request
    schema has no status field by design."""
    manifest: dict
    photo_base64: str | None = None


class TimeAnchorVerifyBody(BaseModel):
    receipt: dict


def _load_link_owned(conn, photo_id: str, link_id: str,
                     token: str) -> tuple[dict, dict]:
    """Fetch photo + link enforcing ownership through the photo's
    project (cross-subject -> 404, identical to not-found)."""
    photo = _get_owned_photo(conn, photo_id, token)
    row = conn.execute(
        "SELECT * FROM mvp_evidence_links WHERE evidence_link_id = ?",
        (link_id,)).fetchone()
    if row is None or row["photo_id"] != photo_id:
        raise ApiError(404, "not_found", "evidence link not found")
    return photo, row_to_evidence_link(row)


def _write_back_status(conn, link_id: str, status: str,
                       reason: str | None, verified_at: str | None) -> dict:
    """The ONLY writer of non-unverified statuses in the business DB -
    always the result of a real server-side verification run."""
    conn.execute(
        "UPDATE mvp_evidence_links SET verification_status = ?, "
        "failure_reason = ?, verified_at = ? WHERE evidence_link_id = ?",
        (status, reason, verified_at, link_id))
    row = conn.execute(
        "SELECT * FROM mvp_evidence_links WHERE evidence_link_id = ?",
        (link_id,)).fetchone()
    return row_to_evidence_link(row)


@router.post("/photos/{photo_id}/evidence-links/{evidence_link_id}/verify")
async def verify_evidence_link(photo_id: str, evidence_link_id: str,
                               request: Request,
                               body: dict):
    """Server-side verification of a registered evidence link.

    The client submits the verification MATERIAL (manifest / anchor
    receipt); the server runs the EXISTING trusted verification paths
    (W002 signature service, W003 anchor service - unmodified) and
    writes the resulting status back. Clients still cannot submit a
    status: the computed value is the only thing that lands in the DB.

    Traceability + binding guards (fail fast, 400 - no state change):
      - signature_manifest: manifest.record_id MUST equal the link's
        evidence_reference, and manifest.photo_hash MUST equal the
        photo's original_sha256 (anti cross-photo misbinding)
      - time_anchor: receipt.batch_id MUST equal the evidence_reference
    """
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    now = utc_now()

    with db() as conn:
        photo, link = _load_link_owned(conn, photo_id, evidence_link_id,
                                        token)

        if link["evidence_type"] == "signature_manifest":
            if "manifest" not in body:
                raise ApiError(400, "missing_material",
                               "signature_manifest links require "
                               "{manifest: {...}}")
            manifest_dict = body["manifest"]
            record_id = manifest_dict.get("record_id")
            if record_id != link["evidence_reference"]:
                raise ApiError(400, "reference_mismatch",
                               f"manifest.record_id {record_id!r} does not "
                               f"match the registered evidence_reference "
                               f"{link['evidence_reference']!r}")
            photo_hash = manifest_dict.get("photo_hash")
            if photo_hash != photo["original_sha256"]:
                raise ApiError(400, "hash_binding_mismatch",
                               "manifest.photo_hash does not match the "
                               "photo's registered original_sha256 - "
                               "refusing to bind evidence to the wrong "
                               "photo")

            from app.models.capture import SignatureManifest
            try:
                manifest = SignatureManifest.model_validate(manifest_dict)
            except Exception as e:
                return _write_back_status(
                    conn, evidence_link_id, "verification_failed",
                    f"manifest failed schema validation: {e}", now)

            from app.services.signature import (
                validate_manifest_structure, verify_signature_with_details,
                verify_photo_hash as check_photo_hash)
            import base64

            struct_errors = validate_manifest_structure(manifest)
            if struct_errors:
                return _write_back_status(
                    conn, evidence_link_id, "verification_failed",
                    "structure validation failed: "
                    + "; ".join(struct_errors[:3]), now)

            if body.get("photo_base64"):
                try:
                    photo_bytes = base64.b64decode(body["photo_base64"],
                                                   validate=True)
                except Exception as e:
                    return _write_back_status(
                        conn, evidence_link_id, "verification_failed",
                        f"photo_base64 undecodable: {e}", now)
                if not check_photo_hash(manifest, photo_bytes):
                    return _write_back_status(
                        conn, evidence_link_id, "verification_failed",
                        "photo bytes do not match the signed photo_hash",
                        now)

            sig_ok, sig_msg = verify_signature_with_details(manifest)
            if not sig_ok:
                return _write_back_status(
                    conn, evidence_link_id, "verification_failed",
                    f"signature invalid: {sig_msg}", now)

            # trust level mapped from the EXISTING status machine:
            # registered key -> cryptographically_valid; unregistered
            # -> signature_valid (same semantics as /verify's
            # evidence_status, never inflated)
            from app.services.key_registry import (
                get_registry, KeyRegistrationStatus)
            reg = get_registry().lookup(manifest.public_key)
            if reg.registration_status == KeyRegistrationStatus.REGISTERED:
                status, reason = "cryptographically_valid", None
            else:
                status = "signature_valid"
                reason = ("signature math valid; public key not in the "
                          "registry - device identity not established")
            return _write_back_status(conn, evidence_link_id, status,
                                      reason, now)

        # ---------------- time_anchor ----------------
        if "receipt" not in body:
            raise ApiError(400, "missing_material",
                           "time_anchor links require {receipt: {...}}")
        receipt_dict = body["receipt"]
        if receipt_dict.get("batch_id") != link["evidence_reference"]:
            raise ApiError(400, "reference_mismatch",
                           f"receipt.batch_id {receipt_dict.get('batch_id')!r}"
                           f" does not match the registered "
                           f"evidence_reference "
                           f"{link['evidence_reference']!r}")

        from app.models.time_anchor import TimeAnchorReceipt
        from app.services.time_anchor import get_time_anchor_service
        try:
            receipt = TimeAnchorReceipt.model_validate(receipt_dict)
        except Exception as e:
            return _write_back_status(
                conn, evidence_link_id, "verification_failed",
                f"receipt failed schema validation: {e}", now)

        service = get_time_anchor_service()
        result = service.verify_anchor(receipt, check_storage=True)
        if result.valid:
            return _write_back_status(
                conn, evidence_link_id, "server_anchored", None, now)
        return _write_back_status(
            conn, evidence_link_id, "verification_failed",
            f"anchor verification failed: {result.message}", now)


@router.post("/photos/{photo_id}/evidence-links", status_code=201)
async def create_evidence_link(photo_id: str, body: EvidenceLinkCreate,
                               request: Request):
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    link_id = str(uuid.uuid4())
    now = utc_now()
    with db() as conn:
        photo = _get_owned_photo(conn, photo_id, token)
        # bind the photo's CURRENT immutable hash at link time (P2 #4)
        status, reason = _resolve_evidence_status(
            conn, photo["original_sha256"],
            body.evidence_reference, body.evidence_type)
        conn.execute(
            "INSERT INTO mvp_evidence_links (evidence_link_id, photo_id, "
            "evidence_reference, evidence_type, original_sha256, "
            "verification_status, failure_reason, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (link_id, photo_id, body.evidence_reference,
             body.evidence_type, photo["original_sha256"], status,
             reason if status == "verification_failed" else reason, now))
        row = conn.execute(
            "SELECT * FROM mvp_evidence_links WHERE evidence_link_id = ?",
            (link_id,)).fetchone()
    return row_to_evidence_link(row)


@router.get("/photos/{photo_id}/evidence-links")
async def list_evidence_links(photo_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    with db() as conn:
        _get_owned_photo(conn, photo_id, token)
        rows = conn.execute(
            "SELECT * FROM mvp_evidence_links WHERE photo_id = ? "
            "ORDER BY created_at DESC, evidence_link_id",
            (photo_id,)).fetchall()
    return {"items": [row_to_evidence_link(r) for r in rows],
            "total": len(rows)}
