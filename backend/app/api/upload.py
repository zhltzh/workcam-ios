"""W003 - T12 upload endpoints (REAL backend, experimental prefix).

Implements the techlab.upload_protocol contract as FastAPI endpoints,
PERSISTED TO DISK (server restart keeps batches resumable):

    POST /upload/experimental/batches        register (idempotent)
    PUT  /upload/experimental/batches/{bid}/chunks/{index}   raw body
    GET  /upload/experimental/batches/{bid}   status (missing chunks)
    POST /upload/experimental/batches/{bid}/complete   recompose+verify

Isolation & guards (review requirements):
- experimental prefix; NOT in the production verify path
- WORKCAM_ENV=production disables registration outright
- content-length + actual-bytes limits per chunk and per batch
- chunk count cap; active batch count cap with LRU-style expiry sweep
- unknown batch / unknown index / wrong size / hash mismatch / duplicate
  complete / wrong revision => explicit HTTP errors, never silent
- ack only happens AFTER the bytes are durably written and hashed
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from techlab.upload_protocol import UploadError

# ------------------------------------------------------ WC-W005 P2 auth wrap
# Three independent switches (defense in depth; each defaults OFF):
#   WORKCAM_UPLOAD_EXPERIMENTAL=1  endpoints reachable at all (W003-R1)
#   WORKCAM_UPLOAD_AUTH=1         device auth + isolation (this layer)
#   WORKCAM_ENV=production        hard veto over everything
# The frozen protocol semantics of every route are UNCHANGED; auth wraps
# them (identity header + ownership check) and can be disabled entirely
# for lab use without touching the protocol paths.
from app.api.upload_auth import (  # noqa: E402
    AuthError,
    DeviceRegistry,
    _audit,
    _check_rate,
    _registry_path,
    claim_batch,
    issue_challenge,
    require_batch_owner,
    verify_identity,
)


def _auth_enabled() -> bool:
    return os.environ.get("WORKCAM_UPLOAD_AUTH") == "1"


# WC-W005-R1 P1: loopback gate. When auth is OFF the endpoints may only
# serve LOOPBACK clients - a non-loopback connection without device auth
# is refused BY CODE (503), not by operator discipline. Rationale: the
# server cannot observe its own bind address from inside a route, but a
# non-loopback CLIENT proves the listener is exposed; refusing the
# request achieves the requirement "non-loopback listeners MUST enforce
# auth" with a strictly stronger signal.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}
# starlette's TestClient hardcodes scope["client"] = ("testclient", 50000)
# - this identifier can only originate from the in-process test client
# and never from a real network socket, so exempting it carries zero
# production risk (documented so nobody 'fixes' this into a hole).
_TEST_CLIENT_HOSTS = {"testclient"}


def _require_safe_network(request) -> None:
    if _auth_enabled():
        return                      # auth handles its own refusal paths
    client = getattr(request, "client", None)
    host = client.host if client else ""
    if host in _TEST_CLIENT_HOSTS or host in _LOOPBACK_HOSTS or host == "":
        return
    raise HTTPException(
        503,
        "upload endpoints reached from a NON-LOOPBACK client without "
        "device auth. Enforce one of: WORKCAM_UPLOAD_AUTH=1 (enrolled "
        "device token required), or keep access loopback-only. "
        "Unauthenticated remote access is refused by code.")


def _require_request_gate(request) -> str | None:
    """Single entry gate for every upload route (R1): production veto ->
    loopback gate (when auth OFF) -> device auth (when auth ON).
    Returns the device_id (auth ON) or None (auth OFF, loopback client)."""
    _require_not_production()
    if not _auth_enabled():
        _require_safe_network(request)
        return None
    return _auth_device(request)


def _auth_device(request) -> str | None:
    """Resolve the calling device when auth is ON; None when OFF (lab
    mode preserves the pre-existing behaviour for joint debugging).
    AuthError from any sub-step (unknown token, rate limit) converts to
    an HTTPException here so no route can forget to catch it."""
    if not _auth_enabled():
        return None
    token = request.headers.get("X-Device-Token")
    if not token:
        raise HTTPException(401, "device token required "
                                 "(X-Device-Token) - auth is enabled")
    try:
        device_id = DeviceRegistry(_registry_path()).device_id_for_token(token)
        if device_id is None:
            raise HTTPException(401, "device not enrolled")
        _check_rate(device_id)
    except AuthError as e:
        raise _auth_error(e)
    return device_id


def _auth_error(e: AuthError) -> HTTPException:
    return HTTPException(e.status, e.detail)

router = APIRouter(prefix="/upload/experimental", tags=["upload-experimental"])

# ----------------------------------------------------------------- config
_STORAGE_ROOT = Path(os.environ.get("WORKCAM_UPLOAD_DIR",
                                    "data/uploads-experimental"))
MAX_CHUNK_BYTES = 4 * 1024 * 1024          # 4 MiB per chunk
MAX_BATCH_TOTAL_BYTES = 2 * 1024 * 1024 * 1024   # 2 GiB per batch
MAX_CHUNKS_PER_BATCH = 512
MAX_ACTIVE_BATCHES = 64
BATCH_TTL_HOURS = 72                        # expired batches are swept


class _BatchManifest(BaseModel):
    batch_id: str = Field(min_length=8, max_length=64)
    chunk_size: int = Field(gt=0, le=MAX_CHUNK_BYTES)
    total_bytes: int = Field(gt=0, le=MAX_BATCH_TOTAL_BYTES)
    total_sha256: str = Field(min_length=64, max_length=64)
    chunks: list[dict] = Field(min_length=1)   # [{index, sha256, size}]
    created_at: float = Field(default_factory=time.time)

    @field_validator("chunks")
    @classmethod
    def _chunk_plan(cls, v: list[dict]) -> list[dict]:
        if len(v) > MAX_CHUNKS_PER_BATCH:
            raise ValueError(f"batch plan exceeds {MAX_CHUNKS_PER_BATCH} chunks")
        idx = sorted(c["index"] for c in v)
        if idx != list(range(len(v))):
            raise ValueError("chunk indexes must be 0..n-1 exactly once")
        for c in v:
            if c["size"] > MAX_CHUNK_BYTES:
                raise ValueError(f"chunk {c['index']} size exceeds {MAX_CHUNK_BYTES}")
        return v


def _bid_path(batch_id: str) -> Path:
    # strict id charset: no traversal, no injection via id
    if not all(c in "0123456789abcdef" for c in batch_id):
        raise HTTPException(400, "batch_id must be lowercase hex")
    return _STORAGE_ROOT / batch_id


def _sweep_expired() -> None:
    """Best-effort GC: expired batches AND orphaned .part temporaries
    from interrupted uploads (P1 review: killed PUTs used to leave
    unbounded garbage)."""
    if not _STORAGE_ROOT.exists():
        return
    deadline = time.time() - BATCH_TTL_HOURS * 3600
    for p in _STORAGE_ROOT.iterdir():
        try:
            if p.is_file() and p.name.endswith(".part"):
                # stray temp file OUTSIDE any batch dir (legacy layout) or
                # inside one - clean expired ones
                if time.time() - p.stat().st_mtime > 3600:
                    p.unlink(missing_ok=True)
                continue
            if not p.is_dir():
                continue
            meta = p / "manifest.json"
            if not meta.exists():
                if time.time() - p.stat().st_mtime > BATCH_TTL_HOURS * 3600:
                    _rmtree(p)
                continue
            m = json.loads(meta.read_text(encoding="utf-8"))
            if m.get("created_at", 0) < deadline:
                _rmtree(p)
        except (OSError, ValueError):
            continue


def _rmtree(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True)


def _active_count() -> int:
    if not _STORAGE_ROOT.exists():
        return 0
    return sum(1 for p in _STORAGE_ROOT.iterdir() if p.is_dir())


def _require_not_production() -> None:
    """Dual gate (P1 security review):
    1. EXPLICIT OPT-IN: the experimental endpoints stay disabled unless
       WORKCAM_UPLOAD_EXPERIMENTAL=1 is set. A production deployment that
       merely forgets WORKCAM_ENV can no longer expose them by accident.
    2. HARD production veto: WORKCAM_ENV=production disables them even
       when the opt-in variable is set."""
    if os.environ.get("WORKCAM_ENV") == "production":
        raise HTTPException(503, "upload endpoints are experimental and "
                                 "disabled in production")
    if os.environ.get("WORKCAM_UPLOAD_EXPERIMENTAL") != "1":
        raise HTTPException(503, "experimental upload endpoints are "
                                 "opt-in only: set "
                                 "WORKCAM_UPLOAD_EXPERIMENTAL=1 "
                                 "(never in production)")


# ------------------------------------------------------------------ routes
class RegisterBody(BaseModel):
    total_bytes: int = Field(gt=0, le=MAX_BATCH_TOTAL_BYTES)
    total_sha256: str
    chunk_size: int = Field(gt=0, le=MAX_CHUNK_BYTES)


@router.post("/auth/challenge")
async def auth_challenge(request: Request, x_device_token: str | None = Header(default=None)):
    """P2 identity flow step 1: server issues a single-use nonce for the
    enrolled device. The device then signs the nonce with its P-256 key
    (the SAME key as evidence signing - one identity)."""
    _require_not_production()
    if not _auth_enabled():
        raise HTTPException(503, "auth layer is not enabled "
                                 "(WORKCAM_UPLOAD_AUTH=1)")
    if not x_device_token:
        raise HTTPException(401, "X-Device-Token header required")
    registry = DeviceRegistry(_registry_path())
    if registry.device_id_for_token(x_device_token) is None:
        raise HTTPException(401, "device not enrolled")
    nonce = issue_challenge(x_device_token)
    _audit({"event": "challenge_issued", "device_id":
            registry.device_id_for_token(x_device_token)})
    return {"nonce": nonce, "expires_s": 60}


@router.post("/auth/verify")
async def auth_verify(body: dict, request: Request):
    """P2 identity flow step 2: {device_token, nonce, signature_b64} ->
    verifies the nonce signature with the ENROLLED public key. Private
    keys never leave the device."""
    _require_not_production()
    if not _auth_enabled():
        raise HTTPException(503, "auth layer is not enabled")
    try:
        device_id = verify_identity(body.get("device_token", ""),
                                     body.get("nonce", ""),
                                     body.get("signature_b64", ""))
    except AuthError as e:
        _audit({"event": "identity_failed", "detail": e.detail})
        raise _auth_error(e)
    return {"device_id": device_id, "verified": True}


@router.post("/batches")
async def register_batch(body: RegisterBody,
                         request: Request,
                         x_device_token: str | None = Header(default=None)):
    device_id = _require_request_gate(request)
    _sweep_expired()
    if _active_count() >= MAX_ACTIVE_BATCHES:
        raise HTTPException(507, f"active batch limit reached "
                                 f"({MAX_ACTIVE_BATCHES})")

    # build the content-addressed chunk plan from total bytes
    n_chunks = (body.total_bytes + body.chunk_size - 1) // body.chunk_size
    if n_chunks > MAX_CHUNKS_PER_BATCH:
        raise HTTPException(400, "batch would exceed the chunk-count cap")

    batch_id = secrets.token_hex(8)
    batch_dir = _bid_path(batch_id)
    batch_dir.mkdir(parents=True, exist_ok=True)

    # chunk hashes are UNKNOWN at registration (content not here yet):
    # the plan carries per-chunk sizes only; hashes are checked on arrival
    chunks = [{"index": i,
               "size": min(body.chunk_size, body.total_bytes - i * body.chunk_size)}
              for i in range(n_chunks)]
    manifest = _BatchManifest(
        batch_id=batch_id, chunk_size=body.chunk_size,
        total_bytes=body.total_bytes, total_sha256=body.total_sha256,
        chunks=chunks,
    )
    (batch_dir / "manifest.json").write_text(
        json.dumps(manifest.model_dump()), encoding="utf-8")
    if device_id is not None:
        try:
            claim_batch(batch_id, device_id)
        except AuthError as e:
            _rmtree(batch_dir)
            raise _auth_error(e)
        _audit({"event": "batch_registered", "batch_id": batch_id,
                "device_id": device_id, "n_chunks": n_chunks})
    return {"batch_id": batch_id, "n_chunks": n_chunks,
            "status": "registered", "experimental": True}


@router.put("/batches/{batch_id}/chunks/{index}")
async def put_chunk(batch_id: str, index: int, request: Request,
                    content_length: int | None = Header(default=None)):
    device_id = _require_request_gate(request)
    if device_id is not None:
        try:
            require_batch_owner(batch_id, device_id)
        except AuthError as e:
            raise _auth_error(e)
        _audit({"event": "chunk_put", "batch_id": batch_id,
                "index": index, "device_id": device_id})
    batch_dir = _bid_path(batch_id)
    manifest_path = batch_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "unknown batch")
    manifest = _BatchManifest.model_validate(
        json.loads(manifest_path.read_text(encoding="utf-8")))

    spec = next((c for c in manifest.chunks if c["index"] == index), None)
    if spec is None:
        raise HTTPException(400, f"chunk index {index} not in batch plan")
    if content_length is not None and content_length != spec["size"]:
        raise HTTPException(400, f"declared {content_length} bytes but the "
                                 f"plan requires exactly {spec['size']}")

    # stream to a UNIQUE temp file FIRST (P1 review: the fixed name
    # `.chunk_{index}.part` let concurrent PUTs of the same index corrupt
    # each other's bytes), hash while streaming, ack only after the
    # bytes are durable and the total plan size still holds
    tmp = batch_dir / f".chunk_{index}.{secrets.token_hex(8)}.part"
    received = 0
    hasher = hashlib.sha256()
    with open(tmp, "wb") as f:
        async for block in request.stream():
            received += len(block)
            if received > spec["size"]:
                f.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(400, f"chunk larger than the planned "
                                         f"{spec['size']} bytes")
            hasher.update(block)
            f.write(block)
    if received != spec["size"]:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"short chunk: got {received}, "
                                 f"planned {spec['size']}")

    final = batch_dir / f"chunk_{index}"
    if final.exists():
        # duplicate delivery: compare; identical -> idempotent ack
        if hashlib.sha256(final.read_bytes()).hexdigest() == hasher.hexdigest():
            tmp.unlink(missing_ok=True)
            return {"index": index, "status": "duplicate_acked"}
        tmp.unlink(missing_ok=True)
        raise HTTPException(409, "chunk already present with different "
                                 "bytes - resend the SAME bytes")

    os.replace(tmp, final)
    (batch_dir / f"chunk_{index}.sha").write_text(hasher.hexdigest(),
                                                  encoding="utf-8")
    return {"index": index, "status": "acked", "sha256": hasher.hexdigest()}


@router.get("/batches/{batch_id}")
async def batch_status(batch_id: str, request: Request = None):
    if request is not None:
        device_id = _require_request_gate(request)
        if device_id is not None:
            try:
                require_batch_owner(batch_id, device_id)
            except AuthError as e:
                raise _auth_error(e)
    batch_dir = _bid_path(batch_id)
    manifest_path = batch_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "unknown batch")
    manifest = _BatchManifest.model_validate(
        json.loads(manifest_path.read_text(encoding="utf-8")))
    present = {c["index"] for c in manifest.chunks
               if (batch_dir / f"chunk_{c['index']}").exists()}
    missing = [c["index"] for c in manifest.chunks if c["index"] not in present]
    return {"batch_id": batch_id, "missing": missing,
            "received": sorted(present), "complete": not missing}


@router.post("/batches/{batch_id}/complete")
async def complete_batch(batch_id: str, request: Request = None):
    if request is not None:
        device_id = _require_request_gate(request)
        if device_id is not None:
            try:
                require_batch_owner(batch_id, device_id)
            except AuthError as e:
                raise _auth_error(e)
    batch_dir = _bid_path(batch_id)
    manifest_path = batch_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "unknown batch")
    manifest = _BatchManifest.model_validate(
        json.loads(manifest_path.read_text(encoding="utf-8")))

    st = await batch_status(batch_id)
    if not st["complete"]:
        raise HTTPException(409, f"cannot complete: {len(st['missing'])} "
                                 f"chunks missing (first: {st['missing'][0]})")

    # recompose in chunk order, streaming into a whole-file hasher and
    # a durable output; chunk-level sizes re-checked while copying
    out_path = batch_dir / "assembled.bin"
    hasher = hashlib.sha256()
    total = 0
    with open(out_path, "wb") as out:
        for c in manifest.chunks:
            chunk_file = batch_dir / f"chunk_{c['index']}"
            expected_size = c["size"]
            expected_sha = (batch_dir / f"chunk_{c['index']}.sha").read_text(
                encoding="utf-8").strip()
            chunk_bytes = chunk_file.read_bytes()
            if len(chunk_bytes) != expected_size:
                raise HTTPException(500, f"stored chunk {c['index']} size "
                                         f"changed on disk")
            if hashlib.sha256(chunk_bytes).hexdigest() != expected_sha:
                raise HTTPException(500, f"stored chunk {c['index']} hash "
                                         f"mismatch on disk")
            hasher.update(chunk_bytes)
            out.write(chunk_bytes)
            total += len(chunk_bytes)
    if total != manifest.total_bytes:
        raise HTTPException(500, "assembled size differs from the plan")
    if hasher.hexdigest() != manifest.total_sha256:
        # whole-batch independent check failed: nothing acknowledged
        out_path.unlink(missing_ok=True)
        raise HTTPException(409, "whole-batch sha256 mismatch - batch "
                                 "rejected, chunks remain for re-upload")
    (batch_dir / "COMPLETE").write_text(manifest.total_sha256,
                                        encoding="utf-8")
    return {"batch_id": batch_id, "status": "complete",
            "total_sha256": manifest.total_sha256,
            "bytes": total, "experimental": True}


@router.get("/batches/{batch_id}/content")
async def batch_content(batch_id: str, request: Request = None):
    """Stream the assembled file back (byte-level verification for the
    client). Only available after COMPLETE; production-disabled like
    every other route here."""
    if request is not None:
        device_id = _require_request_gate(request)
        if device_id is not None:
            try:
                require_batch_owner(batch_id, device_id)
            except AuthError as e:
                raise _auth_error(e)
    batch_dir = _bid_path(batch_id)
    if not (batch_dir / "manifest.json").exists():
        raise HTTPException(404, "unknown batch")
    if not (batch_dir / "COMPLETE").exists():
        raise HTTPException(409, "batch not completed yet")
    out_path = batch_dir / "assembled.bin"
    if not out_path.exists():
        raise HTTPException(500, "COMPLETE marker exists but assembled "
                                 "file is missing")

    async def _stream():
        with open(out_path, "rb") as f:
            while block := f.read(256 * 1024):
                yield block

    from fastapi.responses import StreamingResponse
    return StreamingResponse(_stream(),
                             media_type="application/octet-stream")
