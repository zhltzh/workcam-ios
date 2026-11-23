"""WC-W006/W009 - business API identity layer.

Identity = an ENROLLED DEVICE TOKEN resolving through the SQLite-backed
device store (W009: mvp_devices with revocation), with read-compat to
the legacy devices.jsonl. Every /api/v1 route calls require_caller;
revoked tokens are refused here; the caller's token is the ownership
key. Cross-subject access returns 404-identical responses (no
existence oracle). Client-supplied device_id is DATA, never identity.
"""

from __future__ import annotations

from fastapi import Request

from app.db import ApiError
from app.services import device_identity


def require_caller(request: Request) -> str:
    """Resolve the authenticated caller's DEVICE TOKEN (the stable
    identity key for ownership). Raises the unified business error on
    any failure. Checks revocation + enrollment + per-token rate limit."""
    token = request.headers.get("X-Device-Token")
    if not token:
        raise ApiError(401, "unauthorized",
                       "X-Device-Token header required for /api/v1")
    device_id = device_identity.resolve_device(token)      # revocation here
    device_identity.check_rate(token)
    return token


def caller_device_id(token: str) -> str:
    return device_identity.resolve_device(token) or "unknown"
