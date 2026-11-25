"""Synthetic Apple-shaped CA tree for App Attest VECTOR GENERATION.

Mimics the documented structure (root -> app intermediate -> credCert
with the two Apple OIDs) so the verifier LOGIC can be tested end to
end. Every artifact here is SYNTHETIC; nothing in this file is or
pretends to be Apple material. Real-device cross-verification stays
DEVICE_PENDING until Mac supplies genuine attestations.
"""

from __future__ import annotations

import datetime
import hashlib

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app_attest_verify import OID_APP_ID, OID_NONCE


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def build_tree(app_id: str, challenge: bytes):
    """Root -> Intermediate(app id) -> credCert(nonce, key).

    Returns (root_der, leaf_der, intermediate_der, credential_key,
    cose_key_cbor)."""
    root_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    root = (x509.CertificateBuilder()
            .subject_name(_name("SYNTHETIC App Attest Root CA (NOT APPLE)"))
            .issuer_name(_name("SYNTHETIC App Attest Root CA (NOT APPLE)"))
            .public_key(root_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                           critical=True)
            .sign(root_key, hashes.SHA256()))

    inter_key = ec.generate_private_key(ec.SECP256R1())
    inter = (x509.CertificateBuilder()
             .subject_name(_name("SYNTHETIC App Attest Intermediate"))
             .issuer_name(root.subject)
             .public_key(inter_key.public_key())
             .serial_number(x509.random_serial_number())
             .not_valid_before(now - datetime.timedelta(days=1))
             .not_valid_after(now + datetime.timedelta(days=365))
             .add_extension(x509.BasicConstraints(ca=True, path_length=0),
                            critical=True)
             .add_extension(
                 x509.UnrecognizedExtension(
                     OID_APP_ID, app_id.encode()), critical=False)
             .sign(root_key, hashes.SHA256()))

    cred_key = ec.generate_private_key(ec.SECP256R1())
    key_id = hashlib.sha256(
        cred_key.public_key().public_bytes(
            __import__("cryptography.hazmat.primitives.serialization",
                       fromlist=["Encoding"]).Encoding.X962,
            __import__("cryptography.hazmat.primitives.serialization",
                       fromlist=["PublicFormat"]).PublicFormat.
            UncompressedPoint)).digest()[:16]
    nonce_digest = hashlib.sha256(challenge).digest()
    leaf = (x509.CertificateBuilder()
            .subject_name(_name(key_id.hex()))
            .issuer_name(inter.subject)
            .public_key(cred_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(x509.UnrecognizedExtension(
                OID_NONCE, nonce_digest), critical=False)
            .sign(inter_key, hashes.SHA256()))

    import cbor_min
    from cryptography.hazmat.primitives.serialization import Encoding, \
        PublicFormat
    point = cred_key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    cose_key = cbor_min.dumps({
        1: 2, 3: -25, -1: 1,
        -2: point[1:33], -3: point[33:65]})

    return (root.public_bytes(
                __import__("cryptography.hazmat.primitives.serialization",
                           fromlist=["Encoding"]).Encoding.DER),
            leaf.public_bytes(
                __import__("cryptography.hazmat.primitives.serialization",
                           fromlist=["Encoding"]).Encoding.DER),
            inter.public_bytes(
                __import__("cryptography.hazmat.primitives.serialization",
                           fromlist=["Encoding"]).Encoding.DER),
            cred_key, cose_key)


def build_attestation(leaf_der: bytes, inter_der: bytes,
                      rp_id: str, cose_key: bytes) -> bytes:
    import cbor_min
    auth_data = (hashlib.sha256(rp_id.encode()).digest()
                 + bytes([0x40])               # AT flag
                 + (0).to_bytes(4, "big")      # counter
                 + b"\x00" * 16                # aaguid
                 + (16).to_bytes(2, "big")     # credIdLen
                 + b"\x11" * 16                # credId
                 + cose_key)
    att_stmt = cbor_min.dumps({"x5c": [leaf_der, inter_der]})
    return cbor_min.dumps({
        "fmt": "apple-appattest",
        "attStmt": att_stmt,
        "authData": auth_data})


def build_assertion(cred_key, rp_id: str, counter: int,
                    client_data_hash: bytes) -> bytes:
    import cbor_min
    from cryptography.hazmat.primitives import hashes
    auth_data = (hashlib.sha256(rp_id.encode()).digest()
                 + bytes([0x00])
                 + counter.to_bytes(4, "big"))
    sig = cred_key.sign(auth_data + client_data_hash,
                        ec.ECDSA(hashes.SHA256()))
    att_stmt = cbor_min.dumps({"sig": sig})
    return cbor_min.dumps({
        "fmt": "apple-appattest",
        "authData": auth_data,
        "attStmt": att_stmt})
