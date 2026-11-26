"""TM01: machine-readable evidence schema validator + unknown-version rules.

Consumes docs/evidence/evidence-schema-v1.json (single source of truth)
and enforces the canonical-payload field contract that B02-W023 pinned
byte-wise. The validator is standalone (no pydantic dependency) so the
same rules can be ported to Swift verbatim.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "evidence-schema-v1.json"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ISO8601 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_B64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")

PAYLOAD_FIELD_ORDER = [
    "version", "record_id", "sequence_number",
    "previous_record_hash", "photo_hash",
    "capture_timestamp", "device_reported_time",
    "device_id", "location",
]


class SchemaReport:
    def __init__(self) -> None:
        self.ok = True
        self.errors: list[str] = []
        self.protocol_version: str | None = None
        self.unknown_version = False

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def as_dict(self) -> dict:
        return {
            "valid": self.ok,
            "protocol_version": self.protocol_version,
            "unknown_version": self.unknown_version,
            "errors": self.errors,
        }


def load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_record(rec: dict) -> SchemaReport:
    """Structural validation per the schema. Signature math is NOT
    checked here (that is the verifier's job); this is the shape gate."""
    r = SchemaReport()
    schema = load_schema()
    props = schema["properties"]

    if not isinstance(rec, dict):
        r.fail("record must be a JSON object")
        return r

    # unknown protocol version: hard rule - never trusted, no signature
    # claim, but structure errors below still get reported for humans
    pv = rec.get("protocol_version")
    if pv not in ("1.0", "1.1"):
        r.unknown_version = True
        r.protocol_version = pv if isinstance(pv, str) else None
        r.fail(f"unknown protocol_version {pv!r}: record can never be "
               f"marked trusted (unknown_version_policy)")
    else:
        r.protocol_version = pv

    # required fields (unknown-version records still get full shape checks)
    for k in schema["required"]:
        if k not in rec:
            r.fail(f"missing required field: {k}")

    # closed vocabulary at top level EXCEPT extensions bag
    allowed = set(props) | {"protocol_version"}
    for k in rec:
        if k not in allowed:
            r.fail(f"unknown top-level field: {k} (only 'extensions' is "
                   f"an open bag)")

    if "record_id" in rec and not (
            isinstance(rec["record_id"], str)
            and re.match(r"^[0-9a-f]{16,64}$", rec["record_id"])):
        r.fail("record_id must be 16-64 lowercase hex")
    if "sequence_number" in rec and not (
            isinstance(rec["sequence_number"], int)
            and 0 <= rec["sequence_number"] <= 1_000_000
            and not isinstance(rec["sequence_number"], bool)):
        r.fail("sequence_number must be an integer in 0..1000000 "
               "(production model bound, TM44)")
    if "photo_hash" in rec and not _HEX64.match(str(rec["photo_hash"])):
        r.fail("photo_hash must be 64 lowercase hex")
    if "previous_record_hash" in rec and rec["previous_record_hash"] is not None \
            and not _HEX64.match(str(rec["previous_record_hash"])):
        r.fail("previous_record_hash must be null or 64 lowercase hex")
    for t in ("capture_timestamp", "device_reported_time"):
        if t in rec and not (isinstance(rec[t], str) and _ISO8601.match(rec[t])):
            r.fail(f"{t} must be ISO 8601 with explicit offset")
    if "signature" in rec and not (
            isinstance(rec["signature"], str) and _B64.match(rec["signature"])):
        r.fail("signature must be Base64")
    if "public_key_b64" in rec and not (
            isinstance(rec["public_key_b64"], str)
            and _B64.match(rec["public_key_b64"])):
        r.fail("public_key_b64 must be Base64")

    loc = rec.get("location")
    if loc is not None:
        if not isinstance(loc, dict) or set(loc) - {
                "latitude", "longitude", "accuracy", "altitude"}:
            r.fail("location keys out of vocabulary")
        else:
            lat, lon, acc = (loc.get("latitude"), loc.get("longitude"),
                             loc.get("accuracy"))
            if not isinstance(lat, (int, float)) or not -90 <= lat <= 90:
                r.fail("latitude out of range")
            if not isinstance(lon, (int, float)) or not -180 <= lon <= 180:
                r.fail("longitude out of range")
            if not isinstance(acc, (int, float)) or acc < 0:
                r.fail("accuracy must be >= 0")
            alt = loc.get("altitude")
            if alt is not None and not isinstance(alt, (int, float)):
                r.fail("altitude must be null or number")

    # derivative refs are payload-adjacent, never signed in v1.x
    if "derivative_refs" in rec:
        if not isinstance(rec["derivative_refs"], list) or len(rec["derivative_refs"]) > 64:
            r.fail("derivative_refs must be an array of <= 64 hashes")
        else:
            for h in rec["derivative_refs"]:
                if not _HEX64.match(str(h)):
                    r.fail(f"derivative_ref {h!r} is not 64 lowercase hex")
    return r


def build_canonical_payload(rec: dict) -> str:
    """Rebuild the exact signed text per the schema field order.
    Raises KeyError with a clear message when a payload field is absent.
    v1.1 float formatting mirrors the B02-W023 byte contract.
    PROTOCOL RULE (schema meta.canonical_payload.rules): no string
    field may contain a newline - a value like 'a\\r\\ninjected=1'
    would smuggle a forged line INTO the signed text, so the builder
    hard-rejects it (found while pinning TM02 hostile vectors)."""
    for field in ("record_id", "capture_timestamp",
                  "device_reported_time", "device_id"):
        v = rec.get(field)
        if isinstance(v, str) and ("\n" in v or "\r" in v):
            raise ValueError(
                f"{field} must not contain newlines "
                f"(protocol canonical-payload rule)")
    pv = rec["protocol_version"]

    def fmt_num(v: float) -> str:
        if isinstance(v, int):
            return str(v)
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("non-finite float in location")
        if pv == "1.1":
            s = f"{v:.10g}"
            if "." not in s and "e" not in s and "inf" not in s:
                s += ".0"
            return s
        return repr(v)  # v1.0 legacy (documented risk F2)

    lines = [f"WorkCam-Sig-v1" if pv == "1.0" else f"WorkCam-Sig-v1.1"]
    values: dict[str, str] = {
        "version": pv,
        "record_id": rec["record_id"],
        "sequence_number": str(rec["sequence_number"]),
        "previous_record_hash": rec.get("previous_record_hash") or "none",
        "photo_hash": rec["photo_hash"],
        "capture_timestamp": rec["capture_timestamp"],
        "device_reported_time": rec["device_reported_time"],
        "device_id": rec["device_id"],
    }
    loc = rec.get("location")
    if loc is None:
        values["location"] = "none"
    else:
        alt = "none" if loc.get("altitude") is None else fmt_num(loc["altitude"])
        values["location"] = (
            f"{fmt_num(loc['latitude'])},{fmt_num(loc['longitude'])},"
            f"{fmt_num(loc['accuracy'])},{alt}")
    for field in PAYLOAD_FIELD_ORDER:
        lines.append(f"{field}={values[field]}")
    text = "\n".join(lines) + "\n"
    return text
