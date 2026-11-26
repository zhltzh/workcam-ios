"""WC-W007 P1/P2/P3 - original-photo storage API.

Endpoints (all under /api/v1, all owner-scoped via the W006 identity
layer - 404-identical to not-found for cross-subject access):

  PUT  /photos/{photo_id}/original                upload+bind (atomic:
                                                    store object, then
                                                    bind; mismatch -> 400)
  GET  /photos/{photo_id}/original                authorized download
  GET  /photos/{photo_id}/original/integrity      P2: object integrity
                                                    + evidence chain state
  GET  /projects/{pid}/photos                      EXTENDED with
                                                    original + evidence
                                                    status (additive)

Compatibility: /verify, signature payloads, evidence-link verification
and the anchor protocol are untouched. This module only ever READS the
evidence-link rows to surface their state next to storage integrity.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.api.identity import require_caller
from app.db import ApiError, db, utc_now
from app.api.mvp import _get_owned_photo, _require_uuid
from app.services import original_store

router = APIRouter(prefix="/api/v1", tags=["mvp-originals"])


class OriginalIntegrity(BaseModel):
    sha256: str
    object_status: str
    byte_size: int | None = None
    file_present: bool
    file_hash_valid: bool | None = None
    verified_at: str | None = None
    note: str


# ---------------------------------------------------------- P0/P1: upload


@router.post("/photos/{photo_id}/original-session")
async def open_upload_session(photo_id: str, body: dict,
                              request: Request):
    """TM35: start a RESUMABLE chunked upload session (chunks land on
    disk; the session survives server restarts by construction)."""
    token = require_caller(request)
    from app.services import chunked_upload
    return chunked_upload.open_session(
        photo_id, token,
        body.get("claimed_sha256", ""),
        int(body.get("chunk_size", 0)),
        int(body.get("total_chunks", 0)),
        int(body.get("total_bytes", 0)))


@router.put("/photos/{photo_id}/original-session/{upload_id}"
           "/chunk/{index}")
async def put_upload_chunk(photo_id: str, upload_id: str,
                           index: int, request: Request):
    token = require_caller(request)
    from app.services import chunked_upload
    chunk_sha = request.headers.get("X-Chunk-SHA256", "")
    data = await request.body()
    return chunked_upload.put_chunk(photo_id, upload_id, index,
                                    chunk_sha, data, token)


@router.get("/photos/{photo_id}/original-session/{upload_id}")
async def upload_session_status(photo_id: str, upload_id: str,
                               request: Request):
    token = require_caller(request)
    from app.services import chunked_upload
    return chunked_upload.session_status(photo_id, upload_id, token)


@router.delete("/photos/{photo_id}/original-session/{upload_id}")
async def cancel_upload_session(photo_id: str, upload_id: str,
                                request: Request):
    token = require_caller(request)
    from app.services import chunked_upload
    return chunked_upload.cancel_session(photo_id, upload_id, token)


@router.post("/photos/{photo_id}/original-session/{upload_id}"
             "/complete")
async def complete_upload_session(photo_id: str, upload_id: str,
                                 request: Request):
    token = require_caller(request)
    from app.services import chunked_upload
    return chunked_upload.complete_session(photo_id, upload_id, token)


@router.put("/photos/{photo_id}/original")
async def upload_original(photo_id: str, request: Request):
    """Store the ORIGINAL bytes for an owned photo record and bind it.

    The body is the raw photo bytes. Integrity is verified DURING the
    write (hash-while-streaming): a mismatch with the photo's REGISTERED
    original_sha256 rejects the request and persists NOTHING.

    Idempotent: same photo + same content -> same binding; a photo may
    be re-uploaded with identical bytes freely.
    Immutable: different content for an ALREADY-BOUND photo is 409
    (the photo's registered hash defines the object; changing it is
    evidence tampering, not an update)."""
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    data = await request.body()
    if not data:
        raise ApiError(400, "empty_body", "photo bytes required")

    # P0: verify the CLAIMED hash against the ACTUAL body BEFORE any
    # store call. store_original's own fast path re-verifies the DISK
    # object - but the BODY may differ from both (found by the sim
    # client: a tampered body hit the idempotent fast path and got
    # a bogus 200; the fix closes exactly that gap).
    import hashlib as _h
    body_sha = _h.sha256(data).hexdigest()

    with db() as conn:
        photo = _get_owned_photo(conn, photo_id, token)
        claimed = photo["original_sha256"]

        existing = conn.execute(
            "SELECT sha256 FROM mvp_photo_originals WHERE photo_id = ?",
            (photo_id,)).fetchone()
        if existing is not None and existing["sha256"] != claimed:
            raise ApiError(409, "binding_conflict",
                           "photo is bound to a different original object")

    if body_sha != claimed:
        raise ApiError(400, "hash_mismatch",
                       f"uploaded content hashes to {body_sha} but the "
                       f"photo's registered original_sha256 is {claimed} - "
                       f"object rejected")

    # store the object (validates claimed hash == actual bytes)
    result = original_store.store_original(data, claimed)

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
            "idempotent_replay": result.get("idempotent_replay", False),
            "note": "file integrity verified; photo authenticity is NOT "
                    "claimed by storage"}


# ------------------------------------------------------ P3: download


@router.get("/photos/{photo_id}/original")
async def download_original(photo_id: str, request: Request):
    """Authorized download of the ORIGINAL bytes. Ownership chains
    through the photo's project (cross-subject -> 404 identical to
    not-found). No disk path is ever exposed - the object is addressed
    by the photo's registered hash and read from the content-addressed
    store."""
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    with db() as conn:
        photo = _get_owned_photo(conn, photo_id, token)
    sha = photo["original_sha256"]
    binding = None
    with db() as conn:
        binding = conn.execute(
            "SELECT sha256 FROM mvp_photo_originals WHERE photo_id = ?",
            (photo_id,)).fetchone()
    if binding is None:
        raise ApiError(404, "original_not_uploaded",
                       "original object not yet stored for this photo")
    data = original_store.read_original(sha)
    # serve with integrity annotation: the caller can re-hash to confirm
    return Response(
        content=data, media_type="application/octet-stream",
        headers={"X-Original-Sha256": sha,
                 "X-Object-Size": str(len(data)),
                 "Content-Disposition":
                     f'attachment; filename="{sha[:16]}.jpg"'})


# --------------------------------------------- P2: integrity + chain state


@router.get("/photos/{photo_id}/original/integrity")
async def original_integrity(photo_id: str, request: Request):
    """Storage integrity + the photo's evidence chain, in one view:

      storage says: do the bytes exist and match the hash
      evidence says: has the server verified anything about origin

    These are SEPARATE claims (P0 requirement) - a stored, intact object
    is 'file integrity verified', never 'authentic photo'."""
    token = require_caller(request)
    _require_uuid(photo_id, "photo_id")
    with db() as conn:
        photo = _get_owned_photo(conn, photo_id, token)
        sha = photo["original_sha256"]
        obj = conn.execute(
            "SELECT * FROM mvp_originals WHERE sha256 = ?",
            (sha,)).fetchone()
        bound = conn.execute(
            "SELECT 1 FROM mvp_photo_originals WHERE photo_id = ?",
            (photo_id,)).fetchone() is not None
        links = conn.execute(
            "SELECT verification_status, failure_reason, verified_at "
            "FROM mvp_evidence_links WHERE photo_id = ? "
            "ORDER BY created_at DESC", (photo_id,)).fetchall()

    path = original_store.object_path(sha)
    present = path.exists()
    hash_valid = None
    if present:
        import hashlib as _h
        h = _h.sha256()
        with open(path, "rb") as f:
            while block := f.read(256 * 1024):
                h.update(block)
        hash_valid = h.hexdigest() == sha

    integrity = OriginalIntegrity(
        sha256=sha,
        object_status=obj["status"] if obj else "not_registered",
        byte_size=obj["byte_size"] if obj else None,
        file_present=present,
        file_hash_valid=hash_valid,
        verified_at=obj["verified_at"] if obj else None,
        note=("file integrity verified; photo authenticity is NOT "
              "claimed by storage"))

    # evidence chain summary (READ-ONLY from the existing machine)
    chain = []
    best = "none"
    rank = {"none": 0, "unverified": 1, "signature_valid": 2,
            "verification_failed": 2, "cryptographically_valid": 3,
            "server_anchored": 4}
    for l in links:
        chain.append({"verification_status": l["verification_status"],
                      "failure_reason": l["failure_reason"],
                      "verified_at": l["verified_at"]})
        if rank.get(l["verification_status"], 0) > rank.get(best, 0):
            best = l["verification_status"]

    return {
        "photo_id": photo_id,
        "original": integrity.model_dump(),
        "bound_to_photo": bound,
        "evidence": {
            "link_count": len(links),
            "highest_status": best,
            "links": chain,
        },
        "claims": {
            "file_integrity_verified": present and hash_valid is True,
            "photo_authenticity_proven": False,
            "authenticity_note":
                "storage verifies bytes only; authenticity requires "
                "verified evidence links and is still not 'proof the "
                "scene was real'",
        },
    }
