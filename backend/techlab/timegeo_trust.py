"""B02-W024 - Offline time & geo trust model (tech-lab, Windows side).

Formal trust state machines for offline capture time and location evidence.
Builds on W-003 time anchors (docs/offline-time-trust.md), T02
(location_meta) and T03 (time_integrity). NOT wired into production
routes; production protocol evolution is proposed in
docs/proposals/time-geo-trust-protocol-v3.md only.

FOUR TIME CATEGORIES (strict, never interchangeable):
- SERVER_ANCHORED : server_received_at from a signature-verified anchor.
  Semantics: existence-before bound ONLY (the digest existed before that
  time). It is NEVER the capture time and NEVER proof of when the photo
  was taken.
- DEVICE_REPORTED : device wall-clock reading. Unverified, affected by
  manual system-time changes.
- EXTRAPOLATED    : estimate = anchor wall time + monotonic delta since
  the anchor. An ESTIMATE with a growing error bound, not a
  server-certified capture time.
- UNVERIFIABLE    : no usable reference at all (no anchor, monotonic
  chain broken, clock anomalies without recovery).

WORDING RULES (enforced by assert_wording_safety):
- extrapolated times must be labeled "estimate", never
  "server-verified"/"certified".
- ordinary location fixes are best-effort GNSS positions, never an
  unforgeable "position proof". Manual coordinates are user input, not
  verified positions.

DEVICE MODEL ASSUMPTIONS (to be validated on real iPhone - Mac side,
status DEVICE_PENDING, see docs/mac-experiments/b02-w024-device-pending.md):
- A1: monotonic clock does not change when the user edits system time.
- A2: monotonic clock resets to zero on device reboot (boot_id must
  change with it).
- A3: iOS provides no public API that reliably detects mock locations;
  this model therefore treats simulation detection as a bounded
  heuristic (explicit flags + plausibility), not a guarantee.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from techlab.location_meta import (
    LocationMetadata,
    LocationQuality,
    LocationSource,
)

# ---------------------------------------------------------------------------
# Tunable thresholds (documented in docs/time-geo-trust-model.md; tests pin)
# ---------------------------------------------------------------------------

ROLLBACK_TOLERANCE_S = 2.0            # wall-clock backstep below this = glitch
MAX_FORWARD_JUMP_S = 6 * 3600.0       # > 6h forward inside one boot = jump
FRESH_WINDOW_S = 24 * 3600.0          # extrapolation freshness window
STALE_WINDOW_S = 72 * 3600.0          # last window before anchor expiry
MAX_EXTRAPOLATION_ERROR_S = 15.0      # estimate error bound ceiling
EXTRAPOLATION_BASE_ERROR_S = 5.0     # anchor-side uncertainty (RTT/clock set)
MONOTONIC_DRIFT_PPM = 50.0            # conservative crystal drift allowance
STALE_FIX_S = 300.0                   # fix older than this vs capture = stale
MAX_PLAUSIBLE_SPEED_MPS = 85.0        # ~306 km/h; above = teleportation hint
GNSS_PERFECT_ACCURACY_M = 1.0         # repeated <=1m fixes = simulation hint
GNSS_PERFECT_REPEATS = 3

_EXTRAPOLATION_DRIFT_S_PER_H = MONOTONIC_DRIFT_PPM * 1e-6 * 3600.0  # 0.18 s/h


# ---------------------------------------------------------------------------
# Time side: categories, states, events
# ---------------------------------------------------------------------------


class TimeCategory(str, Enum):
    """Which kind of time value backs a claim. See module docstring."""

    SERVER_ANCHORED = "server_anchored"
    DEVICE_REPORTED = "device_reported"
    EXTRAPOLATED = "extrapolated"
    UNVERIFIABLE = "unverifiable"


class TimeTrustState(str, Enum):
    NO_EVIDENCE = "no_evidence"
    SERVER_ANCHORED = "server_anchored"
    EXTRAPOLATED_FRESH = "extrapolated_fresh"
    EXTRAPOLATED_STALE = "extrapolated_stale"
    EXTRAPOLATED_EXPIRED = "extrapolated_expired"
    DEVICE_REPORTED_ONLY = "device_reported_only"
    UNVERIFIABLE_ANOMALY = "unverifiable_anomaly"
    DEGRADED_RECOVERED = "degraded_recovered"


class TimeTrustEvent(str, Enum):
    ANCHOR_VERIFIED = "anchor_verified"
    ANCHOR_INVALID = "anchor_invalid"
    CAPTURE = "capture"
    WINDOW_ELAPSED = "window_elapsed"
    DRIFT_EXCEEDED = "drift_exceeded"
    WALL_ROLLBACK = "wall_rollback"
    FORWARD_JUMP = "forward_jump"
    MONOTONIC_RESET = "monotonic_reset"     # reboot: monotonic clock cleared
    TIMEZONE_SHIFT = "timezone_shift"         # local representation only (info)
    MANUAL_TZ_CHANGE = "manual_tz_change"    # UTC value jumped (suspicious)
    RE_ANCHOR = "re_anchor"                  # anchor established after anomaly
    SEQUENCE_CONFLICT = "sequence_conflict"


#: Explicit transition table. Self-loops mean "state kept, anomaly attached".
TIME_TRANSITIONS: dict[tuple[TimeTrustState, TimeTrustEvent], TimeTrustState] = {
    # Fresh start
    (TimeTrustState.NO_EVIDENCE, TimeTrustEvent.ANCHOR_VERIFIED): TimeTrustState.SERVER_ANCHORED,
    (TimeTrustState.NO_EVIDENCE, TimeTrustEvent.CAPTURE): TimeTrustState.DEVICE_REPORTED_ONLY,
    (TimeTrustState.NO_EVIDENCE, TimeTrustEvent.ANCHOR_INVALID): TimeTrustState.NO_EVIDENCE,
    # Anchored session continues offline
    (TimeTrustState.SERVER_ANCHORED, TimeTrustEvent.CAPTURE): TimeTrustState.EXTRAPOLATED_FRESH,
    (TimeTrustState.SERVER_ANCHORED, TimeTrustEvent.WALL_ROLLBACK): TimeTrustState.SERVER_ANCHORED,
    (TimeTrustState.SERVER_ANCHORED, TimeTrustEvent.TIMEZONE_SHIFT): TimeTrustState.SERVER_ANCHORED,
    (TimeTrustState.SERVER_ANCHORED, TimeTrustEvent.MANUAL_TZ_CHANGE): TimeTrustState.SERVER_ANCHORED,
    # Extrapolation ageing
    (TimeTrustState.EXTRAPOLATED_FRESH, TimeTrustEvent.CAPTURE): TimeTrustState.EXTRAPOLATED_FRESH,
    (TimeTrustState.EXTRAPOLATED_FRESH, TimeTrustEvent.WINDOW_ELAPSED): TimeTrustState.EXTRAPOLATED_STALE,
    (TimeTrustState.EXTRAPOLATED_FRESH, TimeTrustEvent.DRIFT_EXCEEDED): TimeTrustState.EXTRAPOLATED_EXPIRED,
    (TimeTrustState.EXTRAPOLATED_FRESH, TimeTrustEvent.WALL_ROLLBACK): TimeTrustState.EXTRAPOLATED_FRESH,
    (TimeTrustState.EXTRAPOLATED_FRESH, TimeTrustEvent.FORWARD_JUMP): TimeTrustState.EXTRAPOLATED_FRESH,
    (TimeTrustState.EXTRAPOLATED_FRESH, TimeTrustEvent.TIMEZONE_SHIFT): TimeTrustState.EXTRAPOLATED_FRESH,
    (TimeTrustState.EXTRAPOLATED_FRESH, TimeTrustEvent.MANUAL_TZ_CHANGE): TimeTrustState.EXTRAPOLATED_FRESH,
    (TimeTrustState.EXTRAPOLATED_STALE, TimeTrustEvent.CAPTURE): TimeTrustState.EXTRAPOLATED_STALE,
    (TimeTrustState.EXTRAPOLATED_STALE, TimeTrustEvent.WINDOW_ELAPSED): TimeTrustState.EXTRAPOLATED_EXPIRED,
    (TimeTrustState.EXTRAPOLATED_STALE, TimeTrustEvent.DRIFT_EXCEEDED): TimeTrustState.EXTRAPOLATED_EXPIRED,
    (TimeTrustState.EXTRAPOLATED_STALE, TimeTrustEvent.WALL_ROLLBACK): TimeTrustState.EXTRAPOLATED_STALE,
    (TimeTrustState.EXTRAPOLATED_STALE, TimeTrustEvent.TIMEZONE_SHIFT): TimeTrustState.EXTRAPOLATED_STALE,
    (TimeTrustState.EXTRAPOLATED_STALE, TimeTrustEvent.MANUAL_TZ_CHANGE): TimeTrustState.EXTRAPOLATED_STALE,
    (TimeTrustState.EXTRAPOLATED_EXPIRED, TimeTrustEvent.CAPTURE): TimeTrustState.EXTRAPOLATED_EXPIRED,
    (TimeTrustState.EXTRAPOLATED_EXPIRED, TimeTrustEvent.ANCHOR_VERIFIED): TimeTrustState.SERVER_ANCHORED,
    (TimeTrustState.EXTRAPOLATED_EXPIRED, TimeTrustEvent.RE_ANCHOR): TimeTrustState.SERVER_ANCHORED,
    # Wall clock is the only witness
    (TimeTrustState.DEVICE_REPORTED_ONLY, TimeTrustEvent.CAPTURE): TimeTrustState.DEVICE_REPORTED_ONLY,
    (TimeTrustState.DEVICE_REPORTED_ONLY, TimeTrustEvent.ANCHOR_VERIFIED): TimeTrustState.SERVER_ANCHORED,
    (TimeTrustState.DEVICE_REPORTED_ONLY, TimeTrustEvent.ANCHOR_INVALID): TimeTrustState.DEVICE_REPORTED_ONLY,
    (TimeTrustState.DEVICE_REPORTED_ONLY, TimeTrustEvent.WALL_ROLLBACK): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.DEVICE_REPORTED_ONLY, TimeTrustEvent.FORWARD_JUMP): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.DEVICE_REPORTED_ONLY, TimeTrustEvent.MANUAL_TZ_CHANGE): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.DEVICE_REPORTED_ONLY, TimeTrustEvent.MONOTONIC_RESET): TimeTrustState.DEVICE_REPORTED_ONLY,
    (TimeTrustState.DEVICE_REPORTED_ONLY, TimeTrustEvent.TIMEZONE_SHIFT): TimeTrustState.DEVICE_REPORTED_ONLY,
    # Anomalies poison extrapolation
    (TimeTrustState.EXTRAPOLATED_FRESH, TimeTrustEvent.MONOTONIC_RESET): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.EXTRAPOLATED_STALE, TimeTrustEvent.MONOTONIC_RESET): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.EXTRAPOLATED_EXPIRED, TimeTrustEvent.MONOTONIC_RESET): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.UNVERIFIABLE_ANOMALY, TimeTrustEvent.SEQUENCE_CONFLICT): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.UNVERIFIABLE_ANOMALY, TimeTrustEvent.ANCHOR_VERIFIED): TimeTrustState.SERVER_ANCHORED,
    (TimeTrustState.UNVERIFIABLE_ANOMALY, TimeTrustEvent.RE_ANCHOR): TimeTrustState.DEGRADED_RECOVERED,
    (TimeTrustState.UNVERIFIABLE_ANOMALY, TimeTrustEvent.ANCHOR_INVALID): TimeTrustState.UNVERIFIABLE_ANOMALY,
    # Recovery: session keeps a degraded flag, chain restarts normally
    (TimeTrustState.DEGRADED_RECOVERED, TimeTrustEvent.CAPTURE): TimeTrustState.EXTRAPOLATED_FRESH,
    (TimeTrustState.DEGRADED_RECOVERED, TimeTrustEvent.ANCHOR_VERIFIED): TimeTrustState.SERVER_ANCHORED,
    (TimeTrustState.DEGRADED_RECOVERED, TimeTrustEvent.MONOTONIC_RESET): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.DEGRADED_RECOVERED, TimeTrustEvent.WALL_ROLLBACK): TimeTrustState.DEGRADED_RECOVERED,
    (TimeTrustState.DEGRADED_RECOVERED, TimeTrustEvent.TIMEZONE_SHIFT): TimeTrustState.DEGRADED_RECOVERED,
    (TimeTrustState.DEGRADED_RECOVERED, TimeTrustEvent.MANUAL_TZ_CHANGE): TimeTrustState.DEGRADED_RECOVERED,
    (TimeTrustState.SERVER_ANCHORED, TimeTrustEvent.MONOTONIC_RESET): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.SERVER_ANCHORED, TimeTrustEvent.ANCHOR_INVALID): TimeTrustState.DEVICE_REPORTED_ONLY,
    (TimeTrustState.SERVER_ANCHORED, TimeTrustEvent.SEQUENCE_CONFLICT): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.EXTRAPOLATED_FRESH, TimeTrustEvent.SEQUENCE_CONFLICT): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.EXTRAPOLATED_STALE, TimeTrustEvent.SEQUENCE_CONFLICT): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.EXTRAPOLATED_EXPIRED, TimeTrustEvent.SEQUENCE_CONFLICT): TimeTrustState.UNVERIFIABLE_ANOMALY,
    (TimeTrustState.DEGRADED_RECOVERED, TimeTrustEvent.SEQUENCE_CONFLICT): TimeTrustState.UNVERIFIABLE_ANOMALY,
}


def transition(state: TimeTrustState, event: TimeTrustEvent) -> TimeTrustState:
    """Pure transition function; unknown pairs raise (no silent fallback)."""
    try:
        return TIME_TRANSITIONS[(state, event)]
    except KeyError:
        raise ValueError(f"no transition defined for {state} + {event}") from None


# ---------------------------------------------------------------------------
# Evidence records (what the state machine is bound to)
# ---------------------------------------------------------------------------


@dataclass
class AnchorEvidence:
    """A server anchor the client holds a receipt for.

    `monotonic_ms_at_anchor` / `boot_id` are NOT part of the production
    v2 protocol; they are the v3 proposal fields. Without them no
    extrapolation is possible (v2 reality), see
    docs/proposals/time-geo-trust-protocol-v3.md.
    """

    anchor_id: str
    server_received_at: datetime
    signature_valid: bool
    monotonic_ms_at_anchor: int | None = None   # None = v2 anchor, no mapping
    boot_id: str | None = None


@dataclass
class CaptureTimeEvidence:
    """Per-record time evidence as captured offline."""

    record_id: str
    sequence: int
    device_wall_time: datetime          # device-reported wall clock (aware)
    utc_offset: str = "+00:00"
    monotonic_ms: int | None = None      # None = device did not log it
    boot_id: str | None = None
    covered_by_anchor: str | None = None  # anchor_id if digest inside batch


@dataclass
class TrustAnomaly:
    kind: str
    detail: str
    evidence_field: str                  # which evidence row triggered it


@dataclass
class ExtrapolatedTime:
    """EXTRAPOLATED estimate with explicit error bound. An ESTIMATE."""

    estimate: datetime
    error_bound_s: float
    elapsed_since_anchor_s: float
    anchor_id: str

    def window(self) -> str:
        if self.elapsed_since_anchor_s <= FRESH_WINDOW_S:
            return "fresh"
        if self.elapsed_since_anchor_s <= STALE_WINDOW_S:
            return "stale"
        return "expired"


# ---------------------------------------------------------------------------
# Time evaluation (pure functions over evidence)
# ---------------------------------------------------------------------------


def extrapolate(anchor: AnchorEvidence, cap: CaptureTimeEvidence) -> ExtrapolatedTime | None:
    """Estimate capture time as anchor wall time + monotonic delta.

    Returns None when the anchor cannot seed extrapolation:
    - anchor signature invalid (not a trustworthy base point), or
    - anchor lacks monotonic mapping (v2 production protocol), or
    - capture is in a different boot (monotonic clock was cleared), or
    - capture monotonic reading missing.

    The result is an ESTIMATE, never a server-verified capture time.
    """
    if not anchor.signature_valid:
        return None
    if anchor.monotonic_ms_at_anchor is None or cap.monotonic_ms is None:
        return None
    if anchor.boot_id is not None and cap.boot_id is not None and anchor.boot_id != cap.boot_id:
        return None
    if anchor.boot_id is None and cap.boot_id is not None:
        # v2-era anchor cannot be tied to this boot at all
        return None
    delta_ms = cap.monotonic_ms - anchor.monotonic_ms_at_anchor
    if delta_ms < 0:
        return None   # capture precedes anchor: not an extrapolation case
    elapsed_s = delta_ms / 1000.0
    error = EXTRAPOLATION_BASE_ERROR_S + elapsed_s / 3600.0 * _EXTRAPOLATION_DRIFT_S_PER_H
    return ExtrapolatedTime(
        estimate=anchor.server_received_at + timedelta(seconds=elapsed_s),
        error_bound_s=round(error, 3),
        elapsed_since_anchor_s=elapsed_s,
        anchor_id=anchor.anchor_id,
    )


def _detect_wall_anomaly(
    prev: CaptureTimeEvidence | None, cap: CaptureTimeEvidence, prev_mono: int | None
) -> list[TrustAnomaly]:
    """Wall-clock vs monotonic consistency checks for one capture step."""
    out: list[TrustAnomaly] = []
    if prev is None:
        return out
    wall_gap = (cap.device_wall_time - prev.device_wall_time).total_seconds()
    mono_gap = None
    if cap.monotonic_ms is not None and prev_mono is not None:
        mono_gap = (cap.monotonic_ms - prev_mono) / 1000.0
        if mono_gap < -ROLLBACK_TOLERANCE_S:
            out.append(TrustAnomaly(
                "monotonic_reset",
                f"monotonic clock regressed {abs(mono_gap):.0f}s at seq={cap.sequence} "
                "(reboot or counter tamper)",
                "monotonic_ms",
            ))
    if wall_gap < -ROLLBACK_TOLERANCE_S:
        detail = f"wall clock moved back {abs(wall_gap):.1f}s at seq={cap.sequence}"
        if mono_gap is not None and mono_gap >= 0:
            detail += " while monotonic advanced (system time edited)"
        out.append(TrustAnomaly("wall_rollback", detail, "device_wall_time"))
    elif wall_gap > MAX_FORWARD_JUMP_S:
        out.append(TrustAnomaly(
            "forward_jump",
            f"wall clock jumped forward {wall_gap:.0f}s at seq={cap.sequence}",
            "device_wall_time",
        ))
    if cap.utc_offset != prev.utc_offset:
        # UTC-continuous representation change vs UTC-value jump
        prev_wall = prev.device_wall_time
        cur_wall = cap.device_wall_time
        implied_shift = wall_gap - (mono_gap or 0.0)
        if abs(implied_shift) <= ROLLBACK_TOLERANCE_S:
            out.append(TrustAnomaly(
                "timezone_shift",
                f"utc_offset {prev.utc_offset} -> {cap.utc_offset} at seq={cap.sequence} "
                "(local representation change, UTC continuity intact)",
                "utc_offset",
            ))
        else:
            out.append(TrustAnomaly(
                "manual_tz_change",
                f"utc_offset change at seq={cap.sequence} shifted UTC value by "
                f"{implied_shift:.0f}s (manual timezone edit suspected)",
                "utc_offset",
            ))
    return out


@dataclass
class TimeAssessment:
    state: TimeTrustState
    category: TimeCategory
    anomalies: list[TrustAnomaly] = field(default_factory=list)
    extrapolated: ExtrapolatedTime | None = None
    existence_bound: datetime | None = None    # SERVER_ANCHORED semantics
    anchor_id: str | None = None
    degraded_session: bool = False
    reason: str = ""

    def time_label(self) -> str:
        """Human-facing label. Wording red lines enforced here."""
        if self.state == TimeTrustState.NO_EVIDENCE:
            return "no time evidence"
        if self.state == TimeTrustState.SERVER_ANCHORED:
            return (
                "server-anchored existence bound: digest existed before "
                f"{self.existence_bound.isoformat() if self.existence_bound else '?'} "
                "(not the capture time)"
            )
        if self.state in (TimeTrustState.EXTRAPOLATED_FRESH,
                          TimeTrustState.EXTRAPOLATED_STALE,
                          TimeTrustState.DEGRADED_RECOVERED):
            est = self.extrapolated
            base = "device-estimated capture time"
            if est is not None:
                base += f" (±{est.error_bound_s:.1f}s estimate)"
            return base
        if self.state == TimeTrustState.EXTRAPOLATED_EXPIRED:
            return ("anchor expired: estimate withdrawn; only unverified "
                    "device-reported time remains")
        if self.state == TimeTrustState.DEVICE_REPORTED_ONLY:
            return "device-reported time (unverified, editable by user)"
        return "unverifiable time (clock anomalies without recovery)"

    def can_claim(self) -> list[str]:
        claims: list[str] = []
        if self.existence_bound is not None:
            claims.append("record existed before the server-anchored time")
        if self.state in (TimeTrustState.EXTRAPOLATED_FRESH,
                          TimeTrustState.EXTRAPOLATED_STALE,
                          TimeTrustState.DEGRADED_RECOVERED) and self.extrapolated:
            claims.append("capture time estimate within the stated error bound")
        if self.state == TimeTrustState.DEVICE_REPORTED_ONLY:
            claims.append("device-reported timestamp as unverified reference")
        return claims

    def cannot_claim(self) -> list[str]:
        out = ["exact capture time"]
        if self.state == TimeTrustState.SERVER_ANCHORED:
            out.append("that server_received_at is when the photo was taken")
        if self.state != TimeTrustState.DEVICE_REPORTED_ONLY:
            out.append("that device wall clock was untouched")
        if self.state == TimeTrustState.EXTRAPOLATED_EXPIRED:
            out.append("the extrapolated estimate (anchor expired)")
        if self.state == TimeTrustState.UNVERIFIABLE_ANOMALY:
            out.append("any time ordering inside the anomalous span")
        return out

def assess_time_state(
    anchor: AnchorEvidence | None,
    cap: CaptureTimeEvidence,
    prev: CaptureTimeEvidence | None = None,
    prev_mono: int | None = None,
    degraded_session: bool = False,
) -> TimeAssessment:
    """Assess one capture record against the latest valid anchor.

    Pure function over evidence. Applies the transition semantics of
    TIME_TRANSITIONS; the state also honours anomalies detected between
    `prev` and `cap` (rollback / jump / reboot / timezone).
    """
    anomalies = _detect_wall_anomaly(prev, cap, prev_mono)
    kinds = {a.kind for a in anomalies}

    # Direct anchor coverage: digest inside a signed batch.
    if anchor is not None and anchor.signature_valid and cap.covered_by_anchor == anchor.anchor_id:
        state = TimeTrustState.DEGRADED_RECOVERED if degraded_session \
            else TimeTrustState.SERVER_ANCHORED
        anchor_conflict = cap.device_wall_time > anchor.server_received_at
        if anchor_conflict:
            anomalies.append(TrustAnomaly(
                "anchor_conflict",
                f"seq={cap.sequence} claims a device time AFTER server_received_at "
                f"({anchor.server_received_at.isoformat()}); device time disqualified",
                "device_wall_time",
            ))
        a = TimeAssessment(
            state=state,
            category=TimeCategory.SERVER_ANCHORED,
            existence_bound=anchor.server_received_at,
            anchor_id=anchor.anchor_id,
            degraded_session=degraded_session,
            anomalies=anomalies,
            reason="record digest covered by signature-verified anchor batch"
                   + (" (anchor conflict detected)" if anchor_conflict else ""),
        )
        return a

    est = None
    if anchor is not None:
        est = extrapolate(anchor, cap)

    # Reboot poisons the monotonic mapping: no extrapolation possible.
    if "monotonic_reset" in kinds or (
        anchor is not None and est is None
        and anchor.boot_id is not None and cap.boot_id is not None
        and anchor.boot_id != cap.boot_id
    ):
        base = TimeAssessment(
            state=TimeTrustState.UNVERIFIABLE_ANOMALY,
            category=TimeCategory.UNVERIFIABLE,
            anomalies=anomalies,
            anchor_id=anchor.anchor_id if anchor else None,
            degraded_session=degraded_session,
            reason="monotonic chain broken (reboot/tamper); anchor mapping unusable",
        )
        if anchor is not None and anchor.signature_valid and cap.device_wall_time <= anchor.server_received_at:
            base.existence_bound = anchor.server_received_at
        return base

    # Extrapolation possible: monotonic chain intact and anchored.
    if est is not None:
        if est.error_bound_s > MAX_EXTRAPOLATION_ERROR_S:
            state = TimeTrustState.EXTRAPOLATED_EXPIRED
            category = TimeCategory.DEVICE_REPORTED
            est = None
            reason = ("extrapolation error bound exceeded; estimate withdrawn, "
                      "only unverified device time remains")
        elif est.elapsed_since_anchor_s <= FRESH_WINDOW_S:
            state = TimeTrustState.EXTRAPOLATED_FRESH
            category = TimeCategory.EXTRAPOLATED
            reason = "estimate inside freshness window"
        elif est.elapsed_since_anchor_s <= STALE_WINDOW_S:
            state = TimeTrustState.EXTRAPOLATED_STALE
            category = TimeCategory.EXTRAPOLATED
            reason = "estimate inside stale window; low confidence"
        else:
            state = TimeTrustState.EXTRAPOLATED_EXPIRED
            category = TimeCategory.DEVICE_REPORTED
            est = None
            reason = ("anchor older than stale window; estimate withdrawn, "
                      "only unverified device time remains")
        a = TimeAssessment(
            state=state,
            category=category,
            anomalies=anomalies,
            extrapolated=est,
            existence_bound=None,
            anchor_id=anchor.anchor_id if anchor else None,
            degraded_session=degraded_session,
            reason=reason,
        )
        # Wall-clock anomalies do not break the monotonic estimate, but they
        # are recorded and the device-reported time is disqualified.
        if "wall_rollback" in kinds or "forward_jump" in kinds or "manual_tz_change" in kinds:
            a.anomalies.append(TrustAnomaly(
                "device_time_disqualified",
                "device wall clock disqualified as reference; estimate from "
                "monotonic chain retained",
                "device_wall_time",
            ))
        return a

    # No extrapolation possible (v2 anchor without mapping, or no anchor).
    has_witness = cap.device_wall_time is not None
    if not has_witness:
        return TimeAssessment(
            state=TimeTrustState.NO_EVIDENCE,
            category=TimeCategory.UNVERIFIABLE,
            anomalies=anomalies,
            reason="no time fields present",
        )
    if "wall_rollback" in kinds or "forward_jump" in kinds or "manual_tz_change" in kinds:
        return TimeAssessment(
            state=TimeTrustState.UNVERIFIABLE_ANOMALY,
            category=TimeCategory.UNVERIFIABLE,
            anomalies=anomalies,
            degraded_session=degraded_session,
            reason="wall clock is the only witness and shows tamper indicators",
        )
    return TimeAssessment(
        state=TimeTrustState.DEVICE_REPORTED_ONLY,
        category=TimeCategory.DEVICE_REPORTED,
        anomalies=anomalies,
        degraded_session=degraded_session,
        reason="only device wall clock available (v2 anchor or none)",
    )


def sequence_conflict(records: list[CaptureTimeEvidence]) -> bool:
    """Sequence order must not disagree with device time order."""
    ordered = sorted(records, key=lambda r: r.sequence)
    times = [r.device_wall_time for r in ordered]
    return times != sorted(times)


# ---------------------------------------------------------------------------
# Location side: states + evaluation over T02 LocationMetadata
# ---------------------------------------------------------------------------


class LocationTrustState(str, Enum):
    NO_LOCATION = "no_location"
    GNSS_GRADE_A = "gnss_grade_a"
    GNSS_GRADE_B = "gnss_grade_b"
    GNSS_GRADE_C = "gnss_grade_c"
    GNSS_GRADE_D = "gnss_grade_d"
    NETWORK_DERIVED = "network_derived"
    MANUAL_INPUT = "manual_input"
    STALE_FIX = "stale_fix"
    SIMULATION_SUSPECT = "simulation_suspect"


class LocationEvent(str, Enum):
    GNSS_FIX = "gnss_fix"
    NETWORK_FIX = "network_fix"
    MANUAL_ENTRY = "manual_entry"
    FIX_AGED = "fix_aged"
    ACCURACY_DEGRADED = "accuracy_degraded"
    SIMULATION_SIGNAL = "simulation_signal"
    LOCATION_LOST = "location_lost"
    SPEED_IMPLAUSIBLE = "speed_implausible"


LOCATION_TRANSITIONS: dict[tuple[LocationTrustState, LocationEvent], LocationTrustState] = {
    (LocationTrustState.NO_LOCATION, LocationEvent.GNSS_FIX): LocationTrustState.GNSS_GRADE_A,  # graded in evaluate()
    (LocationTrustState.NO_LOCATION, LocationEvent.NETWORK_FIX): LocationTrustState.NETWORK_DERIVED,
    (LocationTrustState.NO_LOCATION, LocationEvent.MANUAL_ENTRY): LocationTrustState.MANUAL_INPUT,
    (LocationTrustState.NO_LOCATION, LocationEvent.LOCATION_LOST): LocationTrustState.NO_LOCATION,
    (LocationTrustState.GNSS_GRADE_A, LocationEvent.ACCURACY_DEGRADED): LocationTrustState.GNSS_GRADE_D,
    (LocationTrustState.GNSS_GRADE_B, LocationEvent.ACCURACY_DEGRADED): LocationTrustState.GNSS_GRADE_D,
    (LocationTrustState.GNSS_GRADE_C, LocationEvent.ACCURACY_DEGRADED): LocationTrustState.GNSS_GRADE_D,
    (LocationTrustState.GNSS_GRADE_A, LocationEvent.FIX_AGED): LocationTrustState.STALE_FIX,
    (LocationTrustState.GNSS_GRADE_B, LocationEvent.FIX_AGED): LocationTrustState.STALE_FIX,
    (LocationTrustState.GNSS_GRADE_C, LocationEvent.FIX_AGED): LocationTrustState.STALE_FIX,
    (LocationTrustState.GNSS_GRADE_D, LocationEvent.FIX_AGED): LocationTrustState.STALE_FIX,
    (LocationTrustState.NETWORK_DERIVED, LocationEvent.FIX_AGED): LocationTrustState.STALE_FIX,
    (LocationTrustState.GNSS_GRADE_A, LocationEvent.SIMULATION_SIGNAL): LocationTrustState.SIMULATION_SUSPECT,
    (LocationTrustState.GNSS_GRADE_B, LocationEvent.SIMULATION_SIGNAL): LocationTrustState.SIMULATION_SUSPECT,
    (LocationTrustState.GNSS_GRADE_C, LocationEvent.SIMULATION_SIGNAL): LocationTrustState.SIMULATION_SUSPECT,
    (LocationTrustState.GNSS_GRADE_D, LocationEvent.SIMULATION_SIGNAL): LocationTrustState.SIMULATION_SUSPECT,
    (LocationTrustState.NETWORK_DERIVED, LocationEvent.SIMULATION_SIGNAL): LocationTrustState.SIMULATION_SUSPECT,
    (LocationTrustState.MANUAL_INPUT, LocationEvent.SIMULATION_SIGNAL): LocationTrustState.MANUAL_INPUT,
    (LocationTrustState.STALE_FIX, LocationEvent.GNSS_FIX): LocationTrustState.GNSS_GRADE_A,
    (LocationTrustState.SIMULATION_SUSPECT, LocationEvent.GNSS_FIX): LocationTrustState.SIMULATION_SUSPECT,
    (LocationTrustState.GNSS_GRADE_A, LocationEvent.SPEED_IMPLAUSIBLE): LocationTrustState.SIMULATION_SUSPECT,
    (LocationTrustState.GNSS_GRADE_B, LocationEvent.SPEED_IMPLAUSIBLE): LocationTrustState.SIMULATION_SUSPECT,
    (LocationTrustState.GNSS_GRADE_C, LocationEvent.SPEED_IMPLAUSIBLE): LocationTrustState.SIMULATION_SUSPECT,
    (LocationTrustState.GNSS_GRADE_D, LocationEvent.SPEED_IMPLAUSIBLE): LocationTrustState.SIMULATION_SUSPECT,
}


@dataclass
class LocationAssessment:
    state: LocationTrustState
    quality: LocationQuality | None
    anomalies: list[TrustAnomaly] = field(default_factory=list)
    evidence: LocationMetadata | None = None
    reason: str = ""

    def location_label(self) -> str:
        """Wording red lines: manual/simulated must never look verified."""
        if self.state == LocationTrustState.NO_LOCATION:
            return "no location data"
        if self.state == LocationTrustState.MANUAL_INPUT:
            return "user-entered coordinates (input, not a verified position)"
        if self.state == LocationTrustState.SIMULATION_SUSPECT:
            return "simulation-suspect location (heuristic only; not a position claim)"
        if self.state == LocationTrustState.STALE_FIX:
            return "stale position fix: location at fix time, not at capture time"
        if self.state == LocationTrustState.NETWORK_DERIVED:
            return "network-derived coarse position (best effort, not a position claim)"
        if self.quality is not None:
            return f"best-effort GNSS position, grade {self.quality.value} (not tamper-proof)"
        return "best-effort position (not tamper-proof)"

    def is_position_claim(self) -> bool:
        """Only fresh, non-suspect GNSS/network fixes may back a claim."""
        return self.state in (
            LocationTrustState.GNSS_GRADE_A,
            LocationTrustState.GNSS_GRADE_B,
            LocationTrustState.GNSS_GRADE_C,
            LocationTrustState.GNSS_GRADE_D,
        ) or self.state == LocationTrustState.NETWORK_DERIVED


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def assess_location_state(
    loc: LocationMetadata | None,
    capture_time: datetime | None = None,
    prev: LocationMetadata | None = None,
    perfect_fix_streak: int = 0,
) -> LocationAssessment:
    """Grade one capture's location evidence.

    - source none / no fix -> NO_LOCATION
    - manual -> MANUAL_INPUT (user input; trust class differs from GNSS)
    - is_simulated or repeated too-perfect fixes or teleport speed ->
      SIMULATION_SUSPECT (bounded heuristic, NOT a detection guarantee)
    - fix older than STALE_FIX_S vs capture time -> STALE_FIX
    - network -> NETWORK_DERIVED (quality capped at C by T02 rules)
    - GNSS graded A-D by horizontal accuracy thresholds
    """
    anomalies: list[TrustAnomaly] = []
    if loc is None or loc.source == LocationSource.NONE or (loc.latitude is None or loc.longitude is None):
        return LocationAssessment(
            state=LocationTrustState.NO_LOCATION,
            quality=None,
            anomalies=anomalies,
            evidence=loc,
            reason="no location obtained",
        )

    if loc.source == LocationSource.MANUAL:
        return LocationAssessment(
            state=LocationTrustState.MANUAL_INPUT,
            quality=None,
            anomalies=anomalies,
            evidence=loc,
            reason="user-entered coordinates; trust class is input, not GNSS",
        )

    simulation_hits: list[str] = []
    if loc.is_simulated:
        simulation_hits.append("explicit simulated flag")
    if (loc.horizontal_accuracy is not None
            and loc.horizontal_accuracy <= GNSS_PERFECT_ACCURACY_M
            and perfect_fix_streak >= GNSS_PERFECT_REPEATS):
        simulation_hits.append(
            f"{perfect_fix_streak} consecutive fixes <= {GNSS_PERFECT_ACCURACY_M} m")
    if prev is not None and prev.latitude is not None and prev.longitude is not None \
            and loc.fix_timestamp and prev.fix_timestamp:
        t0 = datetime.fromisoformat(prev.fix_timestamp)
        t1 = datetime.fromisoformat(loc.fix_timestamp)
        dt = (t1 - t0).total_seconds()
        if dt > 0:
            d = _haversine_m(prev.latitude, prev.longitude, loc.latitude, loc.longitude)
            speed = d / dt
            if speed > MAX_PLAUSIBLE_SPEED_MPS:
                simulation_hits.append(
                    f"implied speed {speed:.0f} m/s exceeds {MAX_PLAUSIBLE_SPEED_MPS:.0f} m/s")
    if simulation_hits:
        return LocationAssessment(
            state=LocationTrustState.SIMULATION_SUSPECT,
            quality=None,
            anomalies=[TrustAnomaly("simulation_suspect", "; ".join(simulation_hits), "is_simulated/accuracy/speed")],
            evidence=loc,
            reason=(
                "mock-location heuristics triggered; iOS offers no public API "
                "guaranteeing mock detection (model assumption A3, DEVICE_PENDING)"
            ),
        )

    if capture_time is not None and loc.fix_timestamp is not None:
        fix_t = datetime.fromisoformat(loc.fix_timestamp)
        age = (capture_time - fix_t).total_seconds()
        if age > STALE_FIX_S:
            anomalies.append(TrustAnomaly(
                "stale_fix", f"fix is {age:.0f}s older than capture", "fix_timestamp"))
            return LocationAssessment(
                state=LocationTrustState.STALE_FIX,
                quality=loc.grade() if loc.source == LocationSource.GPS else LocationQuality.C,
                anomalies=anomalies,
                evidence=loc,
                reason="position fix predates capture by more than the staleness threshold",
            )

    if loc.source == LocationSource.NETWORK:
        return LocationAssessment(
            state=LocationTrustState.NETWORK_DERIVED,
            quality=LocationQuality.C,
            anomalies=anomalies,
            evidence=loc,
            reason="wifi/cell derived fix; quality capped at C",
        )

    q = loc.grade()
    state = {
        LocationQuality.A: LocationTrustState.GNSS_GRADE_A,
        LocationQuality.B: LocationTrustState.GNSS_GRADE_B,
        LocationQuality.C: LocationTrustState.GNSS_GRADE_C,
        LocationQuality.D: LocationTrustState.GNSS_GRADE_D,
    }[q]
    return LocationAssessment(
        state=state, quality=q, anomalies=anomalies, evidence=loc,
        reason=f"GNSS fix graded by horizontal accuracy ({q.value})",
    )


# ---------------------------------------------------------------------------
# Combined per-record report + wording safety
# ---------------------------------------------------------------------------


@dataclass
class TimeGeoTrustReport:
    record_id: str
    time: TimeAssessment
    location: LocationAssessment

    def combined_label(self) -> str:
        return f"[{self.record_id}] {self.time.time_label()} | {self.location.location_label()}"


_FORBIDDEN_TIME_PHRASES = ("server-verified capture time", "certified capture time",
                           "authenticated capture time", "true capture time")
_FORBIDDEN_GEO_PHRASES = ("unforgeable", "tamper-proof position", "verified position proof",
                          "verified user-entered", "verified manual",
                          "verified GNSS", "verified location")


def assert_wording_safety(report: TimeGeoTrustReport) -> None:
    """Enforce the wording red lines; raises AssertionError on violation."""
    t_label = report.time.time_label()
    for phrase in _FORBIDDEN_TIME_PHRASES:
        assert phrase not in t_label, f"time label must not claim '{phrase}': {t_label}"
    if report.time.category == TimeCategory.EXTRAPOLATED:
        assert "estimate" in t_label, f"extrapolated label must say estimate: {t_label}"
    g_label = report.location.location_label()
    for phrase in _FORBIDDEN_GEO_PHRASES:
        assert phrase not in g_label, f"location label must not claim '{phrase}': {g_label}"
    if report.location.state == LocationTrustState.MANUAL_INPUT:
        assert "not a verified position" in g_label
    if report.location.state == LocationTrustState.SIMULATION_SUSPECT:
        assert "not a position claim" in g_label


# ---------------------------------------------------------------------------
# Retroactive anchoring (records recovered after long offline periods)
# ---------------------------------------------------------------------------


@dataclass
class RetroactiveUpdate:
    record_id: str
    existence_bound: datetime | None
    state_changed: bool
    note: str


def apply_late_anchor(
    assessments: dict[str, TimeAssessment],
    caps: dict[str, CaptureTimeEvidence],
    anchor: AnchorEvidence,
) -> list[RetroactiveUpdate]:
    """Re-anchor a batch of records that synced late.

    Retroactive policy (explicit, tested):
    1. A late anchor NEVER upgrades a record's time state. EXTRAPOLATED
       stays EXTRAPOLATED (an estimate), UNVERIFIABLE_ANOMALY stays
       UNVERIFIABLE_ANOMALY.
    2. It only appends an existence-before bound (server_received_at)
       when the record's time evidence does not already contradict it.
    3. Records claiming to exist AFTER server_received_at are anchor
       conflicts and keep their state plus a conflict note.
    """
    out: list[RetroactiveUpdate] = []
    for rid, cap in caps.items():
        prior = assessments.get(rid)
        if prior is None:
            continue
        bound: datetime | None = None
        state_changed = False
        note = ""
        if anchor.signature_valid and cap.device_wall_time is not None:
            if cap.device_wall_time > anchor.server_received_at:
                note = ("anchor conflict: device-reported time is after "
                        f"server_received_at ({anchor.server_received_at.isoformat()}); "
                        "state kept, bound not attached")
            elif prior.existence_bound is None:
                bound = anchor.server_received_at
                state_changed = True
                note = ("existence-before bound appended; time state NOT upgraded "
                        "(anchor proves existence, never capture time)")
            else:
                note = "existence bound already present; unchanged"
        else:
            note = "anchor unusable (signature invalid); nothing retroactively changed"
        out.append(RetroactiveUpdate(
            record_id=rid,
            existence_bound=bound,
            state_changed=state_changed,
            note=note,
        ))
    return out


def assess_offline_session(
    anchor: AnchorEvidence | None,
    records: list[CaptureTimeEvidence],
) -> list[TimeAssessment]:
    """Walk a capture sequence, applying per-step anomaly detection.

    Mirrors transition() semantics: this is the reference evaluator used
    by tests to cross-check the explicit transition table.
    """
    results: list[TimeAssessment] = []
    prev: CaptureTimeEvidence | None = None
    degraded = False
    for cap in records:
        a = assess_time_state(anchor, cap, prev=prev,
                              prev_mono=prev.monotonic_ms if prev else None,
                              degraded_session=degraded)
        if a.state == TimeTrustState.UNVERIFIABLE_ANOMALY:
            degraded = True
        results.append(a)
        prev = cap
    return results
