from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Protocol

from app.models.capture import (
    KeyRegistrationResult,
    KeyRegistrationStatus,
    RecordStoreEntry,
)


class PublicKeyRegistry(Protocol):
    def lookup(self, public_key_b64: str) -> KeyRegistrationResult: ...
    def register(self, public_key_b64: str, device_id: str) -> KeyRegistrationResult: ...


def compute_public_key_hash(public_key_b64: str) -> str:
    return hashlib.sha256(public_key_b64.encode("utf-8")).hexdigest()[:32]


def compute_manifest_hash(manifest_dict: dict) -> str:
    import json
    canonical = json.dumps(manifest_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InMemoryKeyRegistry:
    def __init__(self) -> None:
        self._keys: dict[str, dict] = {}
        self._records: dict[str, RecordStoreEntry] = {}

    def lookup(self, public_key_b64: str) -> KeyRegistrationResult:
        key_hash = compute_public_key_hash(public_key_b64)
        if key_hash in self._keys:
            entry = self._keys[key_hash]
            return KeyRegistrationResult(
                public_key_hash=key_hash,
                registration_status=KeyRegistrationStatus.REGISTERED,
                registered_at=entry["registered_at"],
                device_id=entry["device_id"],
            )
        return KeyRegistrationResult(
            public_key_hash=key_hash,
            registration_status=KeyRegistrationStatus.UNREGISTERED,
        )

    def register(self, public_key_b64: str, device_id: str) -> KeyRegistrationResult:
        key_hash = compute_public_key_hash(public_key_b64)
        now = datetime.now(timezone.utc).isoformat()
        self._keys[key_hash] = {
            "registered_at": now,
            "device_id": device_id,
        }
        return KeyRegistrationResult(
            public_key_hash=key_hash,
            registration_status=KeyRegistrationStatus.REGISTERED,
            registered_at=now,
            device_id=device_id,
        )

    def check_device_identity(self, public_key_b64: str, claimed_device_id: str) -> tuple[bool, str | None]:
        """
        Check if the public key is registered and matches the claimed device_id.

        Returns:
            (is_consistent, registered_device_id or None)
        """
        key_hash = compute_public_key_hash(public_key_b64)
        if key_hash not in self._keys:
            return False, None
        registered_device_id = self._keys[key_hash]["device_id"]
        is_consistent = registered_device_id == claimed_device_id
        return is_consistent, registered_device_id

    def store_record(self, entry: RecordStoreEntry) -> tuple[bool, str]:
        """
        Store a record for replay detection.

        Returns:
            (is_new, message)
        """
        if entry.record_id in self._records:
            existing = self._records[entry.record_id]
            if existing.manifest_hash == entry.manifest_hash:
                return False, "Duplicate submission: same record_id with identical content"
            else:
                return False, "CONFLICT: same record_id with different content"
        self._records[entry.record_id] = entry
        return True, "Record stored"

    def check_record_exists(self, record_id: str) -> bool:
        return record_id in self._records

    def get_record(self, record_id: str) -> RecordStoreEntry | None:
        return self._records.get(record_id)


_registry: InMemoryKeyRegistry | None = None


def get_registry() -> InMemoryKeyRegistry:
    global _registry
    if _registry is None:
        _registry = InMemoryKeyRegistry()
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None
