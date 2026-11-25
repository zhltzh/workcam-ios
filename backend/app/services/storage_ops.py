"""B02-W025 - engineering-photo storage space management layer.

Built ON TOP of the W007 immutable object store (same content-addressed
layout, same atomic-write discipline, same error vocabulary). This
module ADDS the storage-operations surface the task brief requires:

  - streaming large-object ingest (chunked hash+write, peak memory =
    ONE chunk, not the whole file) - the RISK-W021-R1 fix the legacy
    `await request.body()` path cannot offer
  - thumbnail generation (regenerable cache, content-addressed by the
    ORIGINAL's hash -> same-original photos share one thumbnail file)
  - derivative-cache management (W011 derivatives are byte-regenerable;
    pruning bytes is safe because the W011 idempotency probe requires
    the file to EXIST before replaying - a pruned derivative re-renders
    on the next identical request)
  - storage-space statistics (per-project LOGICAL accounting vs GLOBAL
    PHYSICAL accounting; shared objects are the whole point of dedup)
  - cache pruning (thumbnails + derivative bytes ONLY - originals are
    never touched BY CONSTRUCTION: this code never walks originals/)
  - FS<->DB consistency audit in BOTH directions (disk-without-DB and
    DB-without-disk), including orphan derivative/thumbnail detection
  - refcount dedup reporting (object-identity view; complements - does
    NOT duplicate - the W017 GET /photos/duplicates photo view)
  - per-project quota MODEL (report-only; hard enforcement and any
    original-deletion policy are PRODUCT decisions - Not Implemented)

Hard red lines enforced here:
  - no original byte is ever re-encoded, overwritten or deleted
  - cache pruning walks ONLY the two regenerable roots
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from app.db import ApiError, db, utc_now
from app.services import original_store
from app.services.watermark_render import MAX_RENDER_PIXELS

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 256 * 1024

# Regenerable cache classes. The original store is NOT a cache and is
# never listed here - prune_cache() is structurally incapable of
# touching originals/ because it only walks these roots.
CACHE_SCOPES = ("thumbnails", "derivatives", "all")

THUMBNAIL_EDGES = (256, 512, 1024)
DEFAULT_THUMBNAIL_EDGE = 512
_THUMB_JPEG_QUALITY = 80


# --------------------------------------------------------------- roots

def thumb_root() -> Path:
    import app.db as dbm
    return dbm._db_path().parent / "thumbnails"


def thumbnail_path(sha256: str, edge: int) -> Path:
    """Cache-object path: thumbnails/ab/<sha>_<edge>.jpg - content
    addressed by the ORIGINAL's hash, so every photo sharing that
    original shares ONE thumbnail file (physical dedup for free)."""
    if not _SHA256_RE.match(sha256):
        raise ApiError(400, "invalid_hash",
                       "sha256 must be 64 lowercase hex characters")
    if edge not in THUMBNAIL_EDGES:
        raise ApiError(400, "invalid_edge",
                       f"edge must be one of {THUMBNAIL_EDGES}")
    return thumb_root() / sha256[:2] / f"{sha256}_{edge}.jpg"


def derivative_root() -> Path:
    from app.api.derivatives import derivative_root as _root
    return _root()


# --------------------------------------------------- streaming ingest

class _StreamingObjectWriter:
    """Core chunked ingest writer shared by the sync and async store
    paths. Peak memory is ONE chunk - the whole file is never held."""

    def __init__(self, claimed_sha256: str):
        if not _SHA256_RE.match(claimed_sha256):
            raise ApiError(400, "invalid_hash",
                           "sha256 must be 64 lowercase hex characters")
        self.claimed = claimed_sha256
        self.target = original_store.object_path(claimed_sha256)
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.hasher = hashlib.sha256()
        self.byte_size = 0
        fd, self.tmp = tempfile.mkstemp(
            dir=str(self.target.parent), prefix=".w025_", suffix=".part")
        self._f = os.fdopen(fd, "wb")

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._f.write(chunk)
        self.hasher.update(chunk)
        self.byte_size += len(chunk)

    def finish(self) -> dict:
        """Close, compare, atomic-commit (or discard). NEVER overwrites
        an existing object - immutability identical to W007."""
        self._f.close()
        try:
            if self.byte_size == 0:
                raise ApiError(
                    400, "empty_body",
                    "photo bytes required (streamed body was empty)")
            actual = self.hasher.hexdigest()
            if actual != self.claimed:
                raise ApiError(
                    400, "hash_mismatch",
                    f"streamed content hashes to {actual} but the "
                    f"claimed sha256 is {self.claimed} - object "
                    f"rejected, nothing persisted")
            if self.target.exists():
                disk_hash = original_store._hash_file(self.target)
                if disk_hash == self.claimed:
                    original_store._touch_verified(self.claimed)
                    self._discard_tmp()      # replay leaves no residue
                    return {"sha256": self.claimed,
                            "byte_size": self.target.stat().st_size,
                            "status": "stored",
                            "idempotent_replay": True,
                            "streamed": True}
                with db() as conn:
                    original_store._mark_original_status(
                        conn, self.claimed, "corruption_detected")
                raise ApiError(
                    409, "object_corrupted",
                    "stored object exists but its content does not "
                    "match its hash; manual recovery required",
                    {"sha256": self.claimed})
            # os.replace is atomic on the same volume; a crash before
            # it leaves ONLY a temp file (audit cleans those)
            os.replace(self.tmp, self.target)
            self._committed = True
        except Exception:
            self.abort()
            raise
        now = utc_now()
        with db() as conn:
            conn.execute(
                "INSERT INTO mvp_originals (sha256, byte_size, status, "
                "stored_at, verified_at) VALUES (?,?, 'stored', ?, ?) "
                "ON CONFLICT(sha256) DO NOTHING",
                (self.claimed, self.byte_size, now, now))
        return {"sha256": self.claimed, "byte_size": self.byte_size,
                "status": "stored", "idempotent_replay": False,
                "streamed": True}

    def _discard_tmp(self) -> None:
        try:
            os.unlink(self.tmp)
        except OSError:
            pass

    def abort(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass
        if not getattr(self, "_committed", False):
            self._discard_tmp()


def store_streaming(source, claimed_sha256: str) -> dict:
    """Stream one original object from a SYNC iterable of chunks."""
    w = _StreamingObjectWriter(claimed_sha256)
    try:
        for chunk in source:
            w.write(chunk)
    except Exception:
        w.abort()
        raise
    return w.finish()


async def store_streaming_async(async_source, claimed_sha256: str) -> dict:
    """Async twin: the HTTP layer feeds the raw request stream chunk
    by chunk; nothing but the current chunk is ever in memory."""
    w = _StreamingObjectWriter(claimed_sha256)
    try:
        async for chunk in async_source:
            w.write(chunk)
    except Exception:
        w.abort()
        raise
    return w.finish()


# ------------------------------------------------------ thumbnails

def ensure_thumbnail(sha256: str, edge: int = DEFAULT_THUMBNAIL_EDGE,
                     ) -> dict:
    """Get-or-generate the thumbnail for one original object.

    Cache identity = (original sha256, edge): regenerable on demand,
    physically shared by every photo that references the original.
    The original file is OPENED READ-ONLY and is never written, re-
    encoded or moved (W007 red line)."""
    if edge not in THUMBNAIL_EDGES:
        raise ApiError(400, "invalid_edge",
                       f"edge must be one of {THUMBNAIL_EDGES}")
    out = thumbnail_path(sha256, edge)
    if out.exists():
        return {"path": str(out), "edge": edge,
                "byte_size": out.stat().st_size,
                "idempotent_replay": True}

    src = original_store.object_path(sha256)
    if not src.exists():
        raise ApiError(404, "object_missing",
                       "original object not found in storage")

    from PIL import Image, ImageOps
    with Image.open(src) as img:
        # bomb guard BEFORE full decode (same ceiling as W011)
        if img.width * img.height > MAX_RENDER_PIXELS:
            raise ApiError(413, "image_too_large",
                           f"image is {img.width}x{img.height} "
                           f"(> {MAX_RENDER_PIXELS} pixel decode limit)")
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((edge, edge))
        if img.mode != "RGB":
            img = img.convert("RGB")
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(out.parent),
                                   prefix=".w025_", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                img.save(f, format="JPEG",
                         quality=_THUMB_JPEG_QUALITY)
            os.replace(tmp, out)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return {"path": str(out), "edge": edge,
            "byte_size": out.stat().st_size,
            "idempotent_replay": False}


# ------------------------------------------------------ statistics

def _tree_bytes(root: Path) -> tuple[int, int]:
    """Committed-object byte/file count of a store root. Crash-residue
    .part temps are EXCLUDED here (they are transient by design and the
    audit reports them separately)."""
    total = 0
    files = 0
    if not root.exists():
        return 0, 0
    for p in root.rglob("*"):
        if p.is_file() and not p.name.endswith(".part"):
            total += p.stat().st_size
            files += 1
    return total, files


def _db_bytes() -> int:
    p = Path(__import__("app.db", fromlist=["_db_path"])._db_path())
    total = p.stat().st_size if p.exists() else 0
    for suffix in ("-wal", "-shm"):
        w = p.with_name(p.name + suffix)
        if w.exists():
            total += w.stat().st_size
    return total


def storage_stats(project_id: str | None = None,
                  owner_token: str | None = None) -> dict:
    """Space accounting in the two readings the task brief asks for:

    GLOBAL (project_id=None): PHYSICAL bytes - what is actually on
    disk per class (originals / thumbnails / derivatives / db), plus
    the dedup saving and the logical (sum-per-photo) view for context.

    PER-PROJECT: LOGICAL bytes - every object referenced by the
    project's photos counts toward the project, even when the bytes
    are physically shared with another project. Shared objects are the
    entire point of dedup; physical bytes only make sense globally."""
    if project_id is not None:
        with db() as conn:
            proj = conn.execute(
                "SELECT * FROM mvp_projects WHERE project_id = ?",
                (project_id,)).fetchone()
            if proj is None or (owner_token is not None
                                and proj["owner_token"] != owner_token):
                raise ApiError(404, "not_found", "project not found")
            shas = [r["original_sha256"] for r in conn.execute(
                "SELECT DISTINCT original_sha256 FROM mvp_photos "
                "WHERE project_id = ?", (project_id,)).fetchall()]
            originals_logical = conn.execute(
                "SELECT COALESCE(SUM(o.byte_size),0) s FROM "
                "mvp_originals o WHERE o.sha256 IN "
                "(SELECT DISTINCT original_sha256 FROM mvp_photos "
                " WHERE project_id = ?)", (project_id,)).fetchone()["s"]
            photos = conn.execute(
                "SELECT COUNT(*) c FROM mvp_photos WHERE project_id = ?",
                (project_id,)).fetchone()["c"]
            d_shas = [r["derivative_sha256"] for r in conn.execute(
                "SELECT DISTINCT d.derivative_sha256 FROM mvp_derivatives d "
                "JOIN mvp_photos p ON p.photo_id = d.photo_id "
                "WHERE p.project_id = ?", (project_id,)).fetchall()]
            d_rows = conn.execute(
                "SELECT COUNT(*) c FROM mvp_derivatives d "
                "JOIN mvp_photos p ON p.photo_id = d.photo_id "
                "WHERE p.project_id = ?", (project_id,)).fetchone()["c"]
        thumb_bytes = 0
        thumb_files = 0
        if shas:
            for sha in shas:
                for edge in THUMBNAIL_EDGES:
                    t = thumbnail_path(sha, edge)
                    if t.exists():
                        thumb_bytes += t.stat().st_size
                        thumb_files += 1
        from app.api.derivatives import derivative_path
        deriv_bytes = 0
        deriv_files = 0
        for dsha in d_shas:
            p = derivative_path(dsha)
            if p.exists():
                deriv_bytes += p.stat().st_size
                deriv_files += 1
        return {
            "scope": "project",
            "project_id": project_id,
            "photos": photos,
            "originals": {
                "logical_bytes": originals_logical,
                "distinct_objects": len(shas),
            },
            "thumbnails": {"bytes": thumb_bytes, "files": thumb_files},
            "derivatives": {"registered": d_rows,
                             "bytes": deriv_bytes, "files": deriv_files},
            "accounting_note": "logical accounting: shared objects "
                               "count toward EVERY project that "
                               "references them; physical bytes are "
                               "reported in the global view only",
        }

    # ---------------- global (physical) ----------------
    with db() as conn:
        o_rows = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(byte_size),0) s "
            "FROM mvp_originals").fetchone()
        referenced = conn.execute(
            "SELECT COUNT(DISTINCT sha256) c FROM "
            "mvp_photo_originals").fetchone()["c"]
        photos = conn.execute(
            "SELECT COUNT(*) c FROM mvp_photos").fetchone()["c"]
        deriv_rows = conn.execute(
            "SELECT COUNT(*) c FROM mvp_derivatives").fetchone()["c"]
        orphan_db = conn.execute(
            "SELECT COUNT(*) c FROM mvp_originals WHERE sha256 NOT IN "
            "(SELECT sha256 FROM mvp_photo_originals)").fetchone()["c"]
        logical_total = conn.execute(
            "SELECT COALESCE(SUM(o.byte_size),0) s FROM "
            "mvp_photo_originals po JOIN mvp_originals o "
            "ON o.sha256 = po.sha256").fetchone()["s"]
        saved = 0
        for r in conn.execute(
                "SELECT (COUNT(*) - 1) * MIN(o.byte_size) s "
                "FROM mvp_photo_originals po JOIN mvp_originals o "
                "ON o.sha256 = po.sha256 GROUP BY po.sha256 "
                "HAVING COUNT(*) >= 2"):
            saved += r["s"]
    orig_phys_bytes, orig_phys_files = _tree_bytes(
        original_store.store_root())
    thumb_bytes, thumb_files = _tree_bytes(thumb_root())
    deriv_phys_bytes, deriv_phys_files = _tree_bytes(derivative_root())
    return {
        "scope": "global",
        "photos": photos,
        "originals": {
            "physical_bytes": orig_phys_bytes,
            "physical_files": orig_phys_files,
            "ledger_objects": o_rows["c"],
            "ledger_bytes": o_rows["s"],
            "referenced_objects": referenced,
            "unreferenced_objects": orphan_db,
            "logical_bytes": logical_total,
            "dedup_saved_bytes": saved,
        },
        "thumbnails": {"physical_bytes": thumb_bytes,
                       "physical_files": thumb_files},
        "derivatives": {"physical_bytes": deriv_phys_bytes,
                        "physical_files": deriv_phys_files,
                        "registered_rows": deriv_rows},
        "database_bytes": _db_bytes(),
        "total_physical_bytes": (orig_phys_bytes + thumb_bytes
                                 + deriv_phys_bytes + _db_bytes()),
        "accounting_note": "physical bytes are what the disk actually "
                           "holds; dedup_saved_bytes is what naive "
                           "per-photo storage WOULD have cost minus "
                           "what is actually stored",
    }


# ------------------------------------------------------ cache pruning

def prune_cache(scope: str = "all", dry_run: bool = False,
                owner_token: str | None = None) -> dict:
    """Delete REGENERABLE cache bytes only: thumbnails and/or W011
    derivative image bytes. NEVER originals - not by policy flag but
    by construction: this function only walks the two cache roots.

    Derivative bytes are safe to prune because the W011 idempotency
    probe requires the file to EXIST before replaying an existing row;
    once pruned, the next identical request re-renders and restores
    the exact same content-addressed bytes. The mvp_derivatives ROW is
    kept (registered provenance), only the bytes are reclaimed."""
    if scope not in CACHE_SCOPES:
        raise ApiError(
            400, "invalid_scope",
            f"scope must be one of {CACHE_SCOPES}; originals are "
            f"immutable evidence and are NEVER prunable")
    roots = []
    if scope in ("thumbnails", "all"):
        roots.append(("thumbnails", thumb_root()))
    if scope in ("derivatives", "all"):
        roots.append(("derivatives", derivative_root()))

    freed = 0
    files = 0
    detail: dict[str, int] = {}
    for name, root in roots:
        detail[name] = 0
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.jpg")):
            freed += p.stat().st_size
            files += 1
            detail[name] += 1
            if not dry_run:
                p.unlink()
    return {
        "scope": scope, "dry_run": dry_run,
        "files_deleted": files, "freed_bytes": freed,
        "per_class": detail,
        "note": "thumbnails and derivative bytes are REGENERABLE "
                "caches; originals are immutable evidence and were "
                "not touched (this operation cannot touch them)",
    }


# ------------------------------------------------------ audit (FS<->DB)

def audit_storage(fix_temps: bool = False,
                  owner_token: str | None = None) -> dict:
    """Consistency audit in BOTH directions the task brief demands:
      - DB -> FS: rows whose bytes are missing on disk
      - FS -> DB: bytes on disk with no database record
    The originals direction reuses the W007 fsck verbatim (it already
    reports corrupt/missing/orphan/temp-residue and never auto-deletes
    committed objects). This function ADDS the derivative + thumbnail
    directions W007 never covered."""
    report = original_store.fsck(fix_temps=fix_temps)

    # ---- derivatives: DB -> FS (registered rows without bytes) ----
    with db() as conn:
        d_rows = conn.execute(
            "SELECT derivative_id, derivative_sha256, photo_id "
            "FROM mvp_derivatives").fetchall()
    d_known = set()
    d_missing = []
    from app.api.derivatives import derivative_path
    for r in d_rows:
        d_known.add(r["derivative_sha256"])
        if not derivative_path(r["derivative_sha256"]).exists():
            d_missing.append({
                "derivative_id": r["derivative_id"],
                "derivative_sha256": r["derivative_sha256"],
                "photo_id": r["photo_id"],
                "note": "bytes pruned or lost; row kept - the next "
                        "identical render request re-generates them"})
    report["derivatives_missing_bytes"] = d_missing

    # ---- derivatives: FS -> DB (bytes with no registered row) ----
    d_orphans = []
    droot = derivative_root()
    if droot.exists():
        for jpg in sorted(droot.rglob("*.jpg")):
            if jpg.stem not in d_known:
                d_orphans.append(jpg.relative_to(droot).as_posix())
    report["derivative_orphan_files"] = d_orphans

    # ---- thumbnails: FS -> original store ----
    # orphan = thumbnail whose ORIGINAL OBJECT is gone from disk (the
    # cache outlived its source - dead weight, safe to report); dangling
    # = source object present but no photo references it anymore.
    t_orphans = []
    t_dangling = []
    troot = thumb_root()
    if troot.exists():
        with db() as conn:
            referenced = {r["sha256"] for r in conn.execute(
                "SELECT DISTINCT sha256 FROM "
                "mvp_photo_originals").fetchall()}
        for jpg in sorted(troot.rglob("*.jpg")):
            sha = jpg.stem.rsplit("_", 1)[0]
            if not original_store.object_path(sha).exists():
                t_orphans.append(jpg.relative_to(troot).as_posix())
            elif sha not in referenced:
                t_dangling.append(jpg.relative_to(troot).as_posix())
    report["thumbnail_orphan_files"] = t_orphans
    report["thumbnail_dangling_originals"] = t_dangling

    problems = (report["corrupt"] or report["missing_files"]
                or report["orphan_files"] or report["bound_missing"]
                or d_orphans or t_orphans)
    report["verdict"] = ("inconsistencies_found" if problems
                         else "consistent")
    report["regenerable_gaps"] = len(d_missing)
    report["note"] = ("audit REPORTS ONLY - it never deletes or "
                      "repairs committed objects (fix_temps only "
                      "removes crash-residue .part files). "
                      "derivatives_missing_bytes are REGENERABLE "
                      "gaps (rows kept, next identical render "
                      "re-creates the bytes) and do not count as "
                      "data-loss inconsistencies; everything else "
                      "does.")
    return report


# ------------------------------------------------------ dedup report

def dedup_report(owner_token: str | None = None,
                 project_id: str | None = None) -> dict:
    """Object-identity view of physical dedup: which content objects
    are referenced by 2+ photos, and the byte saving that sharing
    already produced. Complements the W017 GET /api/v1/photos/
    duplicates PHOTO view (same sha256 identity, different axis);
    intentionally does not re-implement it."""
    sql = ("SELECT po.sha256, COUNT(*) refs, o.byte_size, o.status "
           "FROM mvp_photo_originals po "
           "JOIN mvp_originals o ON o.sha256 = po.sha256 ")
    conds: list[str] = []
    params: list = []
    if owner_token is not None:
        conds.append("po.owner_token = ?")
        params.append(owner_token)
    if project_id is not None:
        conds.append("po.photo_id IN (SELECT photo_id FROM "
                     "mvp_photos WHERE project_id = ?)")
        params.append(project_id)
    if conds:
        sql += "WHERE " + " AND ".join(conds) + " "
    sql += ("GROUP BY po.sha256 HAVING COUNT(*) >= 2 "
            "ORDER BY refs DESC, po.sha256")
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    groups = []
    saved = 0
    for r in rows:
        groups.append({"sha256": r["sha256"], "references": r["refs"],
                       "byte_size": r["byte_size"],
                       "object_status": r["status"]})
        saved += (r["refs"] - 1) * r["byte_size"]
    return {
        "groups": groups,
        "shared_objects": len(groups),
        "dedup_saved_bytes": saved,
        "note": "storage-layer object view; the W017 /photos/"
                "duplicates endpoint reports the same content "
                "identity per-photo - the views are complementary, "
                "and NEITHER deletes anything (originals are immutable "
                "evidence; deletion is a product decision outside "
                "any API)",
    }


# ------------------------------------------------------ quota model

def set_quota(project_id: str, quota_bytes: int, policy: str,
              updated_by: str = "",
              owner_token: str | None = None) -> dict:
    """Upsert one project's storage quota. MODEL ONLY."""
    if quota_bytes < 0:
        raise ApiError(400, "invalid_quota",
                       "quota_bytes must be >= 0 (0 = no storage)")
    if policy not in ("report_only", "enforce_reject"):
        raise ApiError(400, "invalid_policy",
                       "policy must be report_only or enforce_reject")
    with db() as conn:
        proj = conn.execute(
            "SELECT owner_token FROM mvp_projects WHERE project_id = ?",
            (project_id,)).fetchone()
        if proj is None or (owner_token is not None
                            and proj["owner_token"] != owner_token):
            raise ApiError(404, "not_found", "project not found")
        conn.execute(
            "INSERT INTO mvp_storage_quota (project_id, quota_bytes, "
            "policy, updated_at, updated_by) VALUES (?,?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "quota_bytes = excluded.quota_bytes, "
            "policy = excluded.policy, "
            "updated_at = excluded.updated_at, "
            "updated_by = excluded.updated_by",
            (project_id, quota_bytes, policy, utc_now(), updated_by))
    return get_quota(project_id, owner_token=owner_token)


def get_quota(project_id: str,
              owner_token: str | None = None) -> dict | None:
    with db() as conn:
        proj = conn.execute(
            "SELECT owner_token FROM mvp_projects WHERE project_id = ?",
            (project_id,)).fetchone()
        if proj is None or (owner_token is not None
                            and proj["owner_token"] != owner_token):
            raise ApiError(404, "not_found", "project not found")
        row = conn.execute(
            "SELECT * FROM mvp_storage_quota WHERE project_id = ?",
            (project_id,)).fetchone()
    if row is None:
        return None
    return {"project_id": project_id,
            "quota_bytes": row["quota_bytes"],
            "policy": row["policy"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"]}


def check_quota(project_id: str,
                owner_token: str | None = None) -> dict:
    """Quota MODEL evaluation: current usage vs the project quota.

    HARD ENFORCEMENT IS NOT IMPLEMENTED - no production route calls
    this, by design (task brief: model + tests only; wiring it into
    upload rejection is a product decision). The `action` field says
    what a future enforcement point WOULD do, never what happened."""
    quota = get_quota(project_id, owner_token=owner_token)
    used = storage_stats(project_id, owner_token=owner_token)
    used_bytes = (used["originals"]["logical_bytes"]
                  + used["thumbnails"]["bytes"]
                  + used["derivatives"]["bytes"])
    if quota is None:
        return {"project_id": project_id, "quota_bytes": None,
                "used_bytes": used_bytes, "exceeded": False,
                "policy": "none_set", "action": "none",
                "enforcement": "Not Implemented - quota model is "
                               "report-only; enforcement wiring is a "
                               "product decision"}
    exceeded = used_bytes > quota["quota_bytes"]
    return {
        "project_id": project_id,
        "quota_bytes": quota["quota_bytes"],
        "used_bytes": used_bytes,
        "exceeded": exceeded,
        "policy": quota["policy"],
        "action": ("reject_future_uploads (would be)" if exceeded
                   and quota["policy"] == "enforce_reject"
                   else ("report" if exceeded else "none")),
        "enforcement": "Not Implemented - quota model is report-only; "
                       "enforcement wiring is a product decision",
    }
