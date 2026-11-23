"""WC-W006 P4 - business API identity model.

THE AUDIT FINDING: the experimental upload DeviceRegistry (opt-in,
file-based, loopback-gated) exists, but NOTHING protects cross-subject
business data. This module is the minimal security layer for /api/v1:

  - Caller identity = an ENROLLED DEVICE TOKEN (reuses upload_auth's
    DeviceRegistry - one identity system, not two).
  - Every project row records its owner_token at creation; ownership is
    checked on EVERY subsequent access.
  - The client-supplied `device_id` field on photos is DATA (clearly
    annotated untrusted) and NEVER an authentication identity.
  - Cross-subject access returns 404 - identical to 'not found', so a
    caller cannot probe which UUIDs exist under other owners.
  - The whole /api/v1 surface defaults to REQUIRING a valid enrolled
    token; there is no unauthenticated mode (contrast: the experimental
    upload prefix keeps its own separate, stricter gate chain).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Request

from app.api.upload_auth import AuthError, DeviceRegistry
from app.db import ApiError


def _registry_path() -> Path:
    return Path(os.environ.get("WORKCAM_DEVICE_REGISTRY",
                               "data/uploads-experimental/devices.jsonl"))


def require_caller(request: Request) -> str:
    """Resolve the authenticated caller's DEVICE TOKEN (the stable
    identity key for ownership). Returns the token; raises the unified
    business error on any failure."""
    token = request.headers.get("X-Device-Token")
    if not token:
        raise ApiError(401, "unauthorized",
                       "X-Device-Token header required for /api/v1")
    registry = DeviceRegistry(_registry_path())
    device_id = registry.device_id_for_token(token)
    if device_id is None:
        raise ApiError(401, "unauthorized",
                       "device token is not enrolled")
    return token


def caller_device_id(token: str) -> str:
    registry = DeviceRegistry(_registry_path())
    return registry.device_id_for_token(token) or "unknown"
