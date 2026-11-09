from __future__ import annotations

from app.models.capture import SignatureManifest

DOMAIN_SEPARATOR = "WorkCam-Sig-v1\n"

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


def _format_location(m: SignatureManifest) -> str:
    if m.location is None:
        return "none"
    loc = m.location
    alt = "none" if loc.altitude is None else f"{loc.altitude}"
    return f"{loc.latitude},{loc.longitude},{loc.horizontal_accuracy},{alt}"


def _format_field(name: str, m: SignatureManifest) -> str:
    if name == "location":
        return f"location={_format_location(m)}"
    value = getattr(m, name)
    if value is None:
        return f"{name}=none"
    return f"{name}={value}"


def canonical_payload(manifest: SignatureManifest) -> bytes:
    lines: list[str] = [DOMAIN_SEPARATOR.rstrip("\n")]
    for field in FIELD_ORDER:
        lines.append(_format_field(field, manifest))
    text = "\n".join(lines) + "\n"
    return text.encode("utf-8")
