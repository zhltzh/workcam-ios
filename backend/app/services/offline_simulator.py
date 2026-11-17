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

from app.models.time_anchor import (
    OfflineSession,
    TimeTrustAnalysis,
    TimeTrustLevel,
    VerificationStatus,
)


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
        self.content_hash: Optional[str] = None  # Hash of actual content

    def compute_hashes(self, previous_hash: Optional[str] = None) -> str:
        """Compute both content hash and chain hash."""
        self.previous_hash = previous_hash

        # Content hash: hash of actual record content
        content_data = (
            f"record_id={self.record_id}\n"
            f"photo_hash={self.photo_hash}\n"
            f"device_timestamp={self.device_timestamp}\n"
        )
        self.content_hash = hashlib.sha256(content_data.encode("utf-8")).hexdigest()

        # Chain hash: includes previous hash for chain integrity
        chain_data = (
            f"record_id={self.record_id}\n"
            f"photo_hash={self.photo_hash}\n"
            f"device_timestamp={self.device_timestamp}\n"
            f"previous_hash={previous_hash or 'none'}\n"
        )
        self.record_hash = hashlib.sha256(chain_data.encode("utf-8")).hexdigest()
        return self.record_hash

    def tamper_content(self, new_photo_hash: str):
        """Simulate content tampering."""
        self.photo_hash = new_photo_hash
        # Recompute hashes to reflect tampering
        if self.previous_hash is not None:
            self.compute_hashes(self.previous_hash)


class OfflineChain:
    """Simulates a chain of offline records."""

    def __init__(self, batch_id: str):
        self.batch_id = batch_id
        self.records: list[OfflineRecord] = []
        self._original_content_hashes: list[str] = []  # For tamper detection

    def add_record(self, record: OfflineRecord) -> str:
        """Add a record to the chain and compute its hash."""
        previous_hash = self.records[-1].record_hash if self.records else None
        record.compute_hashes(previous_hash)
        self.records.append(record)
        self._original_content_hashes.append(record.content_hash)
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

    def detect_content_tampering(self) -> tuple[bool, list[str]]:
        """Detect if any record content was tampered with."""
        tampering = []

        for i, record in enumerate(self.records):
            if i < len(self._original_content_hashes):
                # Recompute content hash
                content_data = (
                    f"record_id={record.record_id}\n"
                    f"photo_hash={record.photo_hash}\n"
                    f"device_timestamp={record.device_timestamp}\n"
                )
                current_content_hash = hashlib.sha256(content_data.encode("utf-8")).hexdigest()

                if current_content_hash != self._original_content_hashes[i]:
                    tampering.append(f"Record {i}: content hash mismatch (tampered)")

        return len(tampering) == 0, tampering


class OfflineSimulator:
    """
    Simulator for offline scenarios.

    Allows testing various edge cases and attack scenarios.

    IMPORTANT: The simulator knows the "ground truth" (actual times, actual content),
    but the client can only prove what was anchored by the server.
    """

    def __init__(self):
        self.chains: dict[str, OfflineChain] = {}
        self.sessions: dict[str, OfflineSession] = {}

    def create_normal_session(
        self,
        session_id: str,
        num_records: int,
        start_time: datetime,
        device_time_offset_seconds: int = 0,
        record_interval_seconds: int = 10,
    ) -> OfflineSession:
        """
        Create a normal offline session.

        Args:
            session_id: Unique identifier for the session
            num_records: Number of records to create
            start_time: Real start time
            device_time_offset_seconds: How much device clock differs from real time
            record_interval_seconds: Interval between records

        Returns:
            OfflineSession with metadata
        """
        batch_id = f"batch-{session_id}"
        chain = OfflineChain(batch_id)

        # Apply device time offset
        device_start = start_time + timedelta(seconds=device_time_offset_seconds)

        record_times = []
        for i in range(num_records):
            actual_time = start_time + timedelta(seconds=i * record_interval_seconds)
            device_time = device_start + timedelta(seconds=i * record_interval_seconds)

            record_times.append(actual_time.isoformat())

            record = OfflineRecord(
                record_id=f"rec-{session_id}-{i}",
                photo_hash=secrets.token_hex(32),
                device_timestamp=device_time.isoformat(),
                actual_timestamp=actual_time.isoformat(),
            )
            chain.add_record(record)

        self.chains[batch_id] = chain

        end_time = start_time + timedelta(seconds=(num_records - 1) * record_interval_seconds)
        device_end = end_time + timedelta(seconds=device_time_offset_seconds)

        session = OfflineSession(
            session_id=session_id,
            start_time=device_start.isoformat(),
            end_time=device_end.isoformat(),
            records_created=num_records,
            actual_start_time=start_time.isoformat(),
            actual_end_time=end_time.isoformat(),
            device_time_offset_seconds=device_time_offset_seconds,
            record_times=record_times,
        )
        self.sessions[session_id] = session

        return session

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
        record_times = []
        for i in range(num_records):
            if i == clock_change_after_records:
                device_time += timedelta(seconds=clock_change_seconds)
                anomalies.append(
                    f"Clock changed by {clock_change_seconds}s after record {i}"
                )

            actual_time = start_time + timedelta(seconds=i * 10)
            record_times.append(actual_time.isoformat())

            record = OfflineRecord(
                record_id=f"rec-{session_id}-{i}",
                photo_hash=secrets.token_hex(32),
                device_timestamp=device_time.isoformat(),
                actual_timestamp=actual_time.isoformat(),
            )
            chain.add_record(record)
            device_time += timedelta(seconds=10)

        self.chains[batch_id] = chain

        end_time = start_time + timedelta(seconds=(num_records - 1) * 10)
        session = OfflineSession(
            session_id=session_id,
            start_time=start_time.isoformat(),
            end_time=(device_time - timedelta(seconds=10)).isoformat(),
            records_created=num_records,
            actual_start_time=start_time.isoformat(),
            actual_end_time=end_time.isoformat(),
            record_times=record_times,
        )
        self.sessions[session_id] = session

        return session, anomalies

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
        record_times = []

        for i in range(num_records):
            actual_time = start_time + timedelta(seconds=i * 10)
            if i in missing_indices:
                gaps.append(f"Record {i} is missing")
                continue

            record_times.append(actual_time.isoformat())

            record = OfflineRecord(
                record_id=f"rec-{session_id}-{i}",
                photo_hash=secrets.token_hex(32),
                device_timestamp=actual_time.isoformat(),
                actual_timestamp=actual_time.isoformat(),
            )
            chain.add_record(record)

        self.chains[batch_id] = chain

        session = OfflineSession(
            session_id=session_id,
            start_time=start_time.isoformat(),
            end_time=(start_time + timedelta(seconds=(num_records - 1) * 10)).isoformat(),
            records_created=num_records - len(missing_indices),
            actual_start_time=start_time.isoformat(),
            actual_end_time=(start_time + timedelta(seconds=(num_records - 1) * 10)).isoformat(),
            record_times=record_times,
        )
        self.sessions[session_id] = session

        return session, gaps

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

    def analyze_time_trust(
        self,
        batch_id: str,
        has_anchor: bool = False,
        verification_status: VerificationStatus = VerificationStatus.DIGEST_ANCHORED,
    ) -> TimeTrustAnalysis:
        """
        Analyze the time trust for a batch of records.

        Args:
            batch_id: The batch to analyze
            has_anchor: Whether this batch has been anchored by server
            verification_status: What the server verified

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

        if has_anchor:
            # What we can prove with anchor
            can_prove.append("Digest existed before server anchor time")
            can_prove.append("Server signed these specific hashes at server_received_at")

            if verification_status == VerificationStatus.CHAIN_VERIFIED:
                can_prove.append("Chain integrity verified by server")
            else:
                cannot_prove.append("Chain integrity (server only signed digest)")

            if verification_status == VerificationStatus.PHOTO_VERIFIED:
                can_prove.append("Photo content verified by server")
            else:
                cannot_prove.append("Photo content (server only signed digest)")

            if verification_status == VerificationStatus.RECORD_COUNT_VERIFIED:
                can_prove.append("Record count verified by server")
            else:
                cannot_prove.append("Record count (server only signed digest)")
        else:
            # No anchor
            cannot_prove.append("Any time evidence (no server anchor)")
            risks.append("No server anchor means no time proof")

        # What we cannot prove regardless
        cannot_prove.append("Exact photo creation time")
        cannot_prove.append("Device clock was accurate")
        cannot_prove.append("Photos were taken at claimed location")

        # Risks
        risks.append("Device clock may be falsified")
        risks.append("Records may have been deleted before sync")
        risks.append("Chain may have been reconstructed with different content")

        # Recommendations
        recommendations.append("Sync frequently to reduce offline window")
        recommendations.append("Use server time as upper bound, not exact time")
        if verification_status == VerificationStatus.DIGEST_ANCHORED:
            recommendations.append("Submit full records for higher verification status")
        recommendations.append("Implement App Attest for stronger device identity")

        # Determine trust level
        if has_anchor:
            trust_level = TimeTrustLevel.SERVER_ANCHORED
        else:
            trust_level = TimeTrustLevel.UNVERIFIED

        return TimeTrustAnalysis(
            batch_id=batch_id,
            can_prove=can_prove,
            cannot_prove=cannot_prove,
            trust_level=trust_level,
            risks=risks,
            recommendations=recommendations,
            verification_status=verification_status,
        )

    def simulate_72_hour_offline(self, session_id: str) -> dict:
        """
        Simulate a 72-hour offline session with realistic record distribution.

        Records are distributed across the 72 hours, not just at the start.

        Returns:
            Dictionary with simulation results and analysis
        """
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(hours=72)

        # Create records distributed across 72 hours
        # Assume ~1 record per hour on average
        num_records = 72
        record_interval = 3600  # 1 hour in seconds

        session = self.create_normal_session(
            session_id=session_id,
            num_records=num_records,
            start_time=start_time,
            device_time_offset_seconds=0,
            record_interval_seconds=record_interval,
        )

        analysis = self.analyze_time_trust(f"batch-{session_id}")

        return {
            "session": session,
            "analysis": analysis,
            "offline_duration_hours": 72,
            "record_distribution": {
                "total_records": num_records,
                "interval_seconds": record_interval,
                "first_record_time": session.record_times[0] if session.record_times else None,
                "last_record_time": session.record_times[-1] if session.record_times else None,
            },
            "trust_assessment": {
                "can_prove": analysis.can_prove,
                "cannot_prove": analysis.cannot_prove,
                "risks": analysis.risks,
                "confidence": "LOW - 72 hours is a long offline period",
            },
        }
