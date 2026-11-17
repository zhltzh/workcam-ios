"""
Time Anchor Service for WC-W003-R2

This module implements server-side time anchoring for offline records.

EXPERIMENTAL: This is a research prototype, NOT for production use.

Key Functions:
- create_anchor: Create a new time anchor for a batch of records
- verify_anchor: Verify an anchor receipt
- check_duplicate: Detect duplicate or conflicting anchor submissions
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.models.time_anchor import (
    TimeAnchorReceipt,
    TimeAnchorRequest,
    TimeAnchorResponse,
    TimeAnchorVerification,
    TimeTrustLevel,
    VerificationStatus,
)


class PersistenceError(Exception):
    """Raised when persistence operations fail."""
    pass


class TimeAnchorStore:
    """
    In-memory store for time anchors with optional file persistence.

    WARNING: This is for testing only. Production needs proper database.
    JSON file storage does NOT provide production-grade transaction guarantees.
    """

    def __init__(self, persist_path: Optional[Path] = None):
        self._anchors: dict[str, TimeAnchorReceipt] = {}
        self._batch_index: dict[str, str] = {}  # batch_id -> anchor_id
        self._persist_path = persist_path
        if persist_path and persist_path.exists():
            self._load_from_file()

    def _load_from_file(self):
        """
        Load anchors from persistent storage.

        Raises:
            PersistenceError: If file is corrupted or cannot be read
        """
        if not self._persist_path:
            return

        try:
            with open(self._persist_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, list):
                raise PersistenceError(
                    f"Corrupted storage file: expected list, got {type(data).__name__}"
                )

            for i, receipt_data in enumerate(data):
                if not isinstance(receipt_data, dict):
                    raise PersistenceError(
                        f"Corrupted storage file: item {i} is not a dict"
                    )
                try:
                    receipt = TimeAnchorReceipt(**receipt_data)
                    self._anchors[receipt.anchor_id] = receipt
                    self._batch_index[receipt.batch_id] = receipt.anchor_id
                except Exception as e:
                    raise PersistenceError(
                        f"Corrupted storage file: cannot parse item {i}: {e}"
                    )

        except json.JSONDecodeError as e:
            raise PersistenceError(f"Corrupted storage file: invalid JSON: {e}")
        except FileNotFoundError:
            # File doesn't exist yet, start with empty store
            pass
        except Exception as e:
            raise PersistenceError(f"Failed to load storage file: {e}")

    def _save_to_file(self):
        """
        Save anchors to persistent storage using atomic write.

        Uses temporary file + atomic replace to prevent corruption.

        Raises:
            PersistenceError: If save fails
        """
        if not self._persist_path:
            return

        try:
            # Ensure parent directory exists
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)

            # Prepare data
            data = [r.model_dump() for r in self._anchors.values()]

            # Write to temporary file first
            temp_fd, temp_path = tempfile.mkstemp(
                dir=self._persist_path.parent,
                suffix='.tmp',
                prefix='.anchors_'
            )

            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())

                # Atomic replace
                os.replace(temp_path, self._persist_path)

            except Exception as e:
                # Clean up temp file on error
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
                raise PersistenceError(f"Failed to save storage file: {e}")

        except PersistenceError:
            raise
        except Exception as e:
            raise PersistenceError(f"Failed to save storage file: {e}")

    def store(self, receipt: TimeAnchorReceipt) -> bool:
        """
        Store an anchor receipt.

        Returns:
            True if stored successfully, False if duplicate

        Raises:
            PersistenceError: If persistence fails
        """
        if receipt.anchor_id in self._anchors:
            return False
        if receipt.batch_id in self._batch_index:
            return False

        self._anchors[receipt.anchor_id] = receipt
        self._batch_index[receipt.batch_id] = receipt.anchor_id

        # Save to file (will raise PersistenceError on failure)
        self._save_to_file()

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

    def check_duplicate(
        self,
        batch_id: str,
        chain_head_hash: str,
        chain_tail_hash: str,
        record_count: int,
    ) -> tuple[bool, str, Optional[TimeAnchorReceipt]]:
        """
        Check if this is a duplicate or conflicting submission.

        Returns:
            (is_duplicate, message, existing_receipt)
        """
        existing = self.get_by_batch_id(batch_id)
        if not existing:
            return False, "New batch", None

        # Check if ALL batch identity fields match
        if (
            existing.chain_head_hash == chain_head_hash
            and existing.chain_tail_hash == chain_tail_hash
            and existing.record_count == record_count
        ):
            return True, "Duplicate submission: identical content", existing
        else:
            # Build detailed conflict message
            conflicts = []
            if existing.chain_head_hash != chain_head_hash:
                conflicts.append("chain_head_hash")
            if existing.chain_tail_hash != chain_tail_hash:
                conflicts.append("chain_tail_hash")
            if existing.record_count != record_count:
                conflicts.append("record_count")
            return True, f"CONFLICT: different {', '.join(conflicts)}", existing


class KeyRegistry:
    """
    Registry for managing multiple signing keys.

    Supports key rotation by maintaining multiple key_id -> public_key mappings.
    """

    def __init__(self):
        self._keys: dict[str, ec.EllipticCurvePublicKey] = {}

    def register_key(self, key_id: str, public_key: ec.EllipticCurvePublicKey):
        """Register a public key with a key_id."""
        self._keys[key_id] = public_key

    def get_public_key(self, key_id: str) -> Optional[ec.EllipticCurvePublicKey]:
        """Get public key by key_id. Returns None if not found."""
        return self._keys.get(key_id)

    def has_key(self, key_id: str) -> bool:
        """Check if key_id is registered."""
        return key_id in self._keys


class TimeAnchorService:
    """
    Service for creating and verifying time anchors.

    Uses ECDSA P-256 for signing anchor receipts.
    Supports fixed key loading for test reproducibility.
    Supports multiple keys for key rotation.
    """

    # Protocol version - increment when changing signed fields
    PROTOCOL_VERSION = "v2"

    def __init__(
        self,
        key_id: str = "test-key-001",
        persist_path: Optional[Path] = None,
        trusted_keys: Optional[dict[str, ec.EllipticCurvePublicKey]] = None,
    ):
        self._key_id = key_id
        self._private_key = self._load_or_generate_key(key_id)
        self._public_key = self._private_key.public_key()
        self._store = TimeAnchorStore(persist_path)

        # Key registry for verification
        self._key_registry = KeyRegistry()

        # Register current key
        self._key_registry.register_key(key_id, self._public_key)

        # Register additional trusted keys (for key rotation)
        if trusted_keys:
            for kid, pubkey in trusted_keys.items():
                self._key_registry.register_key(kid, pubkey)

    def _load_or_generate_key(self, key_id: str) -> ec.EllipticCurvePrivateKey:
        """
        Load fixed key for test environment or generate new key.

        For test reproducibility, checks for key file in test-keys directory.
        """
        # Check for test key file
        test_key_dir = Path(__file__).parent.parent.parent / "test-keys"
        key_file = test_key_dir / f"{key_id}.pem"

        if key_file.exists():
            # Load existing test key
            with open(key_file, 'rb') as f:
                return serialization.load_pem_private_key(f.read(), password=None)
        else:
            # Generate new key
            private_key = ec.generate_private_key(ec.SECP256R1())

            # Save for test reproducibility (only in test environment)
            if os.environ.get("WORKCAM_ENV") != "production":
                test_key_dir.mkdir(parents=True, exist_ok=True)
                with open(key_file, 'wb') as f:
                    f.write(private_key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.PKCS8,
                        encryption_algorithm=serialization.NoEncryption(),
                    ))

            return private_key

    def get_key_id(self) -> str:
        """Get the current key identifier."""
        return self._key_id

    def get_public_key_pem(self) -> str:
        """Get the server's current public key in PEM format."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    def register_trusted_key(self, key_id: str, public_key_pem: str):
        """
        Register a trusted public key for verification.

        Used for key rotation - allows verifying receipts signed by old keys.
        """
        public_key = serialization.load_pem_public_key(
            public_key_pem.encode("utf-8")
        )
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise ValueError("Only EC public keys are supported")
        self._key_registry.register_key(key_id, public_key)

    def _validate_field(self, value: str, field_name: str) -> str:
        """
        Validate and sanitize a field value for signing.

        Rejects control characters and newlines to prevent injection attacks.

        Raises:
            ValueError: If field contains invalid characters
        """
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")

        # Check for control characters (including newlines)
        for i, char in enumerate(value):
            if ord(char) < 32 or ord(char) == 127:
                raise ValueError(
                    f"{field_name} contains control character at position {i}: "
                    f"0x{ord(char):02x}"
                )

        # Check for field separator (to prevent field injection)
        if '=' in value or '\n' in value:
            raise ValueError(f"{field_name} contains invalid characters")

        return value

    def _create_sign_data(
        self,
        anchor_id: str,
        key_id: str,
        batch_id: str,
        server_received_at: str,
        challenge: str,
        chain_head_hash: str,
        chain_tail_hash: str,
        record_count: int,
        verification_status: VerificationStatus,
    ) -> bytes:
        """
        Create deterministic data to sign.

        Includes verification_status to prevent clients from elevating their
        verification level by modifying the unsigned field.

        All string fields are validated to prevent injection attacks.

        Protocol version is included to allow future changes without
        breaking compatibility with old receipts.
        """
        # Validate all string fields
        anchor_id = self._validate_field(anchor_id, "anchor_id")
        key_id = self._validate_field(key_id, "key_id")
        batch_id = self._validate_field(batch_id, "batch_id")
        server_received_at = self._validate_field(server_received_at, "server_received_at")
        challenge = self._validate_field(challenge, "challenge")
        chain_head_hash = self._validate_field(chain_head_hash, "chain_head_hash")
        chain_tail_hash = self._validate_field(chain_tail_hash, "chain_tail_hash")
        verification_status_str = self._validate_field(
            verification_status.value, "verification_status"
        )

        # Validate record_count
        if not isinstance(record_count, int) or record_count < 0:
            raise ValueError("record_count must be a non-negative integer")

        # Create canonical signed data
        # Format: protocol_version\nfield1=value1\nfield2=value2\n...
        data = (
            f"WorkCam-TimeAnchor-{self.PROTOCOL_VERSION}\n"
            f"anchor_id={anchor_id}\n"
            f"key_id={key_id}\n"
            f"batch_id={batch_id}\n"
            f"server_received_at={server_received_at}\n"
            f"challenge={challenge}\n"
            f"chain_head_hash={chain_head_hash}\n"
            f"chain_tail_hash={chain_tail_hash}\n"
            f"record_count={record_count}\n"
            f"verification_status={verification_status_str}\n"
        )
        return data.encode("utf-8")

    def create_anchor(self, request: TimeAnchorRequest) -> TimeAnchorResponse:
        """
        Create a time anchor for a batch of offline records.

        The server:
        1. Records the current time
        2. Generates a random challenge
        3. Signs the anchor data (including verification_status)
        4. Returns the signed receipt

        IMPORTANT: By default, the server only provides DIGEST_ANCHORED status.
        This means it signed the submitted hashes, but did NOT verify the
        actual records, photos, or chain integrity.

        For higher verification status, the client must submit actual content.
        """
        # Check for duplicates
        is_dup, msg, existing = self._store.check_duplicate(
            request.batch_id,
            request.chain_head_hash,
            request.chain_tail_hash,
            request.record_count,
        )

        if is_dup and existing:
            if "identical" in msg.lower():
                # Idempotent: return existing receipt
                return TimeAnchorResponse(
                    anchor_id=existing.anchor_id,
                    key_id=existing.key_id,
                    batch_id=existing.batch_id,
                    server_received_at=existing.server_received_at,
                    challenge=existing.challenge,
                    server_signature=existing.server_signature,
                    chain_head_hash=existing.chain_head_hash,
                    chain_tail_hash=existing.chain_tail_hash,
                    record_count=existing.record_count,
                    verification_status=existing.verification_status,
                )
            else:
                # Conflict
                raise ValueError(msg)

        # Generate anchor data
        anchor_id = f"anchor-{secrets.token_hex(16)}"
        server_received_at = datetime.now(timezone.utc).isoformat()
        challenge = secrets.token_hex(32)

        # Default verification status: only digest anchored
        verification_status = VerificationStatus.DIGEST_ANCHORED

        # Create data to sign (includes verification_status)
        sign_data = self._create_sign_data(
            anchor_id=anchor_id,
            key_id=self._key_id,
            batch_id=request.batch_id,
            server_received_at=server_received_at,
            challenge=challenge,
            chain_head_hash=request.chain_head_hash,
            chain_tail_hash=request.chain_tail_hash,
            record_count=request.record_count,
            verification_status=verification_status,
        )

        # Sign the data
        signature = self._sign(sign_data)

        # Create response
        response = TimeAnchorResponse(
            anchor_id=anchor_id,
            key_id=self._key_id,
            batch_id=request.batch_id,
            server_received_at=server_received_at,
            challenge=challenge,
            server_signature=signature,
            chain_head_hash=request.chain_head_hash,
            chain_tail_hash=request.chain_tail_hash,
            record_count=request.record_count,
            verification_status=verification_status,
        )

        # Store receipt
        receipt = TimeAnchorReceipt(
            anchor_id=anchor_id,
            key_id=self._key_id,
            batch_id=request.batch_id,
            server_received_at=server_received_at,
            challenge=challenge,
            server_signature=signature,
            chain_head_hash=request.chain_head_hash,
            chain_tail_hash=request.chain_tail_hash,
            record_count=request.record_count,
            device_reported_start_time=request.device_reported_start_time,
            device_reported_end_time=request.device_reported_end_time,
            verification_status=verification_status,
            stored_at=server_received_at,
        )
        self._store.store(receipt)

        return response

    def verify_anchor(
        self,
        receipt: TimeAnchorReceipt,
        check_storage: bool = True,
    ) -> TimeAnchorVerification:
        """
        Verify a time anchor receipt.

        Args:
            receipt: The receipt to verify
            check_storage: If True, also check against server storage.
                          If False, only verify signature offline.

        Checks:
        1. key_id is registered (reject unknown keys)
        2. Signature is valid using the correct public key
        3. verification_status matches signed value (prevent elevation)
        4. All signed fields match stored values
        5. Receipt exists in server storage (requires server)

        Returns verification result with clear distinction between
        signed and unsigned fields.
        """
        warnings = []

        # Check if key_id is registered
        public_key = self._key_registry.get_public_key(receipt.key_id)
        if not public_key:
            return TimeAnchorVerification(
                valid=False,
                anchor_id=receipt.anchor_id,
                key_id=receipt.key_id,
                server_received_at=receipt.server_received_at,
                signature_valid=False,
                challenge_matches=False,
                storage_matches=False,
                verification_status=receipt.verification_status,
                trust_level=TimeTrustLevel.UNVERIFIED,
                message=f"Unknown key_id: {receipt.key_id}",
                warnings=[
                    f"key_id '{receipt.key_id}' is not registered. "
                    "Cannot verify signature without trusted public key."
                ],
                signed_fields_verified=False,
                unsigned_fields_note="",
            )

        # Verify signature using the correct public key
        sign_data = self._create_sign_data(
            anchor_id=receipt.anchor_id,
            key_id=receipt.key_id,
            batch_id=receipt.batch_id,
            server_received_at=receipt.server_received_at,
            challenge=receipt.challenge,
            chain_head_hash=receipt.chain_head_hash,
            chain_tail_hash=receipt.chain_tail_hash,
            record_count=receipt.record_count,
            verification_status=receipt.verification_status,
        )
        signature_valid = self._verify_with_key(sign_data, receipt.server_signature, public_key)

        # Check storage (requires server)
        storage_matches = False
        verification_status_matches = False

        if check_storage:
            stored = self._store.get_by_anchor_id(receipt.anchor_id)
            if stored:
                # Check all signed fields match
                storage_matches = (
                    stored.batch_id == receipt.batch_id
                    and stored.server_received_at == receipt.server_received_at
                    and stored.challenge == receipt.challenge
                    and stored.chain_head_hash == receipt.chain_head_hash
                    and stored.chain_tail_hash == receipt.chain_tail_hash
                    and stored.record_count == receipt.record_count
                )

                # Check verification_status matches (prevent elevation attack)
                verification_status_matches = (
                    stored.verification_status == receipt.verification_status
                )
            else:
                warnings.append("Anchor not found in server storage")
        else:
            storage_matches = True  # Not checking storage
            verification_status_matches = True  # Assume matches in offline mode
            warnings.append("Storage verification skipped (offline mode)")

        # Determine validity
        valid = signature_valid and storage_matches and verification_status_matches

        # Build message
        if valid:
            message = "Receipt is valid. Server signed this digest at server_received_at."
            warnings.append(
                "Server signature proves the digest existed at server_received_at, "
                "but does NOT prove when actual photos were taken."
            )
            warnings.append(
                "Device-reported times are unsigned and untrusted."
            )
            if receipt.verification_status == VerificationStatus.DIGEST_ANCHORED:
                warnings.append(
                    "Verification status is DIGEST_ANCHORED: server only signed the "
                    "submitted hashes. It did NOT verify actual records, photos, or chain."
                )
        else:
            message = "Receipt verification failed"
            if not signature_valid:
                warnings.append("Signature is invalid")
            if not storage_matches and check_storage:
                warnings.append("Receipt does not match server storage")
            if not verification_status_matches:
                warnings.append(
                    "verification_status mismatch: client may have attempted to elevate "
                    "verification level. This is a security violation."
                )

        # Determine trust level
        if valid:
            trust_level = TimeTrustLevel.SERVER_ANCHORED
        else:
            trust_level = TimeTrustLevel.UNVERIFIED

        # Note about unsigned fields
        unsigned_note = (
            "Unsigned fields (device_reported_start_time, device_reported_end_time, "
            "stored_at) are NOT included in the signature and cannot be verified offline. "
            "verification_status IS signed to prevent elevation attacks."
        )

        return TimeAnchorVerification(
            valid=valid,
            anchor_id=receipt.anchor_id,
            key_id=receipt.key_id,
            server_received_at=receipt.server_received_at,
            signature_valid=signature_valid,
            challenge_matches=True,  # Challenge is part of signed data
            storage_matches=storage_matches,
            verification_status=receipt.verification_status,
            trust_level=trust_level,
            message=message,
            warnings=warnings,
            signed_fields_verified=signature_valid and verification_status_matches,
            unsigned_fields_note=unsigned_note,
        )

    def _sign(self, data: bytes) -> str:
        """Sign data and return base64-encoded signature."""
        import base64

        signature = self._private_key.sign(data, ec.ECDSA(hashes.SHA256()))
        return base64.b64encode(signature).decode("utf-8")

    def _verify_with_key(
        self,
        data: bytes,
        signature_b64: str,
        public_key: ec.EllipticCurvePublicKey,
    ) -> bool:
        """Verify signature using a specific public key."""
        import base64

        try:
            signature = base64.b64decode(signature_b64)
            public_key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
            return True
        except Exception:
            return False


# Global service instance
_service: Optional[TimeAnchorService] = None


def get_time_anchor_service() -> TimeAnchorService:
    """Get the global time anchor service instance."""
    global _service
    if _service is None:
        # Check for persistence path
        persist_dir = Path(__file__).parent.parent.parent / "data" / "time-anchors"
        persist_path = persist_dir / "anchors.json" if os.environ.get("WORKCAM_PERSIST") else None

        key_id = os.environ.get("WORKCAM_KEY_ID", "test-key-001")
        _service = TimeAnchorService(key_id=key_id, persist_path=persist_path)
    return _service


def reset_time_anchor_service() -> None:
    """Reset the global service (for testing)."""
    global _service
    _service = None
