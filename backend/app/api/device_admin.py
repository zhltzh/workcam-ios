"""WC-W009 P0 - device administration endpoints.

/api/v1/devices/* - the CONTROLLED enrollment surface. Enrollment
requires the out-of-band enrollment code; revocation and status need an
ADMIN credential (the enrollment code doubles as the admin secret for
this MVP stage - production needs a real operator RBAC, listed as a
known gap).

Device isolation: these endpoints manage DEVICES, not business data -
a caller can never enroll/revoke ANOTHER caller's device through them
(the admin code is the gate, and revoke/status are admin-only).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.db import ApiError
from app.services import device_identity

router = APIRouter(prefix="/api/v1/devices", tags=["device-admin"])


class EnrollRequest(BaseModel):
    public_key_b64: str
    device_id: str = Field(min_length=1, max_length=128)
    enrollment_code: str = Field(min_length=1, max_length=256)


class RevokeRequest(BaseModel):
    admin_code: str = Field(min_length=1, max_length=256)


def _require_admin(request: Request) -> None:
    """Admin actions are gated by the SAME out-of-band code as
    enrollment (MVP simplification - see docs/w009-security.md for the
    production gap: real operator RBAC)."""
    code = os.environ.get("WORKCAM_ENROLLMENT_CODE") or ""
    if not code or request.headers.get("X-Admin-Code") != code:
        raise ApiError(401, "unauthorized",
                       "admin actions require X-Admin-Code")


import os  # noqa: E402


@router.post("/enroll", status_code=201)
async def enroll(body: EnrollRequest):
    token = device_identity.enroll_device(
        body.public_key_b64, body.device_id, body.enrollment_code)
    return {"device_token": token,
            "key_fingerprint": device_identity.token_fingerprint(token),
            "note": "store this token on the device; it is the X-Device-"
                    "Token credential for /api/v1"}


@router.post("/{device_token}/revoke")
async def revoke(device_token: str, body: RevokeRequest,
                request: Request):
    _require_admin(request)
    result = device_identity.revoke_device(device_token)
    return result


@router.get("/{device_token}")
async def status(device_token: str, request: Request):
    _require_admin(request)
    return device_identity.device_status(device_token)


@router.post("/challenge")
async def challenge(request: Request):
    """Prove-of-possession flow (W005 semantics, DB-backed nonce):
    issue a single-use nonce for the enrolled device to sign."""
    token = request.headers.get("X-Device-Token")
    if not token:
        raise ApiError(401, "unauthorized", "X-Device-Token required")
    device_identity.resolve_device(token)      # enrollment + revocation
    nonce = device_identity.issue_challenge(token)
    return {"nonce": nonce, "expires_s": 60}


@router.post("/challenge/verify")
async def challenge_verify(body: dict, request: Request):
    """{device_token, nonce, signature_b64} -> verifies with the
    ENROLLED public key. Private keys never leave the device."""
    try:
        device_id = device_identity.verify_challenge_signature(
            body.get("device_token", ""), body.get("nonce", ""),
            body.get("signature_b64", ""))
    except ApiError:
        raise
    return {"device_id": device_id, "verified": True}
