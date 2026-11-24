"""WC-W012 P2/P4 - report generation & delivery package API.

/api/v1 surface (owner-scoped, 404-identical cross-subject):
  POST /api/v1/projects/{project_id}/reports            generate (snapshot)
  GET  /api/v1/projects/{project_id}/reports            list
  GET  /api/v1/reports/{report_id}                      detail (snapshot view)
  GET  /api/v1/reports/{report_id}/download             PDF bytes
  POST /api/v1/reports/{report_id}/delivery-package     ZIP package

Snapshot immutability: regeneration = a NEW report row; existing rows
are never mutated. The PDF bytes live in the content-addressed store.

Delivery package (P4): report.pdf + originals/ + derivatives/ +
manifest.json + verification-guide.txt, optional include flags, file
sha-256 manifest for offline integrity checks. NEVER exports tokens,
enrollment codes, or key material.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
import zipfile

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.api.identity import require_caller
from app.db import ApiError, db, utc_now
from app.api.mvp import _get_owned_project, _require_uuid

router = APIRouter(prefix="/api/v1", tags=["mvp-reports"])

MAX_REPORT_PHOTOS = 500
_MAX_ZIP_ITEMS = 2000
# P5 resource bound: photos larger than this (longest edge) are
# downscaled for EMBEDDING ONLY (originals stay untouched in the
# content-addressed store; their full SHA-256 is recorded in the
# appendix). Keeps 500-photo batches within a bounded memory envelope.
_EMBED_MAX_EDGE = 1800


def _load_photo_image(orig_sha: str) -> tuple[bytes | None, int, int]:
    """Load the original for embedding; downscale if beyond the embed
    bound. Returns (jpeg_bytes_for_report, width, height)."""
    from app.services.original_store import read_original
    try:
        data = read_original(orig_sha)
    except Exception:
        return None, 0, 0
    try:
        import io
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(data)) as im:
            w, h = im.size
            longest = max(w, h)
            if longest <= _EMBED_MAX_EDGE:
                return data, w, h
            scale = _EMBED_MAX_EDGE / longest
            nw, nh = int(w * scale), int(h * scale)
            resized = im.convert("RGB").resize((nw, nh),
                                              PILImage.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, format="JPEG", quality=88)
            return buf.getvalue(), nw, nh
    except Exception:
        return None, 0, 0


class ReportCreateRequest(BaseModel):
    report_type: str = Field(pattern="^(site_photo|inspection|evidence)$")
    page_size: str = Field(default="a4", pattern="^(a4|letter)$")
    per_page: int = Field(default=2, ge=1, le=4)
    photo_ids: list[str] = Field(min_length=1,
                                 max_length=MAX_REPORT_PHOTOS)
    operator: str = Field(default="", max_length=128)
    company_name: str = Field(default="", max_length=200)
    company_logo_b64: str | None = None
    include_evidence_appendix: bool | None = None

    @field_validator("per_page")
    @classmethod
    def _per_page_grid(cls, v: int) -> int:
        if v not in (1, 2, 4):
            raise ValueError(
                f"per_page must be 1, 2 or 4 (grid layouts); got {v}")
        return v


class DeliveryPackageRequest(BaseModel):
    include_originals: bool = True
    include_derivatives: bool = True
    include_evidence: bool = True


def _report_store() -> "Path":
    from pathlib import Path
    import app.db as dbm
    return dbm._db_path().parent / "reports"


def _report_path(report_id: str) -> "Path":
    p = _report_store() / f"{report_id}.pdf"
    return p


def _require_report_owned(conn, report_id: str, token: str):
    row = conn.execute(
        "SELECT * FROM mvp_reports WHERE report_id = ?",
        (report_id,)).fetchone()
    if row is None or row["owner_token"] != token:
        raise ApiError(404, "not_found", "report not found")
    return row


# --------------------------------------------------------------- generate


def _snapshot_photos(conn, token: str, photo_ids: list[str],
                     project_id: str) -> list:
    """Collect per-photo state AT GENERATION TIME: photo metadata,
    derivative (if any), and the SERVER-RESOLVED evidence status (the
    same resolver as the watermark engine)."""
    from app.api.derivatives import _resolve_actual_evidence_status

    ordered = photo_ids
    seen = set()
    for pid in ordered:
        if pid in seen:
            raise ApiError(400, "duplicate_photo_id",
                           f"photo {pid} appears more than once")
        seen.add(pid)

    entries = []
    for pid in ordered:
        row = conn.execute(
            "SELECT ph.*, p.owner_token FROM mvp_photos ph "
            "JOIN mvp_projects p ON p.project_id = ph.project_id "
            "WHERE ph.photo_id = ?", (pid,)).fetchone()
        if row is None or row["owner_token"] != token:
            raise ApiError(404, "not_found",
                           f"photo {pid} not found in this project")
        if row["project_id"] != project_id:
            raise ApiError(400, "photo_project_mismatch",
                           f"photo {pid} belongs to a different project")

        orig_sha = row["original_sha256"]
        ev_status = _resolve_actual_evidence_status(conn, pid, orig_sha)
        ev_ref_row = conn.execute(
            "SELECT evidence_reference, verified_at "
            "FROM mvp_evidence_links WHERE photo_id = ? "
            "ORDER BY created_at DESC LIMIT 1", (pid,)).fetchone()

        d_row = conn.execute(
            "SELECT derivative_sha256 FROM mvp_derivatives "
            "WHERE photo_id = ? ORDER BY created_at DESC LIMIT 1",
            (pid,)).fetchone()

        entries.append({
            "photo_id": pid,
            "original_sha256": orig_sha,
            "derivative_sha256": d_row["derivative_sha256"]
                if d_row else None,
            "image_width": None, "image_height": None,
            "captured_time": row["captured_at"],
            "time_source": "device",     # captured_at is a device claim
            "gps_latitude": None, "gps_longitude": None,
            "gps_source": None, "gps_accuracy": None,
            "notes": None,
            "evidence_status": ev_status,
            "evidence_reference": ev_ref_row["evidence_reference"]
                if ev_ref_row else None,
            "evidence_verified_at": ev_ref_row["verified_at"]
                if ev_ref_row else None,
        })
    return entries


def _load_photo_image_unused(orig_sha: str) -> bytes | None:
    """Deprecated - kept only for reference; the real loader above
    returns downscaled bytes + dimensions."""
    from app.services.original_store import read_original
    try:
        return read_original(orig_sha)
    except Exception:
        return None


@router.post("/projects/{project_id}/reports", status_code=201)
async def create_report(project_id: str, body: ReportCreateRequest,
                        request: Request):
    from app.services.report_pdf import (
        PhotoEntry, ReportMeta, ReportType, PageSize, generate_report_pdf)

    token = require_caller(request)
    _require_uuid(project_id, "project_id")

    logo_bytes = None
    if body.company_logo_b64:
        import base64
        try:
            logo_bytes = base64.b64decode(body.company_logo_b64,
                                          validate=True)
        except Exception:
            raise ApiError(400, "invalid_logo",
                           "company_logo_b64 is not valid base64")

    report_id = str(uuid.uuid4())
    now = utc_now()
    report_number = (
        f"WC-{datetime_prefix()}-{report_id[:8].upper()}")

    # ---- snapshot under one transaction -------------------------------
    with db() as conn:
        project = _get_owned_project(conn, project_id, token)
        snapshot = _snapshot_photos(conn, token, body.photo_ids,
                                   project_id)

    # ---- build photo entries with embedded images ---------------------
    # Images are loaded ONE AT A TIME and downscaled to the embed bound
    # (P5: no 500-photo OOM); original bytes stay immutable in storage.
    photos: list[PhotoEntry] = []
    for s in snapshot:
        image_bytes, w, h = _load_photo_image(s["original_sha256"])
        s["image_width"], s["image_height"] = w, h
        photos.append(PhotoEntry(**s, image_bytes=image_bytes))

    meta = ReportMeta(
        report_id=report_id, report_number=report_number,
        project_name=project["name"],
        site_address=project.get("site_address") or "",
        generated_at=now,
        operator=body.operator, company_name=body.company_name,
        logo_bytes=logo_bytes,
        include_evidence_appendix=body.include_evidence_appendix)

    try:
        result = generate_report_pdf(
            ReportType(body.report_type), meta, photos,
            PageSize(body.page_size), body.per_page,
            body.include_evidence_appendix)
    except Exception as e:
        raise ApiError(500, "report_generation_failed", str(e)[:200])

    pdf_bytes = result["pdf_bytes"]
    store = _report_store()
    store.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(store), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(pdf_bytes)
        os.replace(tmp, _report_path(report_id))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    with db() as conn:
        _get_owned_project(conn, project_id, token)
        conn.execute(
            "INSERT INTO mvp_reports (report_id, project_id, "
            "owner_token, report_number, report_type, "
            "template_version, page_size, per_page, photo_ids_json, "
            "snapshot_json, pdf_sha256, page_count, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'complete', ?)",
            (report_id, project_id, token, report_number,
             body.report_type, result["version"], body.page_size,
             body.per_page, json.dumps(body.photo_ids),
             json.dumps(snapshot), result["sha256"],
             result["page_count"], now))

    # release embedded images (bytes referenced by the built flowables
    # are already consumed by doc.build; drop locals explicitly for
    # large batches)
    del photos, snapshot

    return {
        "report_id": report_id, "report_number": report_number,
        "report_type": body.report_type,
        "template_version": result["version"],
        "page_size": body.page_size, "per_page": body.per_page,
        "photo_count": len(body.photo_ids),
        "pdf_sha256": result["sha256"],
        "page_count": result["page_count"],
        "created_at": now,
        "note": "report content is a SNAPSHOT at generation time; "
                "regeneration creates a new version"}


def datetime_prefix() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%d")


# ------------------------------------------------------------- list/detail


def _row_to_report(row) -> dict:
    return {
        "report_id": row["report_id"],
        "project_id": row["project_id"],
        "report_number": row["report_number"],
        "report_type": row["report_type"],
        "template_version": row["template_version"],
        "page_size": row["page_size"], "per_page": row["per_page"],
        "pdf_sha256": row["pdf_sha256"], "page_count": row["page_count"],
        "status": row["status"], "created_at": row["created_at"],
    }


@router.get("/projects/{project_id}/reports")
async def list_reports(project_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(project_id, "project_id")
    with db() as conn:
        _get_owned_project(conn, project_id, token)
        rows = conn.execute(
            "SELECT * FROM mvp_reports WHERE project_id = ? "
            "ORDER BY created_at DESC, report_id",
            (project_id,)).fetchall()
    return {"items": [_row_to_report(r) for r in rows],
            "total": len(rows)}


@router.get("/reports/{report_id}")
async def report_detail(report_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(report_id, "report_id")
    with db() as conn:
        row = _require_report_owned(conn, report_id, token)
    out = _row_to_report(row)
    out["photo_ids"] = json.loads(row["photo_ids_json"])
    out["snapshot"] = {
        s["photo_id"]: {k: v for k, v in s.items()
                        if k not in ("image_bytes",)}
        for s in json.loads(row["snapshot_json"])}
    return out


@router.get("/reports/{report_id}/download")
async def download_report(report_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(report_id, "report_id")
    with db() as conn:
        row = _require_report_owned(conn, report_id, token)
    path = _report_path(report_id)
    if not path.exists():
        raise ApiError(404, "object_missing", "report PDF missing")
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == row["pdf_sha256"]
    return Response(
        content=data, media_type="application/pdf",
        headers={"X-Report-Sha256": row["pdf_sha256"],
                 "Content-Disposition":
                 f'attachment; filename="{row["report_number"]}.pdf"'})


# ------------------------------------------------- P4: delivery package


@router.post("/reports/{report_id}/delivery-package")
async def create_delivery_package(report_id: str,
                                  body: DeliveryPackageRequest,
                                  request: Request):
    """ZIP delivery package: report.pdf + originals/ + derivatives/ +
    manifest.json + verification-guide.txt. Options control inclusion.
    Sensitive material (tokens, enrollment codes, keys) is NEVER
    exported. Returns the ZIP inline (bytes) with a manifest header."""
    token = require_caller(request)
    _require_uuid(report_id, "report_id")
    with db() as conn:
        row = _require_report_owned(conn, report_id, token)
        snapshot = json.loads(row["snapshot_json"])

    store = _report_store()
    pdf_path = _report_path(report_id)
    if not pdf_path.exists():
        raise ApiError(404, "object_missing", "report PDF missing")

    import io
    buf = io.BytesIO()
    manifest: dict = {
        "report_id": report_id,
        "report_number": row["report_number"],
        "report_type": row["report_type"],
        "generated_at": row["created_at"],
        "files": [],
        "includes": {"originals": body.include_originals,
                      "derivatives": body.include_derivatives,
                      "evidence": body.include_evidence},
    }

    def add_bytes(zf, arcname, data: bytes):
        if len(manifest["files"]) >= _MAX_ZIP_ITEMS:
            raise ApiError(413, "package_too_large",
                           f"delivery package exceeds "
                           f"{_MAX_ZIP_ITEMS} files")
        zf.writestr(arcname, data)
        manifest["files"].append({
            "path": arcname,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data)})

    from app.services.original_store import read_original, object_path

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        # report first
        add_bytes(zf, "report.pdf", pdf_path.read_bytes())

        for s in snapshot:
            pid = s["photo_id"]
            if body.include_originals:
                try:
                    add_bytes(zf, f"originals/{pid}.jpg",
                              read_original(s["original_sha256"]))
                except Exception:
                    manifest.setdefault("missing", []).append(
                        f"originals/{pid}.jpg")
            if body.include_derivatives and s.get("derivative_sha256"):
                dpath = object_path(s["derivative_sha256"]) \
                    if hasattr(object_path, "__call__") else None
                from pathlib import Path as _P
                import app.db as dbm
                dstore = dbm._db_path().parent / "derivatives"
                cand = dstore / s["derivative_sha256"][:2] / \
                    f"{s['derivative_sha256']}.jpg"
                if cand.exists():
                    add_bytes(zf, f"derivatives/{pid}.jpg",
                              cand.read_bytes())
            if body.include_evidence and s.get("evidence_reference"):
                ev = {k: s.get(k) for k in (
                    "photo_id", "original_sha256", "evidence_status",
                    "evidence_reference", "evidence_verified_at")}
                add_bytes(zf, f"evidence/{pid}.json",
                          json.dumps(ev, indent=2).encode())

        add_bytes(zf, "manifest.json",
                  json.dumps(manifest, indent=2, ensure_ascii=False)
                  .encode())
        guide = (
            "WORKCAM DELIVERY PACKAGE - VERIFICATION GUIDE\n"
            "============================================\n\n"
            "1. manifest.json lists every file with its SHA-256.\n"
            "2. Recompute any file's SHA-256 and compare to detect "
            "corruption or tampering.\n"
            "3. originals/ holds the immutable original photos. A "
            "watermarked image (derivatives/) is NOT the trusted "
            "original.\n"
            "4. Evidence status in evidence/*.json is the SERVER's "
            "verification state at report generation time. "
            "'Cryptographically verified' means the signature verified; "
            "it does NOT prove the capture scene was authentic.\n"
            "5. This package contains NO private keys, tokens, or "
            "enrollment codes.\n"
            "6. This package is a work-product delivery, NOT a legal "
            "certification.\n")
        add_bytes(zf, "verification-guide.txt", guide.encode())

    zip_bytes = buf.getvalue()
    zip_sha = hashlib.sha256(zip_bytes).hexdigest()
    return Response(
        content=zip_bytes, media_type="application/zip",
        headers={
            "X-Package-Sha256": zip_sha,
            "X-File-Count": str(len(manifest["files"])),
            "Content-Disposition":
            f'attachment; filename="{row["report_number"]}-package.zip"'})
