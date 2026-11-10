from __future__ import annotations

import math
import re
from typing import Any

from app.models.capture import SignatureManifest


DOMAIN_SEPARATOR_V1_0 = "WorkCam-Sig-v1"
DOMAIN_SEPARATOR_V1_1 = "WorkCam-Sig-v1.1"

FIELD_ORDER: list[str] = [
    "version",
    "record_id",
    "sequence_number",
    "previous_record_hash",
    "photo_hash",
    "capture_timestamp",
    "device_reported_time",
    "device_id",
    "location",
]

ISO8601_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _validate_string_no_newlines(value: str, field_name: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError(f"{field_name} must not contain newlines")


def _validate_iso8601(value: str, field_name: str) -> None:
    if not ISO8601_REGEX.match(value):
        raise ValueError(
            f"{field_name} must be ISO 8601 format: {value}"
        )


def _validate_finite(value: float | None, field_name: str) -> None:
    if value is None:
        return
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"{field_name} must be finite, got {value}")


def _format_float(value: float) -> str:
    _validate_finite(value, "float")
    if value == int(value) and not (value == 0.0 and str(value).startswith("-")):
        return f"{int(value)}.0"
    formatted = f"{value:.10g}"
    if "." not in formatted and "e" not in formatted.lower():
        formatted += ".0"
    return formatted


def _format_location_v1_1(m: SignatureManifest) -> str:
    if m.location is None:
        return "none"
    loc = m.location
    _validate_finite(loc.latitude, "latitude")
    _validate_finite(loc.longitude, "longitude")
    _validate_finite(loc.horizontal_accuracy, "horizontal_accuracy")
    _validate_finite(loc.altitude, "altitude")
    lat = _format_float(loc.latitude)
    lon = _format_float(loc.longitude)
    acc = _format_float(loc.horizontal_accuracy)
    alt = "none" if loc.altitude is None else _format_float(loc.altitude)
    return f"{lat},{lon},{acc},{alt}"


def _format_location_v1_0(m: SignatureManifest) -> str:
    if m.location is None:
        return "none"
    loc = m.location
    alt = "none" if loc.altitude is None else f"{loc.altitude}"
    return f"{loc.latitude},{loc.longitude},{loc.horizontal_accuracy},{alt}"


def _format_field(name: str, m: SignatureManifest, version: str) -> str:
    if name == "location":
        if version == "1.1":
            return f"location={_format_location_v1_1(m)}"
        return f"location={_format_location_v1_0(m)}"
    value = getattr(m, name)
    if value is None:
        return f"{name}=none"
    if isinstance(value, str):
        _validate_string_no_newlines(value, name)
        if name in ("capture_timestamp", "device_reported_time"):
            _validate_iso8601(value, name)
    return f"{name}={value}"


def canonical_payload(manifest: SignatureManifest) -> bytes:
    version = manifest.version
    if version == "1.1":
        domain = DOMAIN_SEPARATOR_V1_1
    else:
        domain = DOMAIN_SEPARATOR_V1_0

    lines: list[str] = [domain]
    for field in FIELD_ORDER:
        lines.append(_format_field(field, manifest, version))
    text = "\n".join(lines) + "\n"
    return text.encode("utf-8")


def validate_manifest_fields(manifest: SignatureManifest) -> list[str]:
    errors: list[str] = []
    try:
        _validate_string_no_newlines(manifest.record_id, "record_id")
    except ValueError as e:
        errors.append(str(e))
    try:
        _validate_string_no_newlines(manifest.photo_hash, "photo_hash")
    except ValueError as e:
        errors.append(str(e))
    try:
        _validate_string_no_newlines(manifest.device_id, "device_id")
    except ValueError as e:
        errors.append(str(e))
    try:
        _validate_iso8601(manifest.capture_timestamp, "capture_timestamp")
    except ValueError as e:
        errors.append(str(e))
    try:
        _validate_iso8601(manifest.device_reported_time, "device_reported_time")
    except ValueError as e:
        errors.append(str(e))
    if manifest.previous_record_hash is not None:
        try:
            _validate_string_no_newlines(
                manifest.previous_record_hash, "previous_record_hash"
            )
        except ValueError as e:
            errors.append(str(e))
    if manifest.location is not None:
        try:
            _validate_finite(manifest.location.latitude, "latitude")
            _validate_finite(manifest.location.longitude, "longitude")
            _validate_finite(
                manifest.location.horizontal_accuracy, "horizontal_accuracy"
            )
            _validate_finite(manifest.location.altitude, "altitude")
        except ValueError as e:
            errors.append(str(e))
    return errors
