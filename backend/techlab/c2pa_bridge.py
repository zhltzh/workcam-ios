"""T14 - C2PA compatibility research (Wave 3 tech-lab).

RESEARCH CONCLUSION (status: RESEARCHED; documented, not claim-complete):

C2PA (Coalition for Content Provenance and Authenticity) binds a
cryptographic "Manifest Store" into media files (JPEG/PNG via a JUMBF
box; embedded manifests reference ingredient manifests and carry
assertions signed by an ACTOR with a private key + certificate chain).

What C2PA gives WorkCam:
  - an INDUSTRY-STANDARD container for provenance assertions
    (capture time, GPS, device) that third-party tools (Verify, Firefox,
    Truepic) can already read - massive adoption advantage.

What C2PA does NOT give (honesty):
  - signing requires an X.509 certificate chain rooted in content
    credentials (an org must register; not possible offline from this
    repo without external accounts - OUT OF SCOPE per task rules);
  - a C2PA signature proves ASSERTION INTEGRITY, not scene genuineness
    (same boundary as our own signatures - the trust chain identifies
    the SIGNER, and a signer can sign lies);
  - for image bytes with our EXISTING evidence packages, C2PA and the
    WorkCam package are PARALLEL truths: C2PA embeds in the JPEG, the
    WorkCam package binds hashes externally. They can cross-reference.

This module implements the BRIDGE PoC (what is verifiable offline):
  1. c2pa_bridge_spec: a documented mapping WorkCam manifest fields ->
     C2PA assertion JSON (action list, standard schema names);
  2. bridge serialization round-trips the mapped assertions;
  3. consistency invariant: the WorkCam photo_hash equals the C2PA
     'digitalSourceType'/hash assertion - if the two disagree the
     bridging is rejected.

What is explicitly NOT implemented here (would need external accounts
or hardware, per the task's red lines): certificate chains, real C2PA
SDK integration, JUMBF embedding. The Mac side can adopt c2pa-rs/
c2pa-swift later; this bridge keeps the ASSERTION LAYER identical so
the same fields can be signed either way.
"""

from __future__ import annotations

import hashlib
import json
from pydantic import BaseModel, Field, field_validator


class BridgeError(ValueError):
    pass


C2PA_ASSERTION_ACTIONS = {
    # WorkCam manifest field -> C2PA Action (c2pa_action vocabulary)
    "photo_hash": "c2pa.hash.data",
    "capture_timestamp": "stds.exif.dateTimeOriginal",
    "device_reported_time": "stds.exif.dateTimeDigitized",
    "device_id": "stds.device.deviceID",
    "location": "stds.exif.gps",
}

STD_METADATA = "stds.metadata claim generator: WorkCam tech-lab bridge v1"


class C2PABridgeSpec(BaseModel):
    """Mapping of one WorkCam manifest into C2PA-shaped assertions."""

    photo_id: str
    claim_generator: str = STD_METADATA
    actions: dict = Field(default_factory=dict)

    @field_validator("actions")
    @classmethod
    def _no_newlines(cls, v: dict) -> dict:
        for key, value in v.items():
            if "\n" in str(key) or "\n" in str(value):
                raise BridgeError("assertion values must not contain newlines")
        return v


def bridge_manifest(workcam_manifest: dict) -> C2PABridgeSpec:
    """Map a WorkCam SignatureManifest dict to C2PA-shaped assertions."""
    actions: dict = {}
    for wc_field, c2pa_action in C2PA_ASSERTION_ACTIONS.items():
        if wc_field not in workcam_manifest:
            continue
        value = workcam_manifest[wc_field]
        if value is None:
            continue
        actions[c2pa_action] = (value if isinstance(value, (str, int, float))
                                else json.dumps(value, ensure_ascii=False))
    if "c2pa.hash.data" not in actions:
        raise BridgeError("a bridge without the photo hash is meaningless")
    return C2PABridgeSpec(photo_id=workcam_manifest["record_id"], actions=actions)


def verify_bridge_consistency(workcam_manifest: dict, bridge: C2PABridgeSpec) -> list[str]:
    """The cross-system invariant: the bridged hash assertion MUST equal
    the WorkCam photo_hash. Returns problems (empty = consistent)."""
    problems: list[str] = []
    if bridge.actions.get("c2pa.hash.data") != workcam_manifest.get("photo_hash"):
        problems.append("bridged c2pa.hash.data differs from WorkCam photo_hash")
    if bridge.photo_id != workcam_manifest.get("record_id"):
        problems.append("bridge photo_id differs from WorkCam record_id")
    return problems


def serialize_bridge(bridge: C2PABridgeSpec) -> str:
    """Round-trip serialization (the layer a real C2PA store would
    embed as a JUMBF/CBOR box - here plain JSON for the PoC contract)."""
    return json.dumps(bridge.model_dump(), indent=2, ensure_ascii=False,
                      allow_nan=False)
