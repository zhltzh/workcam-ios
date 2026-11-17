"""
Tests for Offline Scenario Simulator (WC-W003-R1)

Tests various offline scenarios and their impact on time trust.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.time_anchor import TimeTrustLevel, VerificationStatus
from app.services.offline_simulator import OfflineSimulator


@pytest.fixture
def simulator():
    """Create a fresh simulator instance."""
    return OfflineSimulator()


def test_normal_session(simulator):
    """Test a normal offline session."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    session = simulator.create_normal_session(
        session_id="normal-001",
        num_records=10,
        start_time=start_time,
    )

    assert session.session_id == "normal-001"
    assert session.records_created == 10
    assert session.device_time_offset_seconds == 0
    assert session.record_times is not None
    assert len(session.record_times) == 10

    # Verify chain
    chain = simulator.chains[f"batch-normal-001"]
    valid, errors = chain.verify_chain_integrity()
    assert valid is True
    assert len(errors) == 0
    assert chain.get_record_count() == 10


def test_session_with_clock_offset(simulator):
    """Test session with device clock offset."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    session = simulator.create_normal_session(
        session_id="offset-001",
        num_records=10,
        start_time=start_time,
        device_time_offset_seconds=3600,  # 1 hour ahead
    )

    assert session.device_time_offset_seconds == 3600

    # Device time should be 1 hour ahead of actual time
    device_start = datetime.fromisoformat(session.start_time)
    actual_start = datetime.fromisoformat(session.actual_start_time)
    diff = (device_start - actual_start).total_seconds()
    assert abs(diff - 3600) < 1  # Allow 1 second tolerance


def test_clock_manipulation(simulator):
    """Test session where clock is changed mid-session."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    session, anomalies = simulator.simulate_clock_manipulation(
        session_id="clock-001",
        num_records=20,
        start_time=start_time,
        clock_change_after_records=10,
        clock_change_seconds=-3600,  # Jump back 1 hour
    )

    assert len(anomalies) > 0
    assert "clock changed" in anomalies[0].lower()

    # Chain should still be valid
    chain = simulator.chains[f"batch-clock-001"]
    valid, errors = chain.verify_chain_integrity()
    assert valid is True


def test_missing_records(simulator):
    """Test session with missing records."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    session, gaps = simulator.simulate_missing_records(
        session_id="missing-001",
        num_records=20,
        start_time=start_time,
        missing_indices=[5, 10, 15],
    )

    assert len(gaps) == 3
    assert session.records_created == 17  # 20 - 3

    # Chain should still be valid (just shorter)
    chain = simulator.chains[f"batch-missing-001"]
    valid, errors = chain.verify_chain_integrity()
    assert valid is True
    assert chain.get_record_count() == 17


def test_chain_reconstruction(simulator):
    """Test attacker trying to reconstruct chain."""
    # Create original session
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    simulator.create_normal_session(
        session_id="original-001",
        num_records=10,
        start_time=start_time,
    )

    # Try to reconstruct
    new_chain, differences = simulator.simulate_chain_reconstruction(
        original_session_id="original-001",
        new_session_id="reconstructed-001",
    )

    assert len(differences) == 10
    assert all("different photo_hash" in d for d in differences)

    # New chain should be valid but different
    valid, errors = new_chain.verify_chain_integrity()
    assert valid is True

    # But hashes should be different
    original_chain = simulator.chains["batch-original-001"]
    assert original_chain.get_head_hash() != new_chain.get_head_hash()
    assert original_chain.get_tail_hash() != new_chain.get_tail_hash()


def test_content_tampering_detection(simulator):
    """Test that content tampering is detected."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    simulator.create_normal_session(
        session_id="tamper-001",
        num_records=5,
        start_time=start_time,
    )

    chain = simulator.chains["batch-tamper-001"]

    # Initially no tampering
    clean, tampering = chain.detect_content_tampering()
    assert clean is True
    assert len(tampering) == 0

    # Tamper with a record
    chain.records[2].tamper_content("x" * 64)

    # Now tampering should be detected
    clean, tampering = chain.detect_content_tampering()
    assert clean is False
    assert len(tampering) > 0
    assert "record 2" in tampering[0].lower()


def test_time_trust_analysis_no_anchor(simulator):
    """Test time trust analysis without anchor."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    simulator.create_normal_session(
        session_id="analysis-001",
        num_records=10,
        start_time=start_time,
    )

    analysis = simulator.analyze_time_trust(
        "batch-analysis-001",
        has_anchor=False,
    )

    assert analysis.batch_id == "batch-analysis-001"
    assert analysis.trust_level == TimeTrustLevel.UNVERIFIED
    assert analysis.verification_status == VerificationStatus.DIGEST_ANCHORED
    assert len(analysis.cannot_prove) > 0
    assert any("no server anchor" in p.lower() for p in analysis.cannot_prove)


def test_time_trust_analysis_with_anchor(simulator):
    """Test time trust analysis with anchor."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    simulator.create_normal_session(
        session_id="analysis-002",
        num_records=10,
        start_time=start_time,
    )

    analysis = simulator.analyze_time_trust(
        "batch-analysis-002",
        has_anchor=True,
        verification_status=VerificationStatus.DIGEST_ANCHORED,
    )

    assert analysis.trust_level == TimeTrustLevel.SERVER_ANCHORED
    assert len(analysis.can_prove) > 0
    assert any("digest" in p.lower() for p in analysis.can_prove)
    assert any("chain" in p.lower() for p in analysis.cannot_prove)


def test_time_trust_analysis_chain_verified(simulator):
    """Test time trust analysis with chain verification."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    simulator.create_normal_session(
        session_id="analysis-003",
        num_records=10,
        start_time=start_time,
    )

    analysis = simulator.analyze_time_trust(
        "batch-analysis-003",
        has_anchor=True,
        verification_status=VerificationStatus.CHAIN_VERIFIED,
    )

    assert analysis.trust_level == TimeTrustLevel.SERVER_ANCHORED
    assert any("chain integrity" in p.lower() for p in analysis.can_prove)


def test_72_hour_offline_simulation(simulator):
    """Test 72-hour offline simulation with realistic distribution."""
    result = simulator.simulate_72_hour_offline(session_id="72h-001")

    assert result["offline_duration_hours"] == 72
    assert result["session"].records_created == 72
    assert result["analysis"].trust_level == TimeTrustLevel.UNVERIFIED  # No anchor yet

    # Check record distribution
    dist = result["record_distribution"]
    assert dist["total_records"] == 72
    assert dist["interval_seconds"] == 3600  # 1 hour
    assert dist["first_record_time"] is not None
    assert dist["last_record_time"] is not None

    # Records should be distributed across 72 hours
    first_time = datetime.fromisoformat(dist["first_record_time"])
    last_time = datetime.fromisoformat(dist["last_record_time"])
    duration = (last_time - first_time).total_seconds() / 3600
    assert 70 < duration < 74  # Approximately 72 hours


def test_chain_head_and_tail(simulator):
    """Test chain head and tail hashes."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    simulator.create_normal_session(
        session_id="headtail-001",
        num_records=5,
        start_time=start_time,
    )

    chain = simulator.chains["batch-headtail-001"]

    head = chain.get_head_hash()
    tail = chain.get_tail_hash()

    assert head is not None
    assert tail is not None
    assert head != tail  # Should be different
    assert len(head) == 64  # SHA-256 hex
    assert len(tail) == 64


def test_empty_chain(simulator):
    """Test empty chain."""
    from app.services.offline_simulator import OfflineChain

    chain = OfflineChain("empty-batch")
    assert chain.get_head_hash() is None
    assert chain.get_tail_hash() is None
    assert chain.get_record_count() == 0

    valid, errors = chain.verify_chain_integrity()
    assert valid is True  # Empty chain is valid


def test_single_record_chain(simulator):
    """Test chain with single record."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    simulator.create_normal_session(
        session_id="single-001",
        num_records=1,
        start_time=start_time,
    )

    chain = simulator.chains["batch-single-001"]
    assert chain.get_record_count() == 1

    head = chain.get_head_hash()
    tail = chain.get_tail_hash()
    assert head == tail  # Single record: head == tail

    valid, errors = chain.verify_chain_integrity()
    assert valid is True


def test_record_has_actual_timestamp(simulator):
    """Test that records track both device and actual timestamps."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    simulator.create_normal_session(
        session_id="timestamp-001",
        num_records=5,
        start_time=start_time,
        device_time_offset_seconds=3600,  # 1 hour offset
    )

    chain = simulator.chains["batch-timestamp-001"]

    for record in chain.records:
        assert record.device_timestamp is not None
        assert record.actual_timestamp is not None
        # They should be different due to offset
        device_time = datetime.fromisoformat(record.device_timestamp)
        actual_time = datetime.fromisoformat(record.actual_timestamp)
        diff = abs((device_time - actual_time).total_seconds())
        assert 3599 < diff < 3601  # Approximately 1 hour


def test_content_hash_vs_chain_hash(simulator):
    """Test that content hash and chain hash are different."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=1)
    simulator.create_normal_session(
        session_id="hash-001",
        num_records=3,
        start_time=start_time,
    )

    chain = simulator.chains["batch-hash-001"]

    for record in chain.records:
        assert record.content_hash is not None
        assert record.record_hash is not None
        # Content hash and chain hash should be different
        assert record.content_hash != record.record_hash
