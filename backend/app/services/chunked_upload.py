"""TM35: resumable chunked upload for original photos.

Design (server-restart safe by construction):
  - a session = one DB row (mvp_upload_sessions) + one directory of
    chunk files: data/upload-sessions/<upload_id>/<index>.part
  - chunks may arrive IN ANY ORDER; re-sending a received chunk is an
    idempotent no-op (duplicate chunks, task requirement)
  - every chunk is hash-verified on arrival (per-chunk sha256 in the
    request header) - a corrupt chunk is refused BEFORE it occupies
    the received set
  - complete() streams the assembled bytes through the W007 store
    discipline: re-hash WHILE assembling; the full-file digest must
    equal the photo's claimed original_sha256 or NOTHING reaches the
    immutable store (typed 409, session stays resumable)
  - cancel() removes the session directory (temp cleanup) and marks
    the row cancelled; the photo record itself is untouched
  - expiry: sessions older than TTL are garbage-collected lazily on
    the next open/complete call (upload expiry requirement)
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from pathlib import Path

from app.db import ApiError, db, utc_now
from app.services import original_store

SESSION_TTL_SECONDS = 24 * 3600
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _sessions_root() -> Path:
    import app.db as dbm
    return dbm._db_path().parent / "upload-sessions"


def _session_dir(upload_id: str) -> Path:
    if not re.match(r"^[0-9a-f]{32}$", upload_id):
        raise ApiError(400, "invalid_upload_id",
                       "upload_id must be 32 lowercase hex")
    return _sessions_root() / upload_id


def _load_session(conn, upload_id: str, photo_id: str,
                  owner: str) -> dict:
    row = conn.execute(
        "SELECT * FROM mvp_upload_sessions WHERE upload_id = ?",
        (upload_id,)).fetchone()
    if row is None or row["photo_id"] != photo_id or \
            row["owner_token"] != owner:
        # cross-subject -> 404 identical to not-found
        raise ApiError(404, "not_found", "upload session not found")
    return dict(row)


def _gc_expired(conn) -> None:
    cutoff = time.time() - SESSION_TTL_SECONDS
    rows = conn.execute(
        "SELECT upload_id, created_at, status FROM "
        "mvp_upload_sessions WHERE status = 'active'").fetchall()
    for r in rows:
        created = r["created_at"]
        # ISO strings sort correctly for the cutoff comparison
        if created < _iso_cutoff():
            conn.execute(
                "UPDATE mvp_upload_sessions SET status='cancelled', "
                "updated_at=? WHERE upload_id=?",
                (utc_now(), r["upload_id"]))
            d = _session_dir(r["upload_id"])
            if d.exists():
                import shutil
                shutil.rmtree(d, ignore_errors=True)


def _iso_cutoff() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) -
            timedelta(seconds=SESSION_TTL_SECONDS)).isoformat()


def open_session(photo_id: str, owner: str, claimed_sha256: str,
                 chunk_size: int, total_chunks: int,
                 total_bytes: int) -> dict:
    """Start (or re-open) a resumable session for one photo."""
    if not _SHA_RE.match(claimed_sha256):
        raise ApiError(400, "invalid_hash",
                       "claimed sha256 must be 64 lowercase hex")
    if not 64 * 1024 <= chunk_size <= 8 * 1024 * 1024:
        raise ApiError(400, "invalid_chunk_size",
                       "chunk_size must be 64KiB..8MiB")
    if not 1 <= total_chunks <= 100_000:
        raise ApiError(400, "invalid_chunk_count",
                       "total_chunks must be 1..100000")
    if total_bytes <= 0:
        raise ApiError(400, "invalid_size", "total_bytes must be > 0")
    upload_id = secrets.token_hex(16)
    now = utc_now()
    with db() as conn:
        photo = conn.execute(
            "SELECT p.project_id FROM mvp_photos p "
            "JOIN mvp_projects pr ON p.project_id = pr.project_id "
            "WHERE p.photo_id = ? AND pr.owner_token = ?",
            (photo_id, owner)).fetchone()
        if photo is None:
            raise ApiError(404, "not_found", "photo not found")
        existing = conn.execute(
            "SELECT * FROM mvp_upload_sessions WHERE photo_id = ? "
            "AND status = 'active'", (photo_id,)).fetchone()
        if existing is not None and existing["claimed_sha256"] == \
                claimed_sha256 and existing["chunk_size"] == chunk_size:
            # idempotent re-open: same photo + same params resumes
            return _session_view(existing, conn)
        conn.execute(
            "INSERT INTO mvp_upload_sessions (upload_id, photo_id, "
            "owner_token, claimed_sha256, chunk_size, total_chunks, "
            "received_mask, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,'[]','active',?,?)",
            (upload_id, photo_id, owner, claimed_sha256, chunk_size,
             total_chunks, now, now))
        row = conn.execute(
            "SELECT * FROM mvp_upload_sessions WHERE upload_id = ?",
            (upload_id,)).fetchone()
    _session_dir(upload_id).mkdir(parents=True, exist_ok=True)
    return _session_view(row, conn)


def _session_view(row, conn) -> dict:
    received = json.loads(row["received_mask"])
    return {"upload_id": row["upload_id"],
            "photo_id": row["photo_id"],
            "chunk_size": row["chunk_size"],
            "total_chunks": row["total_chunks"],
            "received_chunks": received,
            "received_count": len(received),
            "status": row["status"],
            "next_chunk": _next_missing(received,
                                        row["total_chunks"])}


def _next_missing(received: list[int], total: int) -> int:
    have = set(received)
    for i in range(total):
        if i not in have:
            return i
    return total          # all present


def put_chunk(photo_id: str, upload_id: str, index: int,
              chunk_sha256: str, data: bytes, owner: str) -> dict:
    """Store one verified chunk (any order; duplicates idempotent)."""
    with db() as conn:
        s = _load_session(conn, upload_id, photo_id, owner)
        if s["status"] != "active":
            raise ApiError(409, "session_not_active",
                           f"session is {s['status']}")
        total = s["total_chunks"]
        if not 0 <= index < total:
            raise ApiError(400, "chunk_out_of_range",
                           f"index must be 0..{total - 1}")
        if len(data) > s["chunk_size"]:
            raise ApiError(400, "chunk_too_large",
                           "chunk exceeds the session chunk_size")
        actual = hashlib.sha256(data).hexdigest()
        if actual != chunk_sha256:
            raise ApiError(400, "chunk_hash_mismatch",
                           "chunk bytes do not match the provided "
                           "sha256 - nothing stored")
        received = json.loads(s["received_mask"])
        d = _session_dir(upload_id)
        part = d / f"{index:08d}.part"
        if index in received:
            # duplicate chunk: verify the EXISTING bytes match, then
            # no-op (idempotence with proof)
            if part.exists() and hashlib.sha256(
                    part.read_bytes()).hexdigest() == actual:
                return {"index": index, "stored": False,
                        "duplicate": True, "received_count":
                            len(received)}
            raise ApiError(409, "chunk_conflict",
                           "index already received with different "
                           "bytes")
        # atomic write discipline per chunk (tmp -> rename)
        tmp = d / f".{index:08d}.tmp"
        tmp.write_bytes(data)
        part = d / f"{index:08d}.part"
        tmp.replace(part)
        received.append(index)
        conn.execute(
            "UPDATE mvp_upload_sessions SET received_mask=?, "
            "updated_at=? WHERE upload_id=?",
            (json.dumps(sorted(received)), utc_now(), upload_id))
        return {"index": index, "stored": True, "duplicate": False,
                "received_count": len(received)}


def session_status(photo_id: str, upload_id: str, owner: str) -> dict:
    with db() as conn:
        s = _load_session(conn, upload_id, photo_id, owner)
        return _session_view(s, conn)


def cancel_session(photo_id: str, upload_id: str, owner: str) -> dict:
    """Cancel + clean the chunk directory. The photo record and any
    already-stored ORIGINAL object are never touched."""
    with db() as conn:
        s = _load_session(conn, upload_id, photo_id, owner)
        conn.execute(
            "UPDATE mvp_upload_sessions SET status='cancelled', "
            "updated_at=? WHERE upload_id=?",
            (utc_now(), upload_id))
    d = _session_dir(upload_id)
    if d.exists():
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    return {"upload_id": upload_id, "status": "cancelled",
            "chunks_removed": True}


def complete_session(photo_id: str, upload_id: str,
                     owner: str) -> dict:
    """Assemble + verify the FULL file, then hand the bytes to the
    immutable store. Any gap -> typed 409 with the missing list; any
    digest mismatch -> typed 409 and the assembled temp is discarded
    (the session stays active for a corrected retry)."""
    with db() as conn:
        s = _load_session(conn, upload_id, photo_id, owner)
        if s["status"] != "active":
            raise ApiError(409, "session_not_active",
                           f"session is {s['status']}")
        received = set(json.loads(s["received_mask"]))
        total = s["total_chunks"]
        missing = [i for i in range(total) if i not in received]
        if missing:
            raise ApiError(409, "upload_incomplete",
                           f"missing chunks: {missing[:32]}"
                           f"{'...' if len(missing) > 32 else ''}",
                           {"missing_chunks": missing})
        claimed = s["claimed_sha256"]
    d = _session_dir(upload_id)
    # stream-assemble into a hash-while-reading verification pass
    h = hashlib.sha256()
    assembled = d / ".assembled.tmp"
    with open(assembled, "wb") as out:
        for i in range(total):
            part = d / f"{i:08d}.part"
            if not part.exists():
                raise ApiError(409, "upload_incomplete",
                               f"chunk {i} file missing (session "
                               f"directory damaged)",
                               {"missing_chunks": [i]})
            data = part.read_bytes()
            h.update(data)
            out.write(data)
    if h.hexdigest() != claimed:
        assembled.unlink(missing_ok=True)
        # the session stays resumable: the client re-sends the wrong
        # chunks under the same session
        raise ApiError(409, "full_hash_mismatch",
                       "assembled bytes do not hash to the claimed "
                       "original_sha256 - session remains open; "
                       "re-send the affected chunks")
    # full file verified -> the normal immutable-store path (which
    # re-verifies on its own and returns the canonical result)
    data = assembled.read_bytes()
    result = original_store.store_original(data, claimed)
    assembled.unlink(missing_ok=True)
    with db() as conn:
        conn.execute(
            "UPDATE mvp_upload_sessions SET status='completed', "
            "updated_at=? WHERE upload_id=?",
            (utc_now(), upload_id))
    # bind the photo (idempotent; owner comes from the session row)
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO mvp_photo_originals (photo_id, "
            "sha256, owner_token, bound_at) VALUES (?,?,?,?)",
            (photo_id, claimed, owner, utc_now()))
    import shutil
    shutil.rmtree(d, ignore_errors=True)     # chunk cleanup
    return {"upload_id": upload_id, "status": "completed",
            "store_result": result, "bound": True}
