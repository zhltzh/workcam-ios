from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256

from app.services.signature import (
    validate_manifest_structure,
    verify_photo_hash,
    verify_signature_with_details,
)
from app.services.serialization import canonical_payload
from tests.conftest import make_manifest, make_test_photo, _encode_pub


def test_valid_photo_hash():
    photo = make_test_photo()
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub, photo_bytes=photo)
    assert verify_photo_hash(m, photo) is True


def test_modified_photo_fails_hash():
    photo = make_test_photo()
    tampered = photo[:500] + b"\x00" + photo[501:]
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub, photo_bytes=photo)
    assert verify_photo_hash(m, tampered) is False


def test_valid_signature():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    ok, msg = verify_signature_with_details(m)
    assert ok is True, msg


def test_modified_photo_byte_invalidates_signature():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    photo = make_test_photo()
    m = make_manifest(priv, pub, photo_bytes=photo)
    m2 = m.model_copy(update={"photo_hash": "aa" * 32})
    ok, msg = verify_signature_with_details(m2)
    assert ok is False


def test_wrong_public_key_fails():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)

    other_priv = ec.generate_private_key(ec.SECP256R1())
    other_pub = _encode_pub(other_priv.public_key())
    m2 = m.model_copy(update={"public_key": base64.b64encode(other_pub).decode()})
    ok, msg = verify_signature_with_details(m2)
    assert ok is False


def test_invalid_signature_bytes():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    fake_sig = base64.b64encode(b"\x30\x06\x02\x01\x01\x02\x01\x01").decode()
    m2 = m.model_copy(update={"signature": fake_sig})
    ok, msg = verify_signature_with_details(m2)
    assert ok is False


def test_tampered_manifest_sequence_breaks_signature():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub, seq=0)
    m2 = m.model_copy(update={"sequence_number": 999})
    ok, msg = verify_signature_with_details(m2)
    assert ok is False


def test_structure_unsupported_version():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    m2 = m.model_copy(update={"version": "9.9"})
    errors = validate_manifest_structure(m2)
    assert any("version" in e.lower() or "Unsupported" in e for e in errors)


def test_structure_bad_photo_hash():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    m2 = m.model_copy(update={"photo_hash": "tooshort"})
    errors = validate_manifest_structure(m2)
    assert len(errors) > 0


def test_structure_invalid_public_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    m2 = m.model_copy(update={"public_key": base64.b64encode(b"\x00\x01").decode()})
    errors = validate_manifest_structure(m2)
    assert any("public_key" in e.lower() for e in errors)


def test_newline_in_record_id_rejected():
    from cryptography.hazmat.primitives.asymmetric import ec
    from app.models.capture import SignatureManifest
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    m2 = m.model_copy(update={"record_id": "test\ninjected"})
    errors = validate_manifest_structure(m2)
    assert any("newline" in e.lower() for e in errors)


def test_invalid_timestamp_format():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub)
    m2 = m.model_copy(update={"capture_timestamp": "not-a-timestamp"})
    errors = validate_manifest_structure(m2)
    assert any("iso 8601" in e.lower() for e in errors)


def test_nan_rejected_in_location():
    from app.models.capture import LocationData
    import pytest
    with pytest.raises(Exception):
        LocationData(
            latitude=float("nan"),
            longitude=0.0,
            horizontal_accuracy=1.0,
        )


def test_infinity_rejected_in_location():
    from app.models.capture import LocationData
    import pytest
    with pytest.raises(Exception):
        LocationData(
            latitude=float("inf"),
            longitude=0.0,
            horizontal_accuracy=1.0,
        )
