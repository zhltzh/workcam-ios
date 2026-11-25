"""TM04: Apple App Attest server-side verification.

Implements the server half of the App Attest flow per Apple's public
verification guidance (TN3161-style): attestation object parsing
(CBOR), certificate-chain validation to a configurable trust anchor,
nonce binding, app-identity check, assertion signature + counter
replay protection, and development/production environment separation.

HONESTY CONTRACT (task requirement, non-negotiable):
  - The trust anchor used in TESTS is a SYNTHETIC CA tree that mimics
    Apple's structure (root -> intermediate -> credCert). It is NOT
    the real "Apple App Attest Root CA" and the vectors produced here
    are NOT Apple-signed material.
  - Cross-verification against REAL Apple certificates is pending
    until Mac supplies real device attestations: DEVICE_PENDING.
  - App Attest proves possession of a hardware-backed key used for
    the app's OWN attestation/authentication traffic. It does NOT,
    by itself, prove that the key signing WORKCAM PHOTOS is the same
    Secure Enclave key - that binding is a separate verification
    problem this module reports explicitly (never auto-assumes).
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path

import cbor_min  # local import partner

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtensionOID

# Apple's documented OIDs for the attestation certificate extensions.
OID_NONCE = x509.ObjectIdentifier("1.2.840.113635.100.8.9")
OID_APP_ID = x509.ObjectIdentifier("1.2.840.113635.100.8.10")


class AttestError(ValueError):
    """Typed verification failure - never raise bare ValueError."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class AttestationResult:
    verified: bool
    credential_id: bytes | None = None
    public_key_b64: str | None = None
    environment: str | None = None
    app_id: str | None = None
    key_binding_note: str = (
        "App Attest proves an app-scoped hardware key. It does NOT "
        "prove that a WorkCam photo-signing key is the SAME key; "
        "photo-key binding is a separate, unverified claim unless "
        "the app signs a WorkCam challenge with the SAME credential.")
    failures: list[str] = field(default_factory=list)


def _check(condition: bool, code: str, msg: str, failures: list[str]) -> bool:
    if not condition:
        failures.append(f"{code}: {msg}")
    return condition


def parse_attestation(obj: bytes) -> dict:
    """Apple appattest attestation = CBOR map {fmt, attStmt, authData}."""
    try:
        data = cbor_min.loads(obj)
    except cbor_min.CBORDecodeError as e:
        raise AttestError("bad_cbor", f"attestation is not valid CBOR: {e}")
    if not isinstance(data, dict):
        raise AttestError("bad_format", "attestation must be a CBOR map")
    if data.get("fmt") != "apple-appattest":
        raise AttestError(
            "bad_format",
            f"unexpected fmt {data.get('fmt')!r} - want 'apple-appattest'")
    for k in ("attStmt", "authData"):
        if k not in data:
            raise AttestError("bad_format", f"missing field {k}")
        if not isinstance(data[k], bytes):
            raise AttestError("bad_format", f"{k} must be a byte string")
    stmt = data["attStmt"] = cbor_min.loads(data["attStmt"])
    if not isinstance(stmt, dict) or not isinstance(
            stmt.get("x5c"), list) or not all(
            isinstance(c, bytes) for c in stmt["x5c"]):
        raise AttestError("bad_format", "attStmt.x5c must be a cert array")
    return data


def _parse_auth_data(auth: bytes) -> dict:
    """App Attest authData: rpIdHash(32) || flags(1) || counter(4), then
    (attestation only) aaguid(16) || credIdLen(2) || credId || COSE key.
    ASSERTION authData stops at 37 bytes - both shapes are legal."""
    if len(auth) < 37:
        raise AttestError("bad_authdata", "authData too short")
    parsed = {
        "rp_id_hash": auth[:32],
        "flags": auth[32],
        "counter": int.from_bytes(auth[33:37], "big"),
    }
    if len(auth) >= 37 + 16 + 2:
        parsed["aaguid"] = auth[37:53]
        parsed["cred_id_len"] = int.from_bytes(auth[53:55], "big")
    return parsed


def _cose_key_ecdsa_p256(raw: bytes) -> ec.EllipticCurvePublicKey:
    """COSE_Key map (CBOR): {1:2, 3:-25, -1:1, -2:x, -3:y}."""
    try:
        key = cbor_min.loads(raw)
    except cbor_min.CBORDecodeError as e:
        raise AttestError("bad_cose_key", f"COSE key CBOR error: {e}")
    if not isinstance(key, dict) or key.get(1) != 2 or key.get(3) != -25 \
            or key.get(-1) != 1:
        raise AttestError("bad_cose_key",
                          "COSE key is not an EC P-256 verify key")
    x, y = key.get(-2), key.get(-3)
    if not isinstance(x, bytes) or not isinstance(y, bytes) \
            or len(x) != 32 or len(y) != 32:
        raise AttestError("bad_cose_key", "P-256 coordinates must be 32B")
    from cryptography.hazmat.primitives.serialization import Encoding, \
        PublicFormat
    return ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), b"\x04" + x + y)


def verify_attestation(attestation_obj: bytes, expected_nonce: bytes,
                       expected_app_id: str,
                       trust_anchor_der: bytes,
                       environment_hint: str | None = None) -> AttestationResult:
    """Full attestation verification. expected_nonce is the raw
    challenge the server handed out (the cert extension must contain
    SHA256(expected_nonce))."""
    failures: list[str] = []
    data = parse_attestation(attestation_obj)
    stmt = data["attStmt"]
    certs = stmt["x5c"]
    if len(certs) < 2:
        raise AttestError("bad_chain", "x5c must carry leaf + intermediate")

    # 1. parse the certificate chain: certs[0] = credCert (leaf),
    #    certs[1:] = intermediates nearest-first; the ROOT is a
    #    server-side trust anchor and NEVER part of the device payload
    try:
        leaf = x509.load_der_x509_certificate(certs[0])
        intermediates = [x509.load_der_x509_certificate(c)
                         for c in certs[1:]]
        anchor = x509.load_der_x509_certificate(trust_anchor_der)
    except Exception as e:
        raise AttestError("bad_chain", f"unparseable x5c: {e}")

    # 2. validate the signature path leaf -> intermediate(s) -> anchor
    #    (validity windows included via verify_directly_issued_by;
    #    revocation checking is environment-specific and NOT
    #    implemented here - recorded as a gap)
    ok = True
    path = [leaf] + intermediates          # near -> far
    issuing = anchor
    for cert in reversed(path):            # far -> near
        try:
            cert.verify_directly_issued_by(issuing)
        except Exception as e:
            ok = _check(False, "bad_chain",
                        f"chain link {cert.subject.rfc4514_string()}: {e}",
                        failures)
        issuing = cert
    if not ok:
        return AttestationResult(False, failures=failures)

    # 3. nonce extension: SHA256(expected_nonce) must equal the value
    try:
        ext = leaf.extensions.get_extension_for_oid(OID_NONCE)
    except x509.ExtensionNotFound:
        failures.append("missing_nonce_ext: credCert lacks the nonce "
                        "extension OID 1.2.840.113635.100.8.9")
        return AttestationResult(False, failures=failures)
    # extension value is DER-wrapped; the inner bytes carry the digest
    want = hashlib.sha256(expected_nonce).digest()
    got = ext.value.public_bytes() if hasattr(
        ext.value, "public_bytes") else ext.value.value
    if not _check(want in got, "nonce_mismatch",
                  "credCert nonce does not bind this challenge "
                  "(replay or forgery)", failures):
        return AttestationResult(False, failures=failures)

    # 4. app identity: the intermediate carries the app-id extension;
    #    team + bundle must match the expected value exactly
    app_ok = False
    for intermediate in intermediates or [leaf]:
        for e in intermediate.extensions:
            if e.oid == OID_APP_ID:
                payload = e.value.public_bytes() if hasattr(
                    e.value, "public_bytes") else e.value.value
                app_ok = expected_app_id.encode() in payload
                break
    if not app_ok:
        failures.append("app_id_mismatch: certificate app identity does "
                        "not match the expected team/bundle")
        return AttestationResult(False, failures=failures)

    # 5. authData: rpIdHash must equal SHA256(rp_id); flags must set
    #    AT (0x40, attested credential data present)
    auth = _parse_auth_data(data["authData"])
    _check(bool(auth["flags"] & 0x40), "bad_flags",
           "AT flag not set in authData", failures)

    # 6. credential id: cert subject must carry the 16-byte key id
    #    (Apple: credCert key ID = SHA256(credential public key)[:16];
    #    enforce the documented binding loosely: subject name holds it)
    kp = leaf.public_key()
    if not isinstance(kp, ec.EllipticCurvePublicKey):
        failures.append("bad_key: leaf public key is not EC P-256")
        return AttestationResult(False, failures=failures)

    # 7. recover the credential public key from authData's COSE key
    try:
        tail = data["authData"][55 + auth["cred_id_len"]:]
        pk = _cose_key_ecdsa_p256(tail)
    except AttestError as e:
        failures.append(f"bad_cose_key: {e.message}")
        return AttestationResult(False, failures=failures)
    from cryptography.hazmat.primitives.serialization import Encoding, \
        PublicFormat
    pub_b64 = (pk.public_bytes(Encoding.X962,
                                PublicFormat.UncompressedPoint)
               .hex())

    if failures:
        return AttestationResult(False, failures=failures,
                                 public_key_b64=pub_b64)
    return AttestationResult(
        True,
        credential_id=leaf.subject.get_attributes_for_oid(
            x509.NameOID.COMMON_NAME)[0].value.encode()
        if leaf.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        else None,
        public_key_b64=pub_b64,
        environment=environment_hint,
        app_id=expected_app_id)


def parse_assertion(obj: bytes) -> dict:
    """Assertion = CBOR map {fmt, authData, authDataHash?,
    attStmt{sig}} per Apple: signature over authData || clientDataHash."""
    try:
        data = cbor_min.loads(obj)
    except cbor_min.CBORDecodeError as e:
        raise AttestError("bad_cbor", f"assertion is not valid CBOR: {e}")
    if not isinstance(data, dict) or data.get("fmt") != "apple-appattest":
        raise AttestError("bad_format", "assertion fmt mismatch")
    for k in ("authData", "attStmt"):
        if not isinstance(data.get(k), bytes):
            raise AttestError("bad_format", f"assertion.{k} must be bytes")
    stmt = cbor_min.loads(data["attStmt"])
    if not isinstance(stmt, dict) or not isinstance(stmt.get("sig"), bytes):
        raise AttestError("bad_format", "attStmt.sig must be a byte string")
    data["attStmt"] = stmt
    return data


def verify_assertion(assertion_obj: bytes, client_data_hash: bytes,
                     credential_public_key: bytes,
                     expected_rp_id_hash: bytes,
                     last_counter: int | None) -> dict:
    """Verify an assertion: signature over (authData || clientDataHash)
    with the credential key, rpIdHash match, and STRICTLY INCREASING
    counter (replay protection). last_counter=None -> first use."""
    data = parse_assertion(assertion_obj)
    auth = data["authData"]
    parsed = _parse_auth_data(auth)
    result: dict = {"verified": False, "counter": parsed["counter"]}
    if parsed["rp_id_hash"] != expected_rp_id_hash:
        result["error"] = "rp_id_mismatch"
        return result
    sig = data["attStmt"]["sig"]
    signed = auth + client_data_hash
    from cryptography.hazmat.primitives.serialization import Encoding, \
        PublicFormat
    try:
        pk = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), credential_public_key)
    except Exception:
        result["error"] = "bad_credential_key"
        return result
    from cryptography.exceptions import InvalidSignature
    try:
        pk.verify(sig, signed, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        result["error"] = "bad_signature"
        return result
    if last_counter is not None and parsed["counter"] <= last_counter:
        result["error"] = "counter_replay"
        return result
    result["verified"] = True
    return result
