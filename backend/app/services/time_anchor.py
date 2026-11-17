"""
Time Anchor Service for WC-W003

This module implements server-side time anchoring for offline records.

EXPERIMENTAL: This is a research prototype, NOT for production use.

Key Functions:
- create_anchor: Create a new time anchor for a batch of records
- verify_anchor: Verify an anchor receipt
- check_duplicate: Detect duplicate or conflicting anchor submissions
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

from app.models.time_anchor import (
    TimeAnchorReceipt,
    TimeAnchorRequest,
    TimeAnchorResponse,
    TimeAnchorVerification,
    TimeTrustLevel,
)


class TimeAnchorStore:
    """
    In-memory store for time anchors.

    WARNING: This is for testing only. Production needs persistent storage.
    """

    def __init__(self):
        self._anchors: dict[str, TimeAnchorReceipt] = {}
        self._batch_index: dict[str, str] = {}  # batch_id -> anchor_id

    def store(self, receipt: TimeAnchorReceipt) -> bool:
        """Store an anchor receipt. Returns False if duplicate."""
        if receipt.anchor_id in self._anchors:
            return False
        if receipt.batch_id in self._batch_index:
            return False
        self._anchors[receipt.anchor_id] = receipt
        self._batch_index[receipt.batch_id] = receipt.anchor_id
        return True

    def get_by_anchor_id(self, anchor_id: str) -> Optional[TimeAnchorReceipt]:
        """Get anchor by anchor_id."""
        return self._anchors.get(anchor_id)

    def get_by_batch_id(self, batch_id: str) -> Optional[TimeAnchorReceipt]:
        """Get anchor by batch_id."""
        anchor_id = self._batch_index.get(batch_id)
        if anchor_id:
            return self._anchors.get(anchor_id)
        return None

    def check_duplicate(self, batch_id: str, chain_head_hash: str, chain_tail_hash: str) -> tuple[bool, str]:
        """
        Check if this is a duplicate or conflicting submission.

        Returns:
            (is_duplicate, message)
        """
        existing = self.get_by_batch_id(batch_id)
        if not existing:
            return False, "New batch"

        # Check if content matches
        if existing.chain_head_hash == chain_head_hash and existing.chain_tail_hash == chain_tail_hash:
            return True, "Duplicate submission: same batch_id with identical content"
        else:
            return True, "CONFLICT: same batch_id with different content"


class TimeAnchorService:
    """
    Service for creating and verifying time anchors.

    Uses ECDSA P-256 for signing anchor receipts.
    """

    def __init__(self):
        # Generate test signing key
        # WARNING: This is for testing only. Production needs secure key management.
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        self._public_key = self._private_key.public_key()
        self._store = TimeAnchorStore()

    def get_public_key_pem(self) -> str:
        """Get the server's public key in PEM format."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    def create_anchor(self, request: TimeAnchorRequest) -> TimeAnchorResponse:
        """
        Create a time anchor for a batch of offline records.

        The server:
        1. Records the current time
        2. Generates a random challenge
        3. Signs the anchor data
        4. Returns the signed receipt

        IMPORTANT: The server time proves the records existed BEFORE this time,
        but does NOT prove when they were actually created.
        """
        # Check for duplicates
        is_dup, msg = self._store.check_duplicate(
            request.batch_id,
            request.chain_head_hash,
            request.chain_tail_hash,
        )
        if is_dup:
            raise ValueError(msg)

        # Generate anchor data
        anchor_id = f"anchor-{secrets.token_hex(16)}"
        server_received_at = datetime.now(timezone.utc).isoformat()
        challenge = secrets.token_hex(32)

        # Create data to sign
        sign_data = self._create_sign_data(
            anchor_id=anchor_id,
            batch_id=request.batch_id,
            server_received_at=server_received_at,
            challenge=challenge,
            chain_head_hash=request.chain_head_hash,
            chain_tail_hash=request.chain_tail_hash,
            record_count=request.record_count,
        )

        # Sign the data
        signature = self._sign(sign_data)

        # Create response
        response = TimeAnchorResponse(
            anchor_id=anchor_id,
            batch_id=request.batch_id,
            server_received_at=server_received_at,
            challenge=challenge,
            server_signature=signature,
            chain_head_hash=request.chain_head_hash,
            chain_tail_hash=request.chain_tail_hash,
            record_count=request.record_count,
        )

        # Store receipt
        receipt = TimeAnchorReceipt(
            anchor_id=anchor_id,
            batch_id=request.batch_id,
            server_received_at=server_received_at,
            challenge=challenge,
            server_signature=signature,
            chain_head_hash=request.chain_head_hash,
            chain_tail_hash=request.chain_tail_hash,
            record_count=request.record_count,
            device_reported_start_time=request.device_reported_start_time,
            device_reported_end_time=request.device_reported_end_time,
            stored_at=server_received_at,
        )
        self._store.store(receipt)

        return response

    def verify_anchor(self, receipt: TimeAnchorReceipt) -> TimeAnchorVerification:
        """
        Verify a time anchor receipt.

        Checks:
        1. Signature is valid
        2. Challenge matches stored value
        3. Anchor exists in store

        Returns verification result with trust level analysis.
        """
        warnings = []

        # Check if anchor exists
        stored = self._store.get_by_anchor_id(receipt.anchor_id)
        if not stored:
            return TimeAnchorVerification(
                valid=False,
                anchor_id=receipt.anchor_id,
                server_received_at="",
                signature_valid=False,
                challenge_matches=False,
                trust_level=TimeTrustLevel.UNVERIFIED,
                message="Anchor not found in store",
                warnings=["Anchor may have been deleted or never existed"],
            )

        # Verify signature
        sign_data = self._create_sign_data(
            anchor_id=receipt.anchor_id,
            batch_id=receipt.batch_id,
            server_received_at=receipt.server_received_at,
            challenge=receipt.challenge,
            chain_head_hash=receipt.chain_head_hash,
            chain_tail_hash=receipt.chain_tail_hash,
            record_count=receipt.record_count,
        )
        signature_valid = self._verify(sign_data, receipt.server_signature)

        # Verify challenge
        challenge_matches = stored.challenge == receipt.challenge

        # Determine trust level
        if signature_valid and challenge_matches:
            trust_level = TimeTrustLevel.SERVER_ANCHORED
            message = "Anchor is valid. Records existed before server_received_at."
            warnings.append(
                "Server time proves records existed BEFORE server_received_at, "
                "but does NOT prove when they were actually created."
            )
            warnings.append(
                "Device-reported times are untrusted and may be falsified."
            )
        else:
            trust_level = TimeTrustLevel.UNVERIFIED
            message = "Anchor verification failed"
            if not signature_valid:
                warnings.append("Signature is invalid")
            if not challenge_matches:
                warnings.append("Challenge does not match stored value")

        return TimeAnchorVerification(
            valid=signature_valid and challenge_matches,
            anchor_id=receipt.anchor_id,
            server_received_at=receipt.server_received_at,
            signature_valid=signature_valid,
            challenge_matches=challenge_matches,
            trust_level=trust_level,
            message=message,
            warnings=warnings,
        )

    def _create_sign_data(
        self,
        anchor_id: str,
        batch_id: str,
        server_received_at: str,
        challenge: str,
        chain_head_hash: str,
        chain_tail_hash: str,
        record_count: int,
    ) -> bytes:
        """Create deterministic data to sign."""
        # Use canonical format for signing
        data = (
            f"WorkCam-TimeAnchor-v1\n"
            f"anchor_id={anchor_id}\n"
            f"batch_id={batch_id}\n"
            f"server_received_at={server_received_at}\n"
            f"challenge={challenge}\n"
            f"chain_head_hash={chain_head_hash}\n"
            f"chain_tail_hash={chain_tail_hash}\n"
            f"record_count={record_count}\n"
        )
        return data.encode("utf-8")

    def _sign(self, data: bytes) -> str:
        """Sign data and return base64-encoded signature."""
        import base64

        signature = self._private_key.sign(data, ec.ECDSA(hashes.SHA256()))
        return base64.b64encode(signature).decode("utf-8")

    def _verify(self, data: bytes, signature_b64: str) -> bool:
        """Verify signature."""
        import base64

        try:
            signature = base64.b64decode(signature_b64)
            self._public_key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
            return True
        except Exception:
            return False


# Global service instance
_service: Optional[TimeAnchorService] = None


def get_time_anchor_service() -> TimeAnchorService:
    """Get the global time anchor service instance."""
    global _service
    if _service is None:
        _service = TimeAnchorService()
    return _service


def reset_time_anchor_service() -> None:
    """Reset the global service (for testing)."""
    global _service
    _service = None
