from __future__ import annotations

from pydantic import BaseModel, Field


SUPPORTED_VERSIONS = {"1.0"}


class LocationData(BaseModel):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    horizontal_accuracy: float = Field(gt=0.0)
    altitude: float | None = None


class SignatureManifest(BaseModel):
    version: str
    record_id: str
    sequence_number: int = Field(ge=0)
    previous_record_hash: str | None = None
    photo_hash: str
    capture_timestamp: str
    device_reported_time: str
    device_id: str
    location: LocationData | None = None
    signature: str
    public_key: str


class VerifyRequest(BaseModel):
    manifest: SignatureManifest
    photo_base64: str | None = None


class VerifyDetail(BaseModel):
    check: str
    passed: bool
    message: str = ""


class VerifyResponse(BaseModel):
    valid: bool
    summary: str
    details: list[VerifyDetail]
