"""T12 - Weak-network resumable & idempotent upload protocol (Wave 3).

PROTOCOL (offline-first evidence; server = the W002/W005 backend):

  Batch manifest: the client splits an evidence package into CONTENT-
  ADDRESSED chunks (sha256(chunk)). The upload plan is:

    POST /upload/batch
      batch_id, total_chunks, chunk_size, total_sha256 (whole package),
      items: [{index, sha256, size}]

    POST /upload/chunk/<batch_id>/<index>   (raw body; retriable; ANY
      order accepted; duplicates are idempotent no-ops; server verifies
      the chunk sha256 BEFORE acknowledging - acked == verified)

    GET  /upload/status/<batch_id>           -> missing indexes (resume)

    POST /upload/complete/<batch_id>         -> server recomposes ONLY
      when every chunk is present; recomputed whole-batch sha256 must
      equal total_sha256; duplicates complete requests are idempotent.

  Wire-level honesty rules:
    - acking a chunk the server never durably stored is FORBIDDEN;
    - a chunk that fails its hash on arrival is NACKed and does NOT
      occupy the slot (retry with the SAME bytes is the only legal fix);
    - the whole-batch verification is INDEPENDENT of per-chunk checks -
      both must pass.

  This module is the PROTOCOL MODEL + in-memory transport used by the
  tests; the FastAPI endpoints are Wave 3+ work (documented as such).
  The model IS the contract the endpoints must implement.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator


class UploadError(ValueError):
    pass


@dataclass
class ChunkSpec:
    index: int
    sha256: str
    size: int


class UploadManifest(BaseModel):
    batch_id: str = Field(default_factory=lambda: secrets.token_hex(8))
    chunk_size: int = Field(gt=0)
    total_sha256: str
    items: list[ChunkSpec]

    @field_validator("items")
    @classmethod
    def _indexes(cls, items: list[ChunkSpec]) -> list[ChunkSpec]:
        idx = [c.index for c in items]
        if sorted(idx) != list(range(len(items))):
            raise UploadError("chunk indexes must be 0..n-1 exactly once")
        return items


def make_batch(data: bytes, chunk_size: int = 256 * 1024) -> tuple[UploadManifest, list[bytes]]:
    """Split data into content-addressed chunks (client side)."""
    chunks: list[bytes] = []
    specs: list[ChunkSpec] = []
    for i in range(0, len(data), chunk_size):
        c = data[i:i + chunk_size]
        chunks.append(c)
        specs.append(ChunkSpec(index=len(specs), sha256=hashlib.sha256(c).hexdigest(),
                               size=len(c)))
    if not chunks:
        raise UploadError("empty batch")
    manifest = UploadManifest(chunk_size=chunk_size,
                              total_sha256=hashlib.sha256(data).hexdigest(),
                              items=specs)
    return manifest, chunks


@dataclass
class ServerBatch:
    manifest: UploadManifest
    received: dict[int, bytes] = field(default_factory=dict)


class UploadServerModel:
    """In-memory protocol model implementing the four endpoints with ALL
    honesty rules enforced. The FastAPI layer must reproduce this
    behaviour exactly - tests here ARE the contract."""

    def __init__(self) -> None:
        self._batches: dict[str, ServerBatch] = {}

    def register_batch(self, manifest: UploadManifest) -> dict:
        existing = self._batches.get(manifest.batch_id)
        if existing is not None:
            if existing.manifest.total_sha256 != manifest.total_sha256:
                raise UploadError(
                    f"batch_id reuse with DIFFERENT content is a conflict "
                    f"(existing {existing.manifest.total_sha256[:12]}... "
                    f"vs new {manifest.total_sha256[:12]}...)")
            # identical re-registration: idempotent no-op
            return {"batch_id": manifest.batch_id, "status": "registered",
                    "already_existed": True}
        self._batches[manifest.batch_id] = ServerBatch(manifest=manifest)
        return {"batch_id": manifest.batch_id, "status": "registered",
                "already_existed": False}

    def put_chunk(self, batch_id: str, index: int, payload: bytes) -> dict:
        batch = self._batches.get(batch_id)
        if batch is None:
            raise UploadError("unknown batch - register first")
        spec = next((c for c in batch.manifest.items if c.index == index), None)
        if spec is None:
            raise UploadError(f"chunk index {index} not in batch plan")
        actual = hashlib.sha256(payload).hexdigest()
        if actual != spec.sha256:
            # NACK: corrupt/attacked chunk does NOT occupy the slot
            raise UploadError(
                f"chunk {index} hash mismatch: expected {spec.sha256[:12]}..., "
                f"got {actual[:12]}... - resend the SAME bytes")
        if index in batch.received:
            # duplicate: idempotent ack, no re-store needed
            return {"index": index, "status": "duplicate_acked"}
        batch.received[index] = payload
        return {"index": index, "status": "acked"}

    def status(self, batch_id: str) -> dict:
        batch = self._batches.get(batch_id)
        if batch is None:
            raise UploadError("unknown batch")
        missing = [c.index for c in batch.manifest.items
                   if c.index not in batch.received]
        return {"batch_id": batch_id, "missing": missing,
                "received": sorted(batch.received.keys()),
                "complete": not missing}

    def complete(self, batch_id: str) -> dict:
        batch = self._batches.get(batch_id)
        if batch is None:
            raise UploadError("unknown batch")
        st = self.status(batch_id)
        if not st["complete"]:
            raise UploadError(
                f"cannot complete: {len(st['missing'])} chunks missing "
                f"(first: {st['missing'][0]})")
        data = b"".join(batch.received[c.index] for c in batch.manifest.items)
        whole = hashlib.sha256(data).hexdigest()
        if whole != batch.manifest.total_sha256:
            # per-chunk passed but whole failed: report, do not accept
            raise UploadError(
                "whole-batch hash mismatch despite valid chunks - "
                "batch rejected; nothing was overwritten")
        return {"batch_id": batch_id, "status": "complete",
                "total_sha256": whole, "bytes": len(data)}


class ClientResume:
    """Client-side resume helper: given a server status, yield the chunk
    indexes that still need sending (retry/duplicate-safe by design)."""

    @staticmethod
    def pending(status: dict, all_indexes: list[int]) -> list[int]:
        missing = set(status.get("missing", []))
        return [i for i in all_indexes if i in missing]
