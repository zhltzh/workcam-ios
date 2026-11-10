from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Protocol

from app.models.capture import (
    KeyRegistrationResult,
    KeyRegistrationStatus,
)


class PublicKeyRegistry(Protocol):
    def lookup(self, public_key_b64: str) -> KeyRegistrationResult: ...
    def register(self, public_key_b64: str, device_id: str) -> KeyRegistrationResult: ...


def compute_public_key_hash(public_key_b64: str) -> str:
    return hashlib.sha256(public_key_b64.encode("utf-8")).hexdigest()[:32]


class InMemoryKeyRegistry:
    def __init__(self) -> None:
        self._keys: dict[str, dict] = {}

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


_registry: InMemoryKeyRegistry | None = None


def get_registry() -> InMemoryKeyRegistry:
    global _registry
    if _registry is None:
        _registry = InMemoryKeyRegistry()
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None
