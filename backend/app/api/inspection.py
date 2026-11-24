"""WC-W013 P1/P3/P4 - annotations, issues & comparison API.

/api/v1 surface (owner-scoped, 404-identical cross-subject):

  Annotations (P1):
    POST /photos/{photo_id}/annotations
    GET  /photos/{photo_id}/annotations           (active layer)
    GET  /photos/{photo_id}/annotations?include_inactive=true  (history)
    PATCH /annotations/{annotation_id}           (revision +1)
    DELETE /annotations/{annotation_id}          (soft delete: is_active=0)

  Inspection Issues (P3):
    POST /projects/{project_id}/issues
    GET  /projects/{project_id}/issues           (filter by status/severity)
    GET  /issues/{issue_id}                       (detail + history)
    PATCH /issues/{issue_id}                     (revision + history entry)
    POST /issues/{issue_id}/photos               (link by role)
    DELETE /issues/{issue_id}/photos/{photo_id}/{role}
    GET  /issues/{issue_id}/photos               (comparison view)

  Comparison Derivative (P4):
    POST /issues/{issue_id}/comparison-image     (before/after side-by-side)

Annotation semantics:
- normalized coords (0..1) on the EXIF-transposed display frame
- HUMAN-ADDED content (every API response says so)
- soft delete keeps history; revision increments on every mutation
- z_order controls layer stacking

Issue semantics:
- severity/status are HUMAN records (no AI claims - 'no AI assessment
  implied' is in the response)
- every mutation appends to mvp_issue_history
- optimistic revision on PATCH (revision_conflict on mismatch)
- resolved_at set/cleared automatically on status change
"""

from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.identity import require_caller
from app.db import ApiError, db, utc_now
from app.api.mvp import _get_owned_photo, _get_owned_project, _require_uuid

router = APIRouter(prefix="/api/v1", tags=["mvp-inspection"])

MAX_ANNOTATIONS_PER_PHOTO = 50


# ------------------------------------------------------------- annotations


class AnnotationCreate(BaseModel):
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

    @model_validator(mode="after")
    def _type_consistency(self):
        if self.annotation_type in ("text", "number_marker") \
                and self.annotation_type == "text" \
                and not self.text_content:
            raise ValueError("text annotation requires text_content")
        if self.annotation_type == "number_marker" \
                and self.marker_number is None:
            raise ValueError("number_marker requires marker_number")
        if self.annotation_type in ("arrow", "rectangle", "circle") \
                and self.norm_x2 is None:
            raise ValueError(
                f"{self.annotation_type} requires norm_x2/norm_y2")
        return self


class AnnotationPatch(BaseModel):
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
    expected_revision: int = Field(ge=1)


def _row_to_annotation(row) -> dict:
    return {
        "annotation_id": row["annotation_id"],
        "photo_id": row["photo_id"],
        "annotation_type": row["annotation_type"],
        "norm_x1": row["norm_x1"], "norm_y1": row["norm_y1"],
        "norm_x2": row["norm_x2"], "norm_y2": row["norm_y2"],
        "color_hex": row["color_hex"],
        "stroke_width": row["stroke_width"],
        "text_content": row["text_content"],
        "marker_number": row["marker_number"],
        "z_order": row["z_order"],
        "revision": row["revision"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "note": "human-added annotation content",
    }


def _get_owned_annotation(conn, annotation_id: str, token: str):
    row = conn.execute(
        "SELECT a.* FROM mvp_annotations a "
        "JOIN mvp_photos ph ON ph.photo_id = a.photo_id "
        "JOIN mvp_projects p ON p.project_id = ph.project_id "
        "WHERE a.annotation_id = ?", (annotation_id,)).fetchone()
    if row is None or _owner_of(conn, row["photo_id"]) != token:
        raise ApiError(404, "not_found", "annotation not found")
    return row


def _owner_of(conn, photo_id: str) -> str | None:
    row = conn.execute(
        "SELECT p.owner_token FROM mvp_photos ph "
        "JOIN mvp_projects p ON p.project_id = ph.project_id "
        "WHERE ph.photo_id = ?", (photo_id,)).fetchone()
    return row["owner_token"] if row else None


@router.post("/photos/{photo_id}/annotations", status_code=201)
async def create_annotation(photo_id: str, body: AnnotationCreate,
                            request: Request):
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    now = utc_now()
    with db() as conn:
        _get_owned_photo(conn, photo_id, token)
        count = conn.execute(
            "SELECT COUNT(*) FROM mvp_annotations WHERE photo_id = ? "
            "AND is_active = 1", (photo_id,)).fetchone()[0]
        if count >= MAX_ANNOTATIONS_PER_PHOTO:
            raise ApiError(413, "too_many_annotations",
                           f"photo already has {count} active annotations "
                           f"(cap {MAX_ANNOTATIONS_PER_PHOTO})")
        annotation_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO mvp_annotations (annotation_id, photo_id, "
            "owner_token, annotation_type, norm_x1, norm_y1, norm_x2, "
            "norm_y2, color_hex, stroke_width, text_content, "
            "marker_number, z_order, revision, is_active, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,?,?)",
            (annotation_id, photo_id, token, body.annotation_type,
             body.norm_x1, body.norm_y1, body.norm_x2, body.norm_y2,
             body.color_hex, body.stroke_width, body.text_content,
             body.marker_number, body.z_order, now, now))
        row = conn.execute(
            "SELECT * FROM mvp_annotations WHERE annotation_id = ?",
            (annotation_id,)).fetchone()
    return _row_to_annotation(row)


@router.get("/photos/{photo_id}/annotations")
async def list_annotations(photo_id: str, request: Request,
                            include_inactive: bool = Query(False)):
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    with db() as conn:
        _get_owned_photo(conn, photo_id, token)
        where = "photo_id = ?"
        if not include_inactive:
            where += " AND is_active = 1"
        rows = conn.execute(
            f"SELECT * FROM mvp_annotations WHERE {where} "
            "ORDER BY z_order ASC, annotation_id",
            (photo_id,)).fetchall()
    return {"items": [_row_to_annotation(r) for r in rows],
            "total": len(rows),
            "note": "annotations are human-added content"}


@router.patch("/annotations/{annotation_id}")
async def patch_annotation(annotation_id: str, body: AnnotationPatch,
                            request: Request):
    token = require_caller(request)
    _require_uuid(annotation_id, "annotation_id")
    now = utc_now()
    with db() as conn:
        row = _get_owned_annotation(conn, annotation_id, token)
        if not row["is_active"]:
            raise ApiError(409, "annotation_deleted",
                           "deleted annotations are immutable")
        if row["revision"] != body.expected_revision:
            raise ApiError(409, "revision_conflict",
                           f"expected revision {body.expected_revision}, "
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
            raise ApiError(400, "empty_patch",
                           "at least one field must be provided")
        updates.append("revision = revision + 1")
        updates.append("updated_at = ?")
        params.extend([now, annotation_id])
        conn.execute(
            f"UPDATE mvp_annotations SET {', '.join(updates)} "
            "WHERE annotation_id = ?", params)
        row = conn.execute(
            "SELECT * FROM mvp_annotations WHERE annotation_id = ?",
            (annotation_id,)).fetchone()
    return _row_to_annotation(row)


@router.delete("/annotations/{annotation_id}")
async def delete_annotation(annotation_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(annotation_id, "annotation_id")
    now = utc_now()
    with db() as conn:
        row = _get_owned_annotation(conn, annotation_id, token)
        if row["is_active"]:
            conn.execute(
                "UPDATE mvp_annotations SET is_active = 0, "
                "revision = revision + 1, updated_at = ? "
                "WHERE annotation_id = ?", (now, annotation_id))
    return {"annotation_id": annotation_id, "deleted": True,
            "note": "soft delete - history preserved; the annotation "
                    "stays queryable with include_inactive=true"}


# ------------------------------------------------------------- issues (P3)


_ISSUE_SEVERITIES = {"low", "medium", "high", "critical"}
_ISSUE_STATUSES = {"open", "in_progress", "resolved", "closed"}
_STATUS_TRANSITIONS = {
    "open": {"open", "in_progress"},
    "in_progress": {"in_progress", "resolved", "open"},
    "resolved": {"resolved", "closed", "in_progress"},
    "closed": {"closed"},
}


class IssueCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)
    severity: str = Field(pattern="^(low|medium|high|critical)$")
    location_description: str = Field(default="", max_length=1000)
    assigned_to: str = Field(default="", max_length=128)


class IssuePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    severity: str | None = Field(default=None,
                                  pattern="^(low|medium|high|critical)$")
    status: str | None = Field(default=None,
                               pattern="^(open|in_progress|resolved|closed)$")
    location_description: str | None = Field(default=None,
                                              max_length=1000)
    assigned_to: str | None = Field(default=None, max_length=128)
    expected_revision: int = Field(ge=1)


class IssuePhotoLink(BaseModel):
    photo_id: str = Field(min_length=1, max_length=64)
    role: str = Field(pattern="^(before|progress|after)$")


def _row_to_issue(row) -> dict:
    return {
        "issue_id": row["issue_id"], "project_id": row["project_id"],
        "title": row["title"], "description": row["description"],
        "severity": row["severity"], "status": row["status"],
        "location_description": row["location_description"],
        "assigned_to": row["assigned_to"],
        "revision": row["revision"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "resolved_at": row["resolved_at"],
        "note": "severity/status are HUMAN records; no AI assessment implied",
    }


def _get_owned_issue(conn, issue_id: str, token: str):
    row = conn.execute(
        "SELECT * FROM mvp_issues WHERE issue_id = ?",
        (issue_id,)).fetchone()
    if row is None or row["owner_token"] != token:
        raise ApiError(404, "not_found", "issue not found")
    return row


def _append_history(conn, issue_id, actor_token, action, from_v, to_v,
                    detail=""):
    conn.execute(
        "INSERT INTO mvp_issue_history (issue_id, actor_token, action, "
        "from_value, to_value, detail, created_at) VALUES (?,?,?,?,?,?,?)",
        (issue_id, actor_token, action, from_v, to_v, detail, utc_now()))


@router.post("/projects/{project_id}/issues", status_code=201)
async def create_issue(project_id: str, body: IssueCreate,
                        request: Request):
    token = require_caller(request)
    _require_uuid(project_id, "project_id")
    now = utc_now()
    issue_id = str(uuid.uuid4())
    with db() as conn:
        _get_owned_project(conn, project_id, token)
        conn.execute(
            "INSERT INTO mvp_issues (issue_id, project_id, owner_token, "
            "title, description, severity, status, "
            "location_description, assigned_to, revision, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?,'open',?,?,1,?,?)",
            (issue_id, project_id, token, body.title, body.description,
             body.severity, body.location_description, body.assigned_to,
             now, now))
        _append_history(conn, issue_id, token, "created", None,
                        body.severity, body.title)
        row = conn.execute("SELECT * FROM mvp_issues WHERE issue_id = ?",
                           (issue_id,)).fetchone()
    return _row_to_issue(row)


@router.get("/projects/{project_id}/issues")
async def list_issues(project_id: str, request: Request,
                       status: str | None = Query(None),
                       severity: str | None = Query(None)):
    token = require_caller(request)
    _require_uuid(project_id, "project_id")
    with db() as conn:
        _get_owned_project(conn, project_id, token)
        where = "project_id = ?"
        params: list = [project_id]
        if status:
            where += " AND status = ?"
            params.append(status)
        if severity:
            where += " AND severity = ?"
            params.append(severity)
        rows = conn.execute(
            f"SELECT * FROM mvp_issues WHERE {where} "
            "ORDER BY created_at DESC, issue_id", params).fetchall()
    return {"items": [_row_to_issue(r) for r in rows],
            "total": len(rows)}


@router.get("/issues/{issue_id}")
async def issue_detail(issue_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(issue_id, "issue_id")
    with db() as conn:
        row = _get_owned_issue(conn, issue_id, token)
        history = conn.execute(
            "SELECT id, actor_token, action, from_value, to_value, "
            "detail, created_at FROM mvp_issue_history "
            "WHERE issue_id = ? ORDER BY id ASC", (issue_id,)).fetchall()
        photos = conn.execute(
            "SELECT ip.photo_id, ip.role, ip.added_at, ph.photo_id "
            "FROM mvp_issue_photos ip "
            "JOIN mvp_photos ph ON ph.photo_id = ip.photo_id "
            "WHERE ip.issue_id = ? ORDER BY ip.role",
            (issue_id,)).fetchall()
    out = _row_to_issue(row)
    out["history"] = [dict(h) for h in history]
    out["photos"] = [{"photo_id": p["photo_id"], "role": p["role"],
                      "added_at": p["added_at"]} for p in photos]
    return out


@router.patch("/issues/{issue_id}")
async def patch_issue(issue_id: str, body: IssuePatch, request: Request):
    token = require_caller(request)
    _require_uuid(issue_id, "issue_id")
    now = utc_now()
    with db() as conn:
        row = _get_owned_issue(conn, issue_id, token)
        if row["status"] == "closed":
            raise ApiError(409, "issue_closed",
                           "closed issues are immutable")
        if row["revision"] != body.expected_revision:
            raise ApiError(409, "revision_conflict",
                           f"expected revision {body.expected_revision}, "
                           f"current {row['revision']}",
                           {"current_revision": row["revision"]})
        updates, params = [], []
        for f in ("title", "description", "location_description",
                  "assigned_to"):
            v = getattr(body, f)
            if v is not None:
                updates.append(f"{f} = ?")
                params.append(v)
        if body.severity is not None and body.severity != row["severity"]:
            if row["status"] == "closed":
                raise ApiError(409, "issue_closed",
                               "closed issues are immutable")
            updates.append("severity = ?")
            params.append(body.severity)
            _append_history(conn, issue_id, token, "severity_changed",
                            row["severity"], body.severity)
        if body.status is not None and body.status != row["status"]:
            allowed = _STATUS_TRANSITIONS.get(row["status"], set())
            if body.status not in allowed:
                raise ApiError(409, "invalid_status_transition",
                               f"cannot move from '{row['status']}' to "
                               f"'{body.status}'",
                               {"allowed": sorted(allowed)})
            updates.append("status = ?")
            params.append(body.status)
            _append_history(conn, issue_id, token, "status_changed",
                            row["status"], body.status)
            if body.status == "resolved":
                updates.append("resolved_at = ?")
                params.append(now)
            elif row["status"] == "resolved":
                updates.append("resolved_at = NULL")
        if not updates:
            raise ApiError(400, "empty_patch",
                           "at least one field must change")
        updates.append("revision = revision + 1")
        updates.append("updated_at = ?")
        params.extend([now, issue_id])
        conn.execute(
            f"UPDATE mvp_issues SET {', '.join(updates)} "
            "WHERE issue_id = ?", params)
        row = conn.execute("SELECT * FROM mvp_issues WHERE issue_id = ?",
                           (issue_id,)).fetchone()
    return _row_to_issue(row)


# ------------------------------------------------ comparison links (P4)


@router.post("/issues/{issue_id}/photos", status_code=201)
async def link_issue_photo(issue_id: str, body: IssuePhotoLink,
                            request: Request):
    """Manually link a photo to an issue by role. The comparison is a
    HUMAN association - the API response says so."""
    token = require_caller(request)
    _require_uuid(issue_id, "issue_id")
    now = utc_now()
    with db() as conn:
        issue = _get_owned_issue(conn, issue_id, token)
        photo_owner = _owner_of(conn, body.photo_id)
        if photo_owner != token:
            raise ApiError(404, "not_found",
                           "photo not found for this owner")
        if issue["project_id"] != _photo_project(conn, body.photo_id):
            raise ApiError(400, "photo_project_mismatch",
                           "photo belongs to a different project")
        if issue["status"] == "closed":
            raise ApiError(409, "issue_closed",
                           "closed issues are immutable")
        # one photo per role
        conn.execute(
            "DELETE FROM mvp_issue_photos WHERE issue_id = ? "
            "AND role = ?", (issue_id, body.role))
        conn.execute(
            "INSERT INTO mvp_issue_photos (issue_id, photo_id, role, "
            "added_at) VALUES (?,?,?,?)",
            (issue_id, body.photo_id, body.role, now))
        _append_history(conn, issue_id, token, "photo_linked",
                       None, body.role, body.photo_id)
    return {"issue_id": issue_id, "photo_id": body.photo_id,
            "role": body.role, "added_at": now,
            "note": "manual association - does not prove the photos "
                    "show the same location or that work was accepted"}


def _photo_project(conn, photo_id: str) -> str | None:
    row = conn.execute(
        "SELECT project_id FROM mvp_photos WHERE photo_id = ?",
        (photo_id,)).fetchone()
    return row["project_id"] if row else None


@router.delete("/issues/{issue_id}/photos/{photo_id}/{role}")
async def unlink_issue_photo(issue_id: str, photo_id: str, role: str,
                             request: Request):
    token = require_caller(request)
    _require_uuid(issue_id, "issue_id")
    with db() as conn:
        issue = _get_owned_issue(conn, issue_id, token)
        if issue["status"] == "closed":
            raise ApiError(409, "issue_closed",
                           "closed issues are immutable")
        deleted = conn.execute(
            "DELETE FROM mvp_issue_photos WHERE issue_id = ? "
            "AND photo_id = ? AND role = ?",
            (issue_id, photo_id, role)).rowcount
        if not deleted:
            raise ApiError(404, "not_found", "link not found")
        _append_history(conn, issue_id, token, "photo_unlinked",
                        role, None, photo_id)
    return {"issue_id": issue_id, "photo_id": photo_id,
            "role": role, "unlinked": True}


@router.get("/issues/{issue_id}/photos")
async def issue_photos(issue_id: str, request: Request):
    token = require_caller(request)
    _require_uuid(issue_id, "issue_id")
    with db() as conn:
        _get_owned_issue(conn, issue_id, token)
        rows = conn.execute(
            "SELECT ip.photo_id, ip.role, ip.added_at "
            "FROM mvp_issue_photos ip WHERE ip.issue_id = ? "
            "ORDER BY ip.role", (issue_id,)).fetchall()
    return {"items": [dict(r) for r in rows],
            "total": len(rows),
            "note": "before/after comparison is a MANUAL association; "
                    "it does not prove the same location, nor does it "
                    "constitute acceptance of completed work"}


# --------------------------------------------- comparison image (P4)


class ComparisonRequest(BaseModel):
    before_photo_id: str = Field(min_length=1)
    after_photo_id: str = Field(min_length=1)


@router.post("/issues/{issue_id}/comparison-image")
async def comparison_image(issue_id: str, body: ComparisonRequest,
                            request: Request):
    """Generate a before/after side-by-side derivative. Reads the two
    originals (never modifies them), draws each with its label, and
    returns the composed JPEG."""
    token = require_caller(request)
    _require_uuid(issue_id, "issue_id")
    with db() as conn:
        issue = _get_owned_issue(conn, issue_id, token)
        for pid in (body.before_photo_id, body.after_photo_id):
            if _owner_of(conn, pid) != token:
                raise ApiError(404, "not_found", f"photo {pid} not found")
            if _photo_project(conn, pid) != issue["project_id"]:
                raise ApiError(400, "photo_project_mismatch",
                               f"photo {pid} belongs to a different project")
        b_sha = _sha_of(conn, body.before_photo_id)
        a_sha = _sha_of(conn, body.after_photo_id)

    from app.services.original_store import read_original
    try:
        before_bytes = read_original(b_sha)
        after_bytes = read_original(a_sha)
    except Exception:
        raise ApiError(404, "original_not_uploaded",
                       "both originals must be uploaded first")

    from PIL import Image as _PILImage
    try:
        result = _render_comparison(before_bytes, after_bytes,
                                    body.before_photo_id,
                                    body.after_photo_id)
    except _PILImage.DecompressionBombError as e:
        # PIL's bomb guard inherits Exception directly (not OSError /
        # ValueError) - caught explicitly so an oversized-pixel bomb is
        # a typed 400 too, never an unhandled 500.
        raise ApiError(400, "image_too_large",
                       f"an original exceeds the decompression-bomb "
                       f"guard: {e}")
    except (OSError, ValueError) as e:
        # B02-W021 fix (ISSUE-W021-01): PIL raises OSError (truncated /
        # unidentifiable bytes, decompression-bomb escalation) or
        # ValueError for bytes that are not a decodable image. The
        # originals stay intact in storage; the request is a typed 400,
        # never an unhandled 500.
        raise ApiError(400, "invalid_image",
                       f"an original is not a decodable image: {e}")
    return Response(
        content=result["image_bytes"],
        media_type="image/jpeg",
        headers={
            "X-Comparison-Sha256": result["derivative_sha256"],
            "X-Before-Sha256": b_sha, "X-After-Sha256": a_sha,
            "X-Comparison-Note":
                "manual before/after association rendered as a "
                "derivative; originals unchanged",
            "Content-Disposition":
                f'attachment; filename="comparison-{issue_id[:12]}.jpg"'})


def _sha_of(conn, photo_id: str) -> str:
    row = conn.execute(
        "SELECT original_sha256 FROM mvp_photos WHERE photo_id = ?",
        (photo_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "photo not found")
    return row["original_sha256"]


def _render_comparison(before: bytes, after: bytes,
                       before_id: str, after_id: str) -> dict:
    import os
    import tempfile
    from PIL import Image, ImageDraw, ImageFont

    def _load(data):
        from app.services.watermark_render import _apply_exif_orientation
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img, _ = _apply_exif_orientation(img)
        return img

    b_img = _load(before)
    a_img = _load(after)

    # normalize to half-width of a 1600-wide canvas
    half_w = 780
    def _fit(img):
        ratio = half_w / img.width
        return img.resize((half_w, int(img.height * ratio)),
                          Image.LANCZOS)
    b_img = _fit(b_img)
    a_img = _fit(a_img)
    half_h = max(b_img.height, a_img.height)

    canvas = Image.new("RGB", (half_w * 2 + 40, half_h + 60),
                       (245, 245, 245))
    canvas.paste(b_img, (20, 50))
    canvas.paste(a_img, (half_w + 20, 50))

    draw = ImageDraw.Draw(canvas)
    draw.font = ImageFont.load_default()
    draw.text((20, 15), f"BEFORE - {before_id}", fill=(20, 20, 20))
    draw.text((half_w + 20, 15), f"AFTER - {after_id}",
              fill=(20, 20, 20))
    draw.text((20, half_h + 40),
              "Manual comparison - same location and quality acceptance "
              "are NOT proven by this image",
              fill=(120, 120, 120))
    draw.line([(half_w + 10, 0), (half_w + 10, canvas.height)],
              fill=(200, 200, 200), width=2)

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=88)
    data = buf.getvalue()
    return {"image_bytes": data,
            "derivative_sha256": hashlib.sha256(data).hexdigest()}
