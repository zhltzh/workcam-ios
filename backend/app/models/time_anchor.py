"""
Time Anchor Models for WC-W003

This module defines data structures for offline time anchoring experiments.
These are EXPERIMENTAL and NOT for production use.

Key Concepts:
- TimeAnchor: Server-side record of when a batch of offline records was received
- TimeAnchorReceipt: Signed receipt that client stores for later verification
- TimeTrustLevel: Classification of how much we can trust the time evidence
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TimeTrustLevel(str, Enum):
    """
    Classification of time evidence trustworthiness.

    SERVER_ANCHORED: Server recorded receipt time. Proves records existed
                     before this time, but not when they were created.

    DEVICE_REPORTED: Only device-reported time. Cannot be trusted as
                     accurate without server corroboration.

    UNVERIFIED: No time evidence available.
    """
    SERVER_ANCHORED = "server_anchored"
    DEVICE_REPORTED = "device_reported"
    UNVERIFIED = "unverified"


class TimeAnchorRequest(BaseModel):
    """
    Client request to anchor a batch of offline records.

    The client submits:
    - batch_id: Unique identifier for this batch
    - chain_head_hash: Hash of the first record in the chain
    - chain_tail_hash: Hash of the last record in the chain
    - record_count: Number of records in the batch
    - device_reported_start_time: Device's claim of when first record was created
    - device_reported_end_time: Device's claim of when last record was created
    """
    batch_id: str = Field(..., max_length=256)
    chain_head_hash: str = Field(..., min_length=64, max_length=64)
    chain_tail_hash: str = Field(..., min_length=64, max_length=64)
    record_count: int = Field(..., gt=0, le=10000)
    device_reported_start_time: str
    device_reported_end_time: str


class TimeAnchorResponse(BaseModel):
    """
    Server response after anchoring a batch.

    The server provides:
    - anchor_id: Unique identifier for this anchor
    - server_received_at: Server's timestamp of when batch was received
    - challenge: Random value to prevent replay attacks
    - server_signature: Signature over the anchor data

    IMPORTANT: server_received_at proves the records existed BEFORE this time,
    but does NOT prove when they were actually created.
    """
    anchor_id: str
    batch_id: str
    server_received_at: str
    challenge: str
    server_signature: str
    chain_head_hash: str
    chain_tail_hash: str
    record_count: int


class TimeAnchorReceipt(BaseModel):
    """
    Receipt stored by client for later verification.

    Contains all information needed to verify the anchor later.
    """
    anchor_id: str
    batch_id: str
    server_received_at: str
    challenge: str
    server_signature: str
    chain_head_hash: str
    chain_tail_hash: str
    record_count: int
    device_reported_start_time: str
    device_reported_end_time: str
    stored_at: str


class TimeAnchorVerification(BaseModel):
    """
    Result of verifying a time anchor receipt.
    """
    valid: bool
    anchor_id: str
    server_received_at: str
    signature_valid: bool
    challenge_matches: bool
    trust_level: TimeTrustLevel
    message: str
    warnings: list[str] = []


class OfflineSession(BaseModel):
    """
    Simulation of an offline session.

    Tracks:
    - session_id: Unique identifier
    - start_time: When session started (device time)
    - end_time: When session ended (device time)
    - records_created: Number of records created during session
    - actual_start_time: Real start time (for simulation)
    - actual_end_time: Real end time (for simulation)
    - device_time_offset: How much device clock differs from real time
    """
    session_id: str
    start_time: str
    end_time: str
    records_created: int
    actual_start_time: Optional[str] = None
    actual_end_time: Optional[str] = None
    device_time_offset_seconds: int = 0


class TimeTrustAnalysis(BaseModel):
    """
    Analysis of time trust for a batch of records.

    Explains what can and cannot be proven about the time evidence.
    """
    batch_id: str
    can_prove: list[str]
    cannot_prove: list[str]
    trust_level: TimeTrustLevel
    risks: list[str]
    recommendations: list[str]
