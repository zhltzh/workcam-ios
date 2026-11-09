from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.models.capture import LocationData, SignatureManifest
from app.services.serialization import canonical_payload


def _encode_pub(public_key) -> bytes:
    return public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


def make_test_photo(size: int = 1024) -> bytes:
    return b"\x42" * size


def make_manifest(
    private_key,
    pub_bytes: bytes,
    photo_bytes: bytes | None = None,
    seq: int = 0,
    prev_hash: str | None = None,
    record_id: str | None = None,
    location: LocationData | None = None,
    tamper_photo_hash: bool = False,
    tamper_sequence: int | None = None,
) -> SignatureManifest:
    if photo_bytes is None:
        photo_bytes = make_test_photo()

    photo_hash = hashlib.sha256(photo_bytes).hexdigest()
    if tamper_photo_hash:
        photo_hash = "aa" * 32

    actual_seq = tamper_sequence if tamper_sequence is not None else seq

    manifest = SignatureManifest(
        version="1.0",
        record_id=record_id or str(uuid.uuid4()),
        sequence_number=actual_seq,
        previous_record_hash=prev_hash,
        photo_hash=photo_hash,
        capture_timestamp=datetime.now(timezone.utc).isoformat(),
        device_reported_time=datetime.now(timezone.utc).isoformat(),
        device_id="test-device-001",
        location=location,
        signature="",
        public_key=base64.b64encode(pub_bytes).decode(),
    )

    payload = canonical_payload(manifest)
    sig_bytes = private_key.sign(payload, ec.ECDSA(SHA256()))

    return manifest.model_copy(update={
        "signature": base64.b64encode(sig_bytes).decode(),
    })
