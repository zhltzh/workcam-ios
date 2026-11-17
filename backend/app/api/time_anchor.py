"""
Time Anchor API Endpoints for WC-W003

EXPERIMENTAL: These endpoints are for research and testing only.
NOT for production use.

Endpoints:
- POST /time-anchor/anchor: Create a new time anchor
- POST /time-anchor/verify: Verify an anchor receipt
- GET /time-anchor/public-key: Get server's public key
- GET /time-anchor/receipt/{anchor_id}: Get anchor receipt by ID
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.time_anchor import (
    TimeAnchorReceipt,
    TimeAnchorRequest,
    TimeAnchorResponse,
    TimeAnchorVerification,
)
from app.services.time_anchor import get_time_anchor_service

router = APIRouter(prefix="/time-anchor", tags=["time-anchor-experimental"])


@router.post("/anchor", response_model=TimeAnchorResponse)
async def create_anchor(request: TimeAnchorRequest):
    """
    Create a time anchor for a batch of offline records.

    EXPERIMENTAL: This endpoint is for research only.

    The server will:
    1. Record the current time
    2. Generate a random challenge
    3. Sign the anchor data
    4. Return the signed receipt

    IMPORTANT: The server time proves the records existed BEFORE this time,
    but does NOT prove when they were actually created.

    Returns:
        TimeAnchorResponse with signed receipt

    Raises:
        HTTPException: If batch_id is duplicate or content conflicts
    """
    service = get_time_anchor_service()
    try:
        response = service.create_anchor(request)
        return response
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/verify", response_model=TimeAnchorVerification)
async def verify_anchor(receipt: TimeAnchorReceipt):
    """
    Verify a time anchor receipt.

    EXPERIMENTAL: This endpoint is for research only.

    Checks:
    1. Signature is valid
    2. Challenge matches stored value
    3. Anchor exists in store

    Returns:
        TimeAnchorVerification with trust level analysis
    """
    service = get_time_anchor_service()
    verification = service.verify_anchor(receipt)
    return verification


@router.get("/public-key")
async def get_public_key():
    """
    Get the server's public key for verifying anchor signatures.

    EXPERIMENTAL: This endpoint is for research only.

    Returns:
        Public key in PEM format
    """
    service = get_time_anchor_service()
    return {
        "public_key_pem": service.get_public_key_pem(),
        "algorithm": "ECDSA",
        "curve": "P-256",
        "warning": "This is a TEST key. Do not use in production.",
    }


@router.get("/receipt/{anchor_id}", response_model=TimeAnchorReceipt)
async def get_receipt(anchor_id: str):
    """
    Get an anchor receipt by anchor_id.

    EXPERIMENTAL: This endpoint is for research only.

    Returns:
        TimeAnchorReceipt if found

    Raises:
        HTTPException: If anchor not found
    """
    service = get_time_anchor_service()
    receipt = service._store.get_by_anchor_id(anchor_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Anchor not found")
    return receipt


@router.get("/health")
async def health():
    """Health check for time anchor service."""
    return {
        "status": "ok",
        "service": "workcam-time-anchor-experimental",
        "warning": "This is an EXPERIMENTAL service for research only.",
    }
