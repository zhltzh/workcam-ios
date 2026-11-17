"""
Offline Scenario Simulator for WC-W003

This module simulates various offline scenarios to test the time anchoring system.

EXPERIMENTAL: This is a research prototype, NOT for production use.

Scenarios:
1. Normal offline session: Device creates records offline, then syncs
2. Clock manipulation: Device clock is changed during offline session
3. Device restart: Device restarts during offline session
4. Missing records: Some records are deleted before sync
5. Chain reconstruction: Attacker tries to rebuild the chain
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.time_anchor import OfflineSession, TimeTrustAnalysis, TimeTrustLevel


class OfflineRecord:
    """Simulates a single offline record."""

    def __init__(
        self,
        record_id: str,
        photo_hash: str,
        device_timestamp: str,
        actual_timestamp: Optional[str] = None,
    ):
        self.record_id = record_id
        self.photo_hash = photo_hash
        self.device_timestamp = device_timestamp
        self.actual_timestamp = actual_timestamp or device_timestamp
        self.previous_hash: Optional[str] = None
        self.record_hash: Optional[str] = None

    def compute_hash(self, previous_hash: Optional[str] = None) -> str:
        """Compute the hash of this record."""
        self.previous_hash = previous_hash
        data = (
            f"record_id={self.record_id}\n"
            f"photo_hash={self.photo_hash}\n"
            f"device_timestamp={self.device_timestamp}\n"
            f"previous_hash={previous_hash or 'none'}\n"
        )
        self.record_hash = hashlib.sha256(data.encode("utf-8")).hexdigest()
        return self.record_hash


class OfflineChain:
    """Simulates a chain of offline records."""

    def __init__(self, batch_id: str):
        self.batch_id = batch_id
        self.records: list[OfflineRecord] = []

    def add_record(self, record: OfflineRecord) -> str:
        """Add a record to the chain and compute its hash."""
        previous_hash = self.records[-1].record_hash if self.records else None
        record.compute_hash(previous_hash)
        self.records.append(record)
        return record.record_hash

    def get_head_hash(self) -> Optional[str]:
        """Get the hash of the first record."""
        return self.records[0].record_hash if self.records else None

    def get_tail_hash(self) -> Optional[str]:
        """Get the hash of the last record."""
        return self.records[-1].record_hash if self.records else None

    def get_record_count(self) -> int:
        """Get the number of records in the chain."""
        return len(self.records)

    def verify_chain_integrity(self) -> tuple[bool, list[str]]:
        """Verify the chain integrity."""
        errors = []
        previous_hash = None

        for i, record in enumerate(self.records):
            if record.previous_hash != previous_hash:
                errors.append(f"Record {i}: previous_hash mismatch")
            if record.record_hash is None:
                errors.append(f"Record {i}: record_hash not computed")
            previous_hash = record.record_hash

        return len(errors) == 0, errors


class OfflineSimulator:
    """
    Simulator for offline scenarios.

    Allows testing various edge cases and attack scenarios.
    """

    def __init__(self):
        self.chains: dict[str, OfflineChain] = {}

    def create_normal_session(
        self,
        session_id: str,
        num_records: int,
        start_time: datetime,
        device_time_offset_seconds: int = 0,
    ) -> OfflineSession:
        """
        Create a normal offline session.

        Args:
            session_id: Unique identifier for the session
            num_records: Number of records to create
            start_time: Real start time
            device_time_offset_seconds: How much device clock differs from real time

        Returns:
            OfflineSession with metadata
        """
        batch_id = f"batch-{session_id}"
        chain = OfflineChain(batch_id)

        # Apply device time offset
        device_start = start_time + timedelta(seconds=device_time_offset_seconds)

        for i in range(num_records):
            record_time = device_start + timedelta(seconds=i * 10)  # 10 seconds apart
            record = OfflineRecord(
                record_id=f"rec-{session_id}-{i}",
                photo_hash=secrets.token_hex(32),
                device_timestamp=record_time.isoformat(),
                actual_timestamp=(start_time + timedelta(seconds=i * 10)).isoformat(),
            )
            chain.add_record(record)

        self.chains[batch_id] = chain

        end_time = start_time + timedelta(seconds=(num_records - 1) * 10)
        device_end = end_time + timedelta(seconds=device_time_offset_seconds)

        return OfflineSession(
            session_id=session_id,
            start_time=device_start.isoformat(),
            end_time=device_end.isoformat(),
            records_created=num_records,
            actual_start_time=start_time.isoformat(),
            actual_end_time=end_time.isoformat(),
            device_time_offset_seconds=device_time_offset_seconds,
        )

    def simulate_clock_manipulation(
        self,
        session_id: str,
        num_records: int,
        start_time: datetime,
        clock_change_after_records: int,
        clock_change_seconds: int,
    ) -> tuple[OfflineSession, list[str]]:
        """
        Simulate a session where the device clock is changed mid-session.

        Args:
            session_id: Unique identifier
            num_records: Total number of records
            start_time: Real start time
            clock_change_after_records: Change clock after this many records
            clock_change_seconds: How much to change the clock (can be negative)

        Returns:
            (OfflineSession, list of anomalies detected)
        """
        batch_id = f"batch-{session_id}"
        chain = OfflineChain(batch_id)
        anomalies = []

        device_time = start_time
        for i in range(num_records):
            if i == clock_change_after_records:
                device_time += timedelta(seconds=clock_change_seconds)
                anomalies.append(
                    f"Clock changed by {clock_change_seconds}s after record {i}"
                )

            record = OfflineRecord(
                record_id=f"rec-{session_id}-{i}",
                photo_hash=secrets.token_hex(32),
                device_timestamp=device_time.isoformat(),
                actual_timestamp=(start_time + timedelta(seconds=i * 10)).isoformat(),
            )
            chain.add_record(record)
            device_time += timedelta(seconds=10)

        self.chains[batch_id] = chain

        end_time = start_time + timedelta(seconds=(num_records - 1) * 10)
        return (
            OfflineSession(
                session_id=session_id,
                start_time=start_time.isoformat(),
                end_time=(device_time - timedelta(seconds=10)).isoformat(),
                records_created=num_records,
                actual_start_time=start_time.isoformat(),
                actual_end_time=end_time.isoformat(),
            ),
            anomalies,
        )

    def simulate_missing_records(
        self,
        session_id: str,
        num_records: int,
        start_time: datetime,
        missing_indices: list[int],
    ) -> tuple[OfflineSession, list[str]]:
        """
        Simulate a session where some records are deleted before sync.

        Args:
            session_id: Unique identifier
            num_records: Total number of records created
            start_time: Real start time
            missing_indices: Indices of records to delete

        Returns:
            (OfflineSession, list of gaps detected)
        """
        batch_id = f"batch-{session_id}"
        chain = OfflineChain(batch_id)
        gaps = []

        for i in range(num_records):
            if i in missing_indices:
                gaps.append(f"Record {i} is missing")
                continue

            record = OfflineRecord(
                record_id=f"rec-{session_id}-{i}",
                photo_hash=secrets.token_hex(32),
                device_timestamp=(start_time + timedelta(seconds=i * 10)).isoformat(),
            )
            chain.add_record(record)

        self.chains[batch_id] = chain

        return (
            OfflineSession(
                session_id=session_id,
                start_time=start_time.isoformat(),
                end_time=(start_time + timedelta(seconds=(num_records - 1) * 10)).isoformat(),
                records_created=num_records - len(missing_indices),
            ),
            gaps,
        )

    def simulate_chain_reconstruction(
        self,
        original_session_id: str,
        new_session_id: str,
    ) -> tuple[OfflineChain, list[str]]:
        """
        Simulate an attacker trying to reconstruct the chain with different records.

        Args:
            original_session_id: Original session to attack
            new_session_id: New session ID for the reconstructed chain

        Returns:
            (Reconstructed chain, list of differences)
        """
        original_batch_id = f"batch-{original_session_id}"
        original_chain = self.chains.get(original_batch_id)
        if not original_chain:
            raise ValueError(f"Original chain not found: {original_batch_id}")

        new_batch_id = f"batch-{new_session_id}"
        new_chain = OfflineChain(new_batch_id)
        differences = []

        # Rebuild chain with same structure but different content
        for i, orig_record in enumerate(original_chain.records):
            new_record = OfflineRecord(
                record_id=f"rec-{new_session_id}-{i}",
                photo_hash=secrets.token_hex(32),  # Different photo hash
                device_timestamp=orig_record.device_timestamp,
            )
            new_chain.add_record(new_record)
            differences.append(f"Record {i}: different photo_hash")

        self.chains[new_batch_id] = new_chain
        return new_chain, differences

    def analyze_time_trust(self, batch_id: str) -> TimeTrustAnalysis:
        """
        Analyze the time trust for a batch of records.

        Returns:
            TimeTrustAnalysis explaining what can and cannot be proven
        """
        chain = self.chains.get(batch_id)
        if not chain:
            raise ValueError(f"Chain not found: {batch_id}")

        can_prove = []
        cannot_prove = []
        risks = []
        recommendations = []

        # What we can prove
        can_prove.append("Records existed before server anchor time")
        can_prove.append("Chain integrity (if all hashes match)")
        can_prove.append("Number of records in batch")

        # What we cannot prove
        cannot_prove.append("Exact creation time of each record")
        cannot_prove.append("Device clock was accurate")
        cannot_prove.append("No records were deleted")
        cannot_prove.append("Chain was not reconstructed")

        # Risks
        risks.append("Device clock may be falsified")
        risks.append("Records may have been deleted before sync")
        risks.append("Chain may have been reconstructed with different content")
        risks.append("Long offline periods increase uncertainty")

        # Recommendations
        recommendations.append("Sync frequently to reduce offline window")
        recommendations.append("Use server time as upper bound, not exact time")
        recommendations.append("Verify chain integrity before trusting timestamps")
        recommendations.append("Implement App Attest for stronger device identity")

        return TimeTrustAnalysis(
            batch_id=batch_id,
            can_prove=can_prove,
            cannot_prove=cannot_prove,
            trust_level=TimeTrustLevel.SERVER_ANCHORED,
            risks=risks,
            recommendations=recommendations,
        )

    def simulate_72_hour_offline(self, session_id: str) -> dict:
        """
        Simulate a 72-hour offline session.

        Returns:
            Dictionary with simulation results and analysis
        """
        start_time = datetime.now(timezone.utc) - timedelta(hours=72)
        session = self.create_normal_session(
            session_id=session_id,
            num_records=100,  # Assume 100 records over 72 hours
            start_time=start_time,
            device_time_offset_seconds=0,
        )

        analysis = self.analyze_time_trust(f"batch-{session_id}")

        return {
            "session": session,
            "analysis": analysis,
            "offline_duration_hours": 72,
            "trust_assessment": {
                "can_prove": analysis.can_prove,
                "cannot_prove": analysis.cannot_prove,
                "risks": analysis.risks,
                "confidence": "LOW - 72 hours is a long offline period",
            },
        }
