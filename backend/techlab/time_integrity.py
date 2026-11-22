"""T03 - Trusted time & tamper detection (tech-lab experiment).

Device clock anomaly detection: rollback, jumps, timezone changes,
non-monotonic capture sequences.

HONESTY RULES (risk-register T03):
- Offline devices have NO trustworthy reference clock. This detector can
  only flag *internal inconsistencies* within a capture session, and
  cross-checks against server anchors when they exist.
- Output is a graded confidence report, never a hard "time is genuine"
  verdict. Absence of anomalies is NOT proof the clock was correct.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class AnomalyKind(str, Enum):
    CLOCK_ROLLBACK = "clock_rollback"        # device time went backwards
    TIME_JUMP = "time_jump"                # implausibly large forward gap
    TIMEZONE_CHANGE = "timezone_change"     # utc-offset changed mid-session
    NON_MONOTONIC_SEQ = "non_monotonic_seq"  # seq order != time order
    ANCHOR_CONFLICT = "anchor_conflict"     # claims time AFTER server anchor


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class SessionAnomaly:
    kind: AnomalyKind
    severity: Severity
    at_index: int
    detail: str


@dataclass
class TimeIntegrityReport:
    anomalies: list[SessionAnomaly] = field(default_factory=list)
    verdict: str = "clean"
    confidence: str = "low"   # default-honest: without an anchor we know little

    def add(self, a: SessionAnomaly) -> None:
        self.anomalies.append(a)

    @property
    def has_critical(self) -> bool:
        return any(a.severity == Severity.CRITICAL for a in self.anomalies)


class CaptureTick(BaseModel):
    """One capture event as recorded offline by the device."""

    sequence: int
    device_time: str        # ISO 8601 as REPORTED by the device
    utc_offset: str = "+00:00"  # e.g. "+08:00" - offset at capture time
    monotonic_ms: int       # device monotonic clock (never goes backwards)

    def parsed_time(self) -> datetime:
        return datetime.fromisoformat(self.device_time)


def _offset_minutes(offset: str) -> int:
    sign = 1 if offset.startswith("+") else -1
    hh, mm = offset[1:].split(":")
    return sign * (int(hh) * 60 + int(mm))


# Tunable plausibility bounds (documented; tests pin the behaviour):
MAX_FORWARD_JUMP_S = 6 * 3600      # >6h forward inside a session = jump
ROLLBACK_TOLERANCE_MS = 2_000      # monotonic glitch tolerance
MONOTONIC_DRIFT_MS_PER_S = 50       # monotonic vs wall clock drift allowance


def analyze_session(ticks: list[CaptureTick], server_anchor_time: str | None = None) -> TimeIntegrityReport:
    """Detect internal time inconsistencies in an offline capture session.

    Checks (in order):
    1. monotonic clock must never go backwards (hardware-guaranteed);
    2. wall-clock time must not go backwards beyond tolerance;
    3. forward wall-clock jumps beyond MAX_FORWARD_JUMP_S are suspicious;
    4. utc-offset changes mid-session are flagged (user traveled or
       manual timezone change);
    5. sequence order must match device_time order;
    6. if a server anchor exists, no capture may claim to be AFTER it.
    """
    report = TimeIntegrityReport()
    if not ticks:
        report.verdict = "no_data"
        report.confidence = "none"
        return report

    ordered = sorted(ticks, key=lambda t: t.sequence)
    prev: CaptureTick | None = None
    for i, tick in enumerate(ordered):
        # 1. monotonic regression
        if prev is not None:
            mono_gap = tick.monotonic_ms - prev.monotonic_ms
            if mono_gap < -ROLLBACK_TOLERANCE_MS:
                report.add(SessionAnomaly(
                    AnomalyKind.CLOCK_ROLLBACK, Severity.CRITICAL, i,
                    f"monotonic clock regressed by {abs(mono_gap)} ms at seq={tick.sequence}",
                ))
            # 2. wall clock backwards
            wall_gap = (tick.parsed_time() - prev.parsed_time()).total_seconds()
            if wall_gap < -(ROLLBACK_TOLERANCE_MS / 1000.0):
                report.add(SessionAnomaly(
                    AnomalyKind.CLOCK_ROLLBACK, Severity.CRITICAL, i,
                    f"device time moved back {abs(wall_gap):.1f}s at seq={tick.sequence} "
                    f"while monotonic advanced {mono_gap} ms",
                ))
            # 3. implausible forward jump
            elif wall_gap > MAX_FORWARD_JUMP_S:
                report.add(SessionAnomaly(
                    AnomalyKind.TIME_JUMP, Severity.WARNING, i,
                    f"forward jump of {wall_gap:.0f}s at seq={tick.sequence}",
                ))
            # 4. timezone change
            if _offset_minutes(tick.utc_offset) != _offset_minutes(prev.utc_offset):
                report.add(SessionAnomaly(
                    AnomalyKind.TIMEZONE_CHANGE, Severity.INFO, i,
                    f"utc-offset changed {prev.utc_offset} -> {tick.utc_offset} at seq={tick.sequence}",
                ))
        prev = tick

    # 5. sequence vs time ordering
    times = [t.parsed_time() for t in ordered]
    if times != sorted(times):
        idx = next(i for i in range(1, len(times)) if times[i] < times[i - 1])
        report.add(SessionAnomaly(
            AnomalyKind.NON_MONOTONIC_SEQ, Severity.CRITICAL, idx,
            f"seq order disagrees with device_time order around seq={ordered[idx].sequence}",
        ))

    # 6. server anchor cross-check
    if server_anchor_time is not None:
        anchor = datetime.fromisoformat(server_anchor_time)
        for i, tick in enumerate(ordered):
            if tick.parsed_time() > anchor:
                report.add(SessionAnomaly(
                    AnomalyKind.ANCHOR_CONFLICT, Severity.CRITICAL, i,
                    f"seq={tick.sequence} claims time AFTER server anchor",
                ))
                break

    # Verdict + confidence (honest defaults)
    if report.has_critical:
        report.verdict = "tamper_indicators"
    elif report.anomalies:
        report.verdict = "suspicious"
    else:
        report.verdict = "internally_consistent"

    if server_anchor_time is None:
        report.confidence = "low"       # no external reference at all
    elif report.has_critical or report.anomalies:
        report.confidence = "low"
    else:
        report.confidence = "medium"    # anchored + internally consistent

    # NOTE: "medium" is the ceiling. Offline sessions can never reach "high"
    # because the device clock itself is untrusted between anchors.
    return report
