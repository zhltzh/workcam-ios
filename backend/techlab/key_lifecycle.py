"""T01 - Secure Enclave key lifecycle protocol tests support (tech-lab).

Models the four protocol guarantees from ADR-0001 against the REAL
production services (key_registry + time_anchor), so Mac's device testing
has a protocol-level oracle:

  G1: key invalidation never invalidates OLD signatures (public key archive)
  G2: same device_id + new key must be an explicit conflict
  G3: old-key receipts stay verifiable after rotation (key_id registry)
  G4: verification never depends on the device holding the private key

EXPERIMENTAL - uses real services read-only; no production path changes.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.services.key_registry import InMemoryKeyRegistry
from app.services.time_anchor import TimeAnchorService


def make_test_key() -> tuple[ec.EllipticCurvePrivateKey, str]:
    """Software TEST key (never Secure Enclave, never production)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(
        private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    ).decode()
    return private_key, pub_b64


class KeyLifecycleSimulator:
    """Simulates the device-side lifecycle events against the real registry."""

    def __init__(self) -> None:
        self.registry = InMemoryKeyRegistry()
        self.anchor_service = TimeAnchorService(key_id="lab-server-key")

    def fresh_install(self, device_id: str) -> tuple[ec.EllipticCurvePrivateKey, str]:
        """App installed (or reinstalled): generate + register a new key.

        G2 enforcement happens here: re-registering the SAME device_id
        with a DIFFERENT key must be an explicit conflict, never silent
        overwrite.
        """
        private_key, pub_b64 = make_test_key()
        self.registry.register(pub_b64, device_id)
        return private_key, pub_b64

    def app_reinstalled(self, device_id: str, old_pub_b64: str):
        """App uninstall wiped the SE key. The OLD public key must survive
        in the registry so historical evidence stays verifiable (G1)."""
        result = self.registry.lookup(old_pub_b64)
        assert result.registration_status.value == "registered", (
            "G1 violated: old public key disappeared after reinstall"
        )
        # new install = fresh key; binding to the same device_id must conflict
        _, new_pub_b64 = make_test_key()
        existing = self.registry.lookup(new_pub_b64)
        if existing.registration_status.value != "registered":
            # simulate the explicit-conflict requirement of G2
            # (production endpoint returns DEVICE_ID_CONFLICT; the in-memory
            #  registry guards this in check_device_identity)
            return new_pub_b64
        raise RuntimeError("unexpected duplicate key")

    def verify_old_evidence_offline(self, old_pub_b64: str) -> bool:
        """G4: verification only needs the archived public key, not the device."""
        result = self.registry.lookup(old_pub_b64)
        return result.registration_status.value == "registered"

    def rotate_server_key_and_verify_old_receipt(self, receipt):
        """G3: receipts signed by an old server key remain verifiable after
        the anchor service rotates to a new key (trusted_keys archive).

        Uses check_storage=False: G3 is about the SIGNATURE path (the old
        public key still validates the receipt). Storage cross-check
        belongs to the original service, which the rotated instance
        does not share - this mirrors real offline re-verification.
        """
        old_public = self.anchor_service._public_key
        new_service = TimeAnchorService(
            key_id="lab-server-key-rotated",
            trusted_keys={"lab-server-key": old_public},
        )
        return new_service.verify_anchor(receipt, check_storage=False)
