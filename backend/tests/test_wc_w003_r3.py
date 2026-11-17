"""
Tests for WC-W003-R3 security fixes

Tests for:
1. Persistence failure rollback
2. Key registry protection
3. Key rotation support
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from app.models.time_anchor import (
    TimeAnchorRequest,
    TimeAnchorReceipt,
    VerificationStatus,
)
from app.services.time_anchor import (
    TimeAnchorStore,
    TimeAnchorService,
    KeyRegistry,
    PersistenceError,
    reset_time_anchor_service,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(autouse=True)
def reset_service():
    """Reset service before each test."""
    reset_time_anchor_service()
    yield
    reset_time_anchor_service()


class TestPersistenceRollback:
    """Test persistence failure rollback."""

    def test_store_rollback_on_persistence_failure(self, temp_dir):
        """Test that memory is rolled back when persistence fails."""
        persist_path = temp_dir / "anchors.json"
        store = TimeAnchorStore(persist_path)

        # Create a receipt
        receipt = TimeAnchorReceipt(
            anchor_id="anchor-1",
            key_id="test-key",
            batch_id="batch-1",
            server_received_at="2026-11-17T10:00:00+00:00",
            challenge="challenge-1",
            server_signature="signature-1",
            chain_head_hash="a" * 64,
            chain_tail_hash="b" * 64,
            record_count=10,
            device_reported_start_time="2026-11-17T09:00:00+00:00",
            device_reported_end_time="2026-11-17T10:00:00+00:00",
            verification_status=VerificationStatus.DIGEST_ANCHORED,
            stored_at="2026-11-17T10:00:00+00:00",
        )

        # Mock _save_to_file to raise PersistenceError
        with patch.object(store, '_save_to_file', side_effect=PersistenceError("Disk full")):
            with pytest.raises(PersistenceError, match="Disk full"):
                store.store(receipt)

        # Verify memory was rolled back
        assert store.get_by_anchor_id("anchor-1") is None
        assert store.get_by_batch_id("batch-1") is None

        # Verify we can retry with the same batch_id
        receipt2 = TimeAnchorReceipt(
            anchor_id="anchor-2",
            key_id="test-key",
            batch_id="batch-1",  # Same batch_id
            server_received_at="2026-11-17T10:00:00+00:00",
            challenge="challenge-2",
            server_signature="signature-2",
            chain_head_hash="c" * 64,
            chain_tail_hash="d" * 64,
            record_count=20,
            device_reported_start_time="2026-11-17T09:00:00+00:00",
            device_reported_end_time="2026-11-17T10:00:00+00:00",
            verification_status=VerificationStatus.DIGEST_ANCHORED,
            stored_at="2026-11-17T10:00:00+00:00",
        )

        # This should succeed now
        result = store.store(receipt2)
        assert result is True
        assert store.get_by_batch_id("batch-1") is not None

    def test_persistence_success_no_rollback(self, temp_dir):
        """Test that successful persistence does not rollback."""
        persist_path = temp_dir / "anchors.json"
        store = TimeAnchorStore(persist_path)

        receipt = TimeAnchorReceipt(
            anchor_id="anchor-1",
            key_id="test-key",
            batch_id="batch-1",
            server_received_at="2026-11-17T10:00:00+00:00",
            challenge="challenge-1",
            server_signature="signature-1",
            chain_head_hash="a" * 64,
            chain_tail_hash="b" * 64,
            record_count=10,
            device_reported_start_time="2026-11-17T09:00:00+00:00",
            device_reported_end_time="2026-11-17T10:00:00+00:00",
            verification_status=VerificationStatus.DIGEST_ANCHORED,
            stored_at="2026-11-17T10:00:00+00:00",
        )

        result = store.store(receipt)
        assert result is True

        # Verify data is in memory
        assert store.get_by_anchor_id("anchor-1") is not None
        assert store.get_by_batch_id("batch-1") is not None

        # Verify data is persisted
        assert persist_path.exists()

        # Create new store instance to verify persistence
        store2 = TimeAnchorStore(persist_path)
        assert store2.get_by_anchor_id("anchor-1") is not None
        assert store2.get_by_batch_id("batch-1") is not None


class TestKeyRegistry:
    """Test key registry protection."""

    def test_register_same_key_id_same_key_idempotent(self):
        """Test that registering same key_id with same key is idempotent."""
        registry = KeyRegistry()
        
        # Generate a key
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key()
        
        # First registration
        result1 = registry.register_key("key-1", public_key)
        assert result1 is True
        
        # Second registration with same key (idempotent)
        result2 = registry.register_key("key-1", public_key)
        assert result2 is False  # False means duplicate, not error
        
        # Key should still be registered
        assert registry.has_key("key-1")
        assert registry.get_public_key("key-1") is not None

    def test_register_same_key_id_different_key_rejected(self):
        """Test that registering same key_id with different key is rejected."""
        registry = KeyRegistry()
        
        # Generate two different keys
        private_key1 = ec.generate_private_key(ec.SECP256R1())
        public_key1 = private_key1.public_key()
        
        private_key2 = ec.generate_private_key(ec.SECP256R1())
        public_key2 = private_key2.public_key()
        
        # First registration
        result1 = registry.register_key("key-1", public_key1)
        assert result1 is True
        
        # Second registration with different key should fail
        with pytest.raises(ValueError, match="already registered with different public key"):
            registry.register_key("key-1", public_key2)
        
        # Original key should still be registered
        assert registry.get_public_key("key-1") == public_key1

    def test_register_different_key_id_allowed(self):
        """Test that registering different key_id is allowed (rotation)."""
        registry = KeyRegistry()
        
        # Generate two different keys
        private_key1 = ec.generate_private_key(ec.SECP256R1())
        public_key1 = private_key1.public_key()
        
        private_key2 = ec.generate_private_key(ec.SECP256R1())
        public_key2 = private_key2.public_key()
        
        # Register first key
        result1 = registry.register_key("key-1", public_key1)
        assert result1 is True
        
        # Register second key with different key_id (rotation)
        result2 = registry.register_key("key-2", public_key2)
        assert result2 is True
        
        # Both keys should be registered
        assert registry.has_key("key-1")
        assert registry.has_key("key-2")
        assert registry.get_public_key("key-1") == public_key1
        assert registry.get_public_key("key-2") == public_key2


class TestKeyRotation:
    """Test key rotation scenarios."""

    def test_old_receipt_verifiable_after_rotation(self, temp_dir):
        """Test that old receipts can still be verified after key rotation."""
        # Create service with first key
        persist_path = temp_dir / "anchors.json"
        service1 = TimeAnchorService(
            key_id="key-v1",
            persist_path=persist_path,
        )
        
        # Create an anchor with first key
        request = TimeAnchorRequest(
            batch_id="batch-old",
            chain_head_hash="a" * 64,
            chain_tail_hash="b" * 64,
            record_count=10,
            device_reported_start_time="2026-11-17T09:00:00+00:00",
            device_reported_end_time="2026-11-17T10:00:00+00:00",
        )
        
        response1 = service1.create_anchor(request)
        receipt1 = service1._store.get_by_anchor_id(response1.anchor_id)
        
        # Verify old receipt works
        verification1 = service1.verify_anchor(receipt1)
        assert verification1.valid is True
        
        # Simulate key rotation: create new service with new key
        # but register old key as trusted
        old_public_key = service1._public_key
        
        service2 = TimeAnchorService(
            key_id="key-v2",
            persist_path=persist_path,
            trusted_keys={"key-v1": old_public_key},
        )
        
        # Old receipt should still be verifiable
        verification2 = service2.verify_anchor(receipt1)
        assert verification2.valid is True
        assert verification2.key_id == "key-v1"

    def test_new_receipt_with_new_key(self, temp_dir):
        """Test that new receipts with new key can be verified."""
        # Create service with first key
        persist_path = temp_dir / "anchors.json"
        service1 = TimeAnchorService(
            key_id="key-v1",
            persist_path=persist_path,
        )
        
        old_public_key = service1._public_key
        
        # Create service with new key
        service2 = TimeAnchorService(
            key_id="key-v2",
            persist_path=persist_path,
            trusted_keys={"key-v1": old_public_key},
        )
        
        # Create anchor with new key
        request = TimeAnchorRequest(
            batch_id="batch-new",
            chain_head_hash="c" * 64,
            chain_tail_hash="d" * 64,
            record_count=20,
            device_reported_start_time="2026-11-17T11:00:00+00:00",
            device_reported_end_time="2026-11-17T12:00:00+00:00",
        )
        
        response2 = service2.create_anchor(request)
        receipt2 = service2._store.get_by_anchor_id(response2.anchor_id)
        
        # Verify new receipt works
        verification = service2.verify_anchor(receipt2)
        assert verification.valid is True
        assert verification.key_id == "key-v2"

    def test_unknown_key_id_rejected(self, temp_dir):
        """Test that receipts with unknown key_id are rejected."""
        persist_path = temp_dir / "anchors.json"
        service = TimeAnchorService(
            key_id="known-key",
            persist_path=persist_path,
        )
        
        # Create a receipt with unknown key_id
        receipt = TimeAnchorReceipt(
            anchor_id="anchor-unknown",
            key_id="unknown-key",
            batch_id="batch-unknown",
            server_received_at="2026-11-17T10:00:00+00:00",
            challenge="challenge",
            server_signature="signature",
            chain_head_hash="a" * 64,
            chain_tail_hash="b" * 64,
            record_count=10,
            device_reported_start_time="2026-11-17T09:00:00+00:00",
            device_reported_end_time="2026-11-17T10:00:00+00:00",
            verification_status=VerificationStatus.DIGEST_ANCHORED,
            stored_at="2026-11-17T10:00:00+00:00",
        )
        
        verification = service.verify_anchor(receipt)
        assert verification.valid is False
        assert "unknown key_id" in verification.message.lower()

    def test_wrong_public_key_rejected(self, temp_dir):
        """Test that receipts verified with wrong public key fail."""
        persist_path = temp_dir / "anchors.json"
        service = TimeAnchorService(
            key_id="key-1",
            persist_path=persist_path,
        )
        
        # Create an anchor
        request = TimeAnchorRequest(
            batch_id="batch-1",
            chain_head_hash="a" * 64,
            chain_tail_hash="b" * 64,
            record_count=10,
            device_reported_start_time="2026-11-17T09:00:00+00:00",
            device_reported_end_time="2026-11-17T10:00:00+00:00",
        )
        
        response = service.create_anchor(request)
        receipt = service._store.get_by_anchor_id(response.anchor_id)
        
        # Create a different key and register it with the same key_id
        # This should fail due to KeyRegistry protection
        different_private_key = ec.generate_private_key(ec.SECP256R1())
        different_public_key = different_private_key.public_key()
        
        with pytest.raises(ValueError, match="already registered with different public key"):
            service._key_registry.register_key("key-1", different_public_key)
        
        # Original receipt should still verify with original key
        verification = service.verify_anchor(receipt)
        assert verification.valid is True


class TestRestartPersistence:
    """Test that receipts persist across restarts."""

    def test_receipts_persist_after_restart(self, temp_dir):
        """Test that receipts persist and can be verified after restart."""
        persist_path = temp_dir / "anchors.json"
        
        # Create service and add receipt
        service1 = TimeAnchorService(
            key_id="test-key",
            persist_path=persist_path,
        )
        
        request = TimeAnchorRequest(
            batch_id="batch-persist",
            chain_head_hash="a" * 64,
            chain_tail_hash="b" * 64,
            record_count=10,
            device_reported_start_time="2026-11-17T09:00:00+00:00",
            device_reported_end_time="2026-11-17T10:00:00+00:00",
        )
        
        response = service1.create_anchor(request)
        anchor_id = response.anchor_id
        
        # Simulate restart: create new service instance
        reset_time_anchor_service()
        service2 = TimeAnchorService(
            key_id="test-key",
            persist_path=persist_path,
        )
        
        # Receipt should still exist
        receipt = service2._store.get_by_anchor_id(anchor_id)
        assert receipt is not None
        
        # Receipt should still be verifiable
        verification = service2.verify_anchor(receipt)
        assert verification.valid is True
        assert verification.signature_valid is True
