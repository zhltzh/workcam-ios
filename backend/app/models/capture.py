from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


SUPPORTED_VERSIONS = {"1.0", "1.1"}


class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_CHECKED = "not_checked"


class KeyRegistrationStatus(str, Enum):
    REGISTERED = "registered"
    UNREGISTERED = "unregistered"
    UNKNOWN = "unknown"


class DeviceAttestationStatus(str, Enum):
    ATTESTED = "attested"
    NOT_ATTESTED = "not_attested"
    UNKNOWN = "unknown"


class EvidenceStatus(str, Enum):
    """
    Evidence trust levels (from highest to lowest):

    FULLY_ATTESTED: All checks passed including device attestation and time anchor.
                    This is the highest trust level, rarely achieved in practice.

    CRYPTOGRAPHICALLY_VALID: Signature math valid, photo hash matches, key registered.
                            BUT device attestation is unknown/unverified.
                            This does NOT prove the device is genuine.

    SIGNATURE_VALID: Signature math valid, but photo not verified or key not registered.

    PARTIAL: Some checks passed, some incomplete.

    UNVERIFIED: Checks could not be performed.

    INVALID: One or more checks failed.
    """
    FULLY_ATTESTED = "fully_attested"
    CRYPTOGRAPHICALLY_VALID = "cryptographically_valid"
    SIGNATURE_VALID = "signature_valid"
    PARTIAL = "partial"
    UNVERIFIED = "unverified"
    INVALID = "invalid"


class TimeTrustLevel(str, Enum):
    """
    How much we trust the timestamp:

    SERVER_ANCHORED: Server verified the time when record was submitted.
    DEVICE_REPORTED: Only device-reported time, no server verification.
    UNKNOWN: No time information available.
    """
    SERVER_ANCHORED = "server_anchored"
    DEVICE_REPORTED = "device_reported"
    UNKNOWN = "unknown"


class LocationData(BaseModel):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    horizontal_accuracy: float = Field(gt=0.0)
    altitude: float | None = Field(default=None, ge=-1000.0, le=100000.0)

    @field_validator("latitude", "longitude", "horizontal_accuracy", "altitude")
    @classmethod
    def reject_non_finite(cls, v: float | None) -> float | None:
        if v is None:
            return v
        import math
        if math.isnan(v) or math.isinf(v):
            raise ValueError("NaN and Infinity are not allowed")
        return v


class SignatureManifest(BaseModel):
    version: str
    record_id: str = Field(max_length=256)
    sequence_number: int = Field(ge=0, le=1000000)
    previous_record_hash: str | None = None
    photo_hash: str
    capture_timestamp: str
    device_reported_time: str
    device_id: str = Field(max_length=256)
    location: LocationData | None = None
    signature: str
    public_key: str


class VerifyRequest(BaseModel):
    manifest: SignatureManifest
    photo_base64: str | None = None


class VerifyDetail(BaseModel):
    check: str
    status: CheckStatus
    message: str = ""


class KeyRegistrationResult(BaseModel):
    public_key_hash: str
    registration_status: KeyRegistrationStatus
    registered_at: str | None = None
    device_id: str | None = None


class VerifyResponse(BaseModel):
    evidence_status: EvidenceStatus
    cryptographic_signature_valid: bool
    photo_hash_valid: bool | None
    key_registration_status: KeyRegistrationStatus
    device_identity_consistent: bool | None
    device_attestation_status: DeviceAttestationStatus
    time_trust_level: TimeTrustLevel
    summary: str
    details: list[VerifyDetail]
    warnings: list[str] = []


class RecordStoreEntry(BaseModel):
    record_id: str
    photo_hash: str
    device_id: str
    public_key_hash: str
    stored_at: str
    manifest_hash: str
