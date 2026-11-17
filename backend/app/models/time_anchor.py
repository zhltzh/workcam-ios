"""
Time Anchor Models for WC-W003

This module defines data structures for offline time anchoring experiments.
These are EXPERIMENTAL and NOT for production use.

Key Concepts:
- TimeAnchor: Server-side record of when a batch of offline records was received
- TimeAnchorReceipt: Signed receipt that client stores for later verification
- TimeTrustLevel: Classification of how much we can trust the time evidence
- VerificationStatus: What the server actually verified vs what it only signed
"""

from __future__ import annotations

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


class VerificationStatus(str, Enum):
    """
    What the server actually verified vs what it only signed.

    IMPORTANT: The server signature only proves it received and signed
    the submitted digest. It does NOT prove the server verified the
    actual content unless explicitly stated.

    DIGEST_ANCHORED: Server signed the submitted digest (hashes, count, times).
                     This is the MINIMUM guarantee. The server did NOT verify
                     the actual records, photos, or chain integrity.

    CHAIN_VERIFIED: Server actually verified the complete record chain.
                    This requires the client to submit all records, not just
                    the head/tail hashes.

    PHOTO_VERIFIED: Server actually verified photo content matches hashes.
                    This requires the client to submit actual photos.

    RECORD_COUNT_VERIFIED: Server actually counted and verified each record.
                           This requires the client to submit all records.
    """
    DIGEST_ANCHORED = "digest_anchored"
    CHAIN_VERIFIED = "chain_verified"
    PHOTO_VERIFIED = "photo_verified"
    RECORD_COUNT_VERIFIED = "record_count_verified"


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

    IMPORTANT: Submitting only hashes means the server can only provide
    DIGEST_ANCHORED status. For CHAIN_VERIFIED or PHOTO_VERIFIED, the
    client must submit actual records/photos.
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
    - key_id: Identifier for the signing key (for key rotation)
    - server_received_at: Server's timestamp of when batch was received
    - challenge: Random value to prevent replay attacks
    - server_signature: Signature over the anchor data
    - verification_status: What the server actually verified

    SIGNED FIELDS (included in signature):
    - anchor_id, batch_id, server_received_at, challenge
    - chain_head_hash, chain_tail_hash, record_count

    UNSIGNED FIELDS (not included in signature):
    - device_reported_start_time, device_reported_end_time
    - verification_status

    IMPORTANT: server_received_at proves the digest existed BEFORE this time,
    but does NOT prove when the actual photos were taken.
    """
    anchor_id: str
    key_id: str
    batch_id: str
    server_received_at: str
    challenge: str
    server_signature: str
    chain_head_hash: str
    chain_tail_hash: str
    record_count: int
    verification_status: VerificationStatus


class TimeAnchorReceipt(BaseModel):
    """
    Receipt stored by client for later verification.

    Contains all information needed to verify the anchor later.

    SIGNED FIELDS (can be verified offline with server public key):
    - anchor_id, key_id, batch_id, server_received_at, challenge
    - chain_head_hash, chain_tail_hash, record_count

    UNSIGNED FIELDS (cannot be verified offline):
    - device_reported_start_time, device_reported_end_time
    - verification_status, stored_at
    """
    anchor_id: str
    key_id: str
    batch_id: str
    server_received_at: str
    challenge: str
    server_signature: str
    chain_head_hash: str
    chain_tail_hash: str
    record_count: int
    device_reported_start_time: str
    device_reported_end_time: str
    verification_status: VerificationStatus
    stored_at: str


class TimeAnchorVerification(BaseModel):
    """
    Result of verifying a time anchor receipt.

    This distinguishes between:
    1. Signature verification (can be done offline with public key)
    2. Storage verification (requires checking server records)
    """
    valid: bool
    anchor_id: str
    key_id: str
    server_received_at: str
    signature_valid: bool
    challenge_matches: bool
    storage_matches: bool  # Does receipt match server storage?
    verification_status: VerificationStatus
    trust_level: TimeTrustLevel
    message: str
    warnings: list[str] = []

    # What was actually verified
    signed_fields_verified: bool  # All signed fields match
    unsigned_fields_note: str  # Explanation of unsigned fields


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
    - record_times: Actual creation time for each record (for 72h simulation)
    """
    session_id: str
    start_time: str
    end_time: str
    records_created: int
    actual_start_time: Optional[str] = None
    actual_end_time: Optional[str] = None
    device_time_offset_seconds: int = 0
    record_times: Optional[list[str]] = None  # Actual time for each record


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
    verification_status: VerificationStatus
