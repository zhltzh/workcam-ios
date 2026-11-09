from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric import ec

from app.services.chain import compute_record_hash, verify_chain, verify_chain_link
from tests.conftest import make_manifest, _encode_pub


def test_genesis_record_valid():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub, seq=0, prev_hash=None)
    ok, msg = verify_chain_link(m, None)
    assert ok is True


def test_genesis_with_prev_hash_rejected():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    m = make_manifest(priv, pub, seq=0, prev_hash="aa" * 32)
    ok, msg = verify_chain_link(m, None)
    assert ok is False


def test_chain_link_valid():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    r0 = make_manifest(priv, pub, seq=0, prev_hash=None)
    prev_hash = compute_record_hash(r0)
    r1 = make_manifest(priv, pub, seq=1, prev_hash=prev_hash)
    ok, msg = verify_chain_link(r1, r0)
    assert ok is True, msg


def test_chain_link_wrong_prev_hash():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    r0 = make_manifest(priv, pub, seq=0, prev_hash=None)
    r1 = make_manifest(priv, pub, seq=1, prev_hash="bb" * 32)
    ok, msg = verify_chain_link(r1, r0)
    assert ok is False
    assert "broken" in msg.lower() or "mismatch" in msg.lower()


def test_chain_link_missing_previous():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    r1 = make_manifest(priv, pub, seq=1, prev_hash="cc" * 32)
    ok, msg = verify_chain_link(r1, None)
    assert ok is False


def test_chain_link_sequence_gap():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    r0 = make_manifest(priv, pub, seq=0, prev_hash=None)
    r5 = make_manifest(priv, pub, seq=5, prev_hash=compute_record_hash(r0))
    ok, msg = verify_chain_link(r5, r0)
    assert ok is False
    assert "gap" in msg.lower() or "sequence" in msg.lower()


def test_verify_chain_full():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    records = []
    prev_hash = None
    for i in range(5):
        r = make_manifest(priv, pub, seq=i, prev_hash=prev_hash)
        records.append(r)
        prev_hash = compute_record_hash(r)

    results = verify_chain(records)
    assert all(r.get("valid") for r in results)


def test_verify_chain_tampered_middle():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    records = []
    prev_hash = None
    for i in range(5):
        r = make_manifest(priv, pub, seq=i, prev_hash=prev_hash)
        records.append(r)
        prev_hash = compute_record_hash(r)

    fake = make_manifest(priv, pub, seq=2, prev_hash="dd" * 32)
    records[2] = fake

    results = verify_chain(records)
    tampered = [r for r in results if r.get("sequence_number") == 2]
    assert len(tampered) == 1
    assert tampered[0]["valid"] is False


def test_verify_chain_duplicate_sequence():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(priv.public_key())
    r0 = make_manifest(priv, pub, seq=0, prev_hash=None)
    r1 = make_manifest(priv, pub, seq=1, prev_hash=compute_record_hash(r0))
    r1_dup = make_manifest(priv, pub, seq=1, prev_hash=compute_record_hash(r0))
    results = verify_chain([r0, r1, r1_dup])
    assert any("duplicate" in str(r).lower() for r in results)


def test_verify_chain_empty():
    results = verify_chain([])
    assert any("error" in r for r in results)
