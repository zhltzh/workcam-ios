from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.services.serialization import canonical_payload, DOMAIN_SEPARATOR
from tests.conftest import make_manifest, make_test_photo, _encode_pub


def test_canonical_payload_starts_with_domain_separator():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    payload = canonical_payload(m)
    text = payload.decode("utf-8")
    assert text.startswith("WorkCam-Sig-v1\n")


def test_canonical_payload_deterministic():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m1 = make_manifest(priv, pub, photo_bytes=photo, record_id="fixed-id-001")
    m2 = make_manifest(priv, pub, photo_bytes=photo, record_id="fixed-id-001")
    p1 = canonical_payload(m1)
    p2 = canonical_payload(m2)
    assert p1 == p2


def test_canonical_payload_utf8():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    payload = canonical_payload(m)
    payload.decode("utf-8")


def test_canonical_payload_contains_all_fields():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    text = canonical_payload(m).decode("utf-8")
    for field in ["version", "record_id", "sequence_number", "photo_hash",
                   "capture_timestamp", "device_id", "location"]:
        assert field in text


def test_canonical_payload_excludes_signature():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    text = canonical_payload(m).decode("utf-8")
    assert "signature=" not in text
    assert "public_key=" not in text


def test_location_none_format():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub, location=None)
    text = canonical_payload(m).decode("utf-8")
    assert "location=none" in text


def test_location_with_values():
    from cryptography.hazmat.primitives.asymmetric import ec
    from app.models.capture import LocationData
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    loc = LocationData(
        latitude=39.9042, longitude=116.4074,
        horizontal_accuracy=5.0, altitude=50.0
    )
    m = make_manifest(priv, pub, location=loc)
    text = canonical_payload(m).decode("utf-8")
    assert "location=39.9042,116.4074,5.0,50.0" in text
