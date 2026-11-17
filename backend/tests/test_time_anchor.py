"""
Tests for Time Anchor Service (WC-W003)

Tests the time anchoring functionality for offline records.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.time_anchor import TimeAnchorRequest, TimeTrustLevel
from app.services.time_anchor import (
    get_time_anchor_service,
    reset_time_anchor_service,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_service():
    """Reset the time anchor service before each test."""
    reset_time_anchor_service()
    yield
    reset_time_anchor_service()


def test_health():
    """Test health endpoint."""
    resp = client.get("/time-anchor/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "experimental" in data["service"].lower()


def test_get_public_key():
    """Test getting server public key."""
    resp = client.get("/time-anchor/public-key")
    assert resp.status_code == 200
    data = resp.json()
    assert "public_key_pem" in data
    assert data["algorithm"] == "ECDSA"
    assert data["curve"] == "P-256"
    assert "warning" in data


def test_create_anchor():
    """Test creating a time anchor."""
    request = TimeAnchorRequest(
        batch_id="test-batch-001",
        chain_head_hash="a" * 64,
        chain_tail_hash="b" * 64,
        record_count=10,
        device_reported_start_time="2026-11-17T10:00:00+00:00",
        device_reported_end_time="2026-11-17T10:10:00+00:00",
    )

    resp = client.post("/time-anchor/anchor", json=request.model_dump())
    assert resp.status_code == 200
    data = resp.json()

    assert "anchor_id" in data
    assert data["batch_id"] == "test-batch-001"
    assert "server_received_at" in data
    assert "challenge" in data
    assert "server_signature" in data
    assert data["chain_head_hash"] == "a" * 64
    assert data["chain_tail_hash"] == "b" * 64
    assert data["record_count"] == 10


def test_create_anchor_duplicate_batch_id():
    """Test that duplicate batch_id is rejected."""
    request = TimeAnchorRequest(
        batch_id="test-batch-dup",
        chain_head_hash="a" * 64,
        chain_tail_hash="b" * 64,
        record_count=10,
        device_reported_start_time="2026-11-17T10:00:00+00:00",
        device_reported_end_time="2026-11-17T10:10:00+00:00",
    )

    # First submission
    resp1 = client.post("/time-anchor/anchor", json=request.model_dump())
    assert resp1.status_code == 200

    # Duplicate submission with same content
    resp2 = client.post("/time-anchor/anchor", json=request.model_dump())
    assert resp2.status_code == 409
    assert "duplicate" in resp2.json()["detail"].lower()


def test_create_anchor_conflict():
    """Test that conflicting batch_id is rejected."""
    request1 = TimeAnchorRequest(
        batch_id="test-batch-conflict",
        chain_head_hash="a" * 64,
        chain_tail_hash="b" * 64,
        record_count=10,
        device_reported_start_time="2026-11-17T10:00:00+00:00",
        device_reported_end_time="2026-11-17T10:10:00+00:00",
    )

    request2 = TimeAnchorRequest(
        batch_id="test-batch-conflict",  # Same batch_id
        chain_head_hash="c" * 64,  # Different content
        chain_tail_hash="d" * 64,
        record_count=20,
        device_reported_start_time="2026-11-17T11:00:00+00:00",
        device_reported_end_time="2026-11-17T11:20:00+00:00",
    )

    # First submission
    resp1 = client.post("/time-anchor/anchor", json=request1.model_dump())
    assert resp1.status_code == 200

    # Conflicting submission
    resp2 = client.post("/time-anchor/anchor", json=request2.model_dump())
    assert resp2.status_code == 409
    assert "conflict" in resp2.json()["detail"].lower()


def test_verify_anchor():
    """Test verifying an anchor receipt."""
    # Create anchor
    request = TimeAnchorRequest(
        batch_id="test-batch-verify",
        chain_head_hash="a" * 64,
        chain_tail_hash="b" * 64,
        record_count=10,
        device_reported_start_time="2026-11-17T10:00:00+00:00",
        device_reported_end_time="2026-11-17T10:10:00+00:00",
    )

    resp = client.post("/time-anchor/anchor", json=request.model_dump())
    anchor_data = resp.json()

    # Get receipt
    resp = client.get(f"/time-anchor/receipt/{anchor_data['anchor_id']}")
    receipt = resp.json()

    # Verify receipt
    resp = client.post("/time-anchor/verify", json=receipt)
    assert resp.status_code == 200
    verification = resp.json()

    assert verification["valid"] is True
    assert verification["signature_valid"] is True
    assert verification["challenge_matches"] is True
    assert verification["trust_level"] == TimeTrustLevel.SERVER_ANCHORED
    assert len(verification["warnings"]) > 0


def test_verify_invalid_receipt():
    """Test verifying an invalid receipt."""
    from app.models.time_anchor import TimeAnchorReceipt

    # Create a fake receipt
    fake_receipt = TimeAnchorReceipt(
        anchor_id="fake-anchor-id",
        batch_id="fake-batch",
        server_received_at="2026-11-17T10:00:00+00:00",
        challenge="fake-challenge",
        server_signature="fake-signature",
        chain_head_hash="a" * 64,
        chain_tail_hash="b" * 64,
        record_count=10,
        device_reported_start_time="2026-11-17T09:00:00+00:00",
        device_reported_end_time="2026-11-17T10:00:00+00:00",
        stored_at="2026-11-17T10:00:00+00:00",
    )

    resp = client.post("/time-anchor/verify", json=fake_receipt.model_dump())
    assert resp.status_code == 200
    verification = resp.json()

    assert verification["valid"] is False
    assert verification["trust_level"] == TimeTrustLevel.UNVERIFIED


def test_get_receipt_not_found():
    """Test getting a non-existent receipt."""
    resp = client.get("/time-anchor/receipt/non-existent-id")
    assert resp.status_code == 404


def test_server_time_is_upper_bound():
    """Test that server time is an upper bound, not exact time."""
    request = TimeAnchorRequest(
        batch_id="test-batch-time",
        chain_head_hash="a" * 64,
        chain_tail_hash="b" * 64,
        record_count=10,
        device_reported_start_time="2020-01-01T00:00:00+00:00",  # Old device time
        device_reported_end_time="2020-01-01T01:00:00+00:00",
    )

    resp = client.post("/time-anchor/anchor", json=request.model_dump())
    anchor_data = resp.json()

    # Server time should be current, not the device time
    from datetime import datetime, timezone
    server_time = datetime.fromisoformat(anchor_data["server_received_at"])
    device_end_time = datetime.fromisoformat(request.device_reported_end_time)

    # Server time should be much later than device time
    assert server_time > device_end_time

    # Verify receipt should warn about this
    resp = client.get(f"/time-anchor/receipt/{anchor_data['anchor_id']}")
    receipt = resp.json()

    resp = client.post("/time-anchor/verify", json=receipt)
    verification = resp.json()

    # Check that warnings mention time limitations
    assert len(verification["warnings"]) > 0
    assert any("before" in w.lower() or "existed" in w.lower() for w in verification["warnings"])
