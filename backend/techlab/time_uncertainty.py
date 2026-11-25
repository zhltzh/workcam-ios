"""TM10: offline time-UNCERTAINTY interval engine.

W024's state machine decides WHICH trust state a capture time is in.
This module answers the next question: GIVEN an anchor and a capture
that happened offline, what time-interval can we honestly claim?

Core honesty rules (task brief, non-negotiable):
  - The output is an INTERVAL [earliest, latest] + uncertainty seconds.
    A point estimate alone is NEVER reported as "the capture time".
  - If no defensible interval can be built (no anchor, restart broke
    the monotonic chain, clock rolled back), the answer is UNKNOWN -
    never a fabricated precise time.
  - An extrapolated time is NEVER described as server-certified.

Model (all inputs server-recorded or device-reported + signed where
noted):

  anchor_receipt (SIGNED by server):
    server_received_at   server clock when the anchor request landed
    server_issued_at     server clock when the receipt was signed
    rtt_seconds          request->response round trip (measured by
                         the client, reported - upper bound used)
    anchor_monotonic     device monotonic clock AT the anchor moment
    ttl_seconds          how long the anchor remains trustworthy
  capture:
    capture_monotonic   device monotonic clock at shutter press
    wall_clock_at_capture (device-reported, UNTRUSTED)
  device constants (per-model, worst-case):
    drift_ppm            oscillator drift bound (typ. 50-250 ppm)
    resolution_seconds  monotonic clock resolution (typ. 1ms)

Interval arithmetic (Swift-portable, documented in the header of
every output):

  delta_t   = capture_monotonic - anchor_monotonic     (seconds)
  drift_slack = delta_t * drift_ppm * 1e-6
  half_rtt  = rtt_seconds / 2
  # where inside the RTT the anchor really happened is unknowable:
  anchor_true_earliest = server_received_at - half_rtt
  anchor_true_latest   = server_received_at + half_rtt
  earliest = anchor_true_earliest + delta_t - drift_slack - res
  latest   = anchor_true_latest   + delta_t + drift_slack + res
  uncertainty = (latest - earliest) / 2

  AFTER RESTART the monotonic delta is meaningless -> UNKNOWN.
  WALL ROLLBACK does not shrink the interval (wall is untrusted) but
  is reported as an anomaly signal alongside.
  EXPIRED ANCHOR (delta_t or age > ttl): the interval still computes
  but is reported with state EXPIRED and a widened confidence reason -
  it degrades, it never silently upgrades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


class TimeUncertaintyError(ValueError):
    pass


@dataclass
class AnchorReceipt:
    server_received_at: datetime
    rtt_seconds: float
    anchor_monotonic: float
    ttl_seconds: float = 14 * 24 * 3600.0        # 2 weeks default
    server_issued_at: datetime | None = None

    def validate(self) -> None:
        if self.rtt_seconds < 0:
            raise TimeUncertaintyError("rtt must be non-negative")
        if self.ttl_seconds <= 0:
            raise TimeUncertaintyError("ttl must be positive")
        if self.server_received_at.tzinfo is None:
            raise TimeUncertaintyError("server_received_at must be "
                                       "timezone-aware")


@dataclass
class CaptureObservation:
    capture_monotonic: float
    wall_clock_at_capture: datetime          # device-reported, UNTRUSTED

    def validate(self) -> None:
        if self.wall_clock_at_capture.tzinfo is None:
            raise TimeUncertaintyError("wall clock must be "
                                       "timezone-aware")


@dataclass
class DeviceClockModel:
    drift_ppm: float = 250.0                  # conservative upper bound
    monotonic_resolution_s: float = 0.001


@dataclass
class TimeIntervalResult:
    state: str                                # anchored/extrapolated/
                                              # expired/unknown
    estimated: datetime | None = None
    earliest_possible: datetime | None = None
    latest_possible: datetime | None = None
    uncertainty_seconds: float | None = None
    time_source: str = "server_anchor+monotonic_extrapolation"
    confidence_reason: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        def iso(x):
            return x.isoformat() if x else None
        return {
            "state": self.state,
            "estimated": iso(self.estimated),
            "earliest_possible": iso(self.earliest_possible),
            "latest_possible": iso(self.latest_possible),
            "uncertainty_seconds": self.uncertainty_seconds,
            "time_source": self.time_source,
            "confidence_reason": self.confidence_reason,
            "anomalies": self.anomalies,
        }


def compute_interval(anchor: AnchorReceipt,
                     capture: CaptureObservation,
                     clock: DeviceClockModel | None = None,
                     monotonic_reset_detected: bool = False,
                     now: datetime | None = None) -> TimeIntervalResult:
    clock = clock or DeviceClockModel()
    anchor.validate()
    capture.validate()

    r = TimeIntervalResult(state="unknown")

    # ---- hard unknown: the monotonic chain is broken ---------------
    if monotonic_reset_detected:
        r.anomalies.append("monotonic reset between anchor and "
                           "capture - offline elapsed time is "
                           "unrecoverable from clocks alone")
        r.confidence_reason.append(
            "restart breaks every honest interval -> UNKNOWN (a "
            "fabricated precise time would be worse than none)")
        return r

    delta_t = capture.capture_monotonic - anchor.anchor_monotonic
    if delta_t < 0:
        r.anomalies.append("capture monotonic PRECEDES anchor "
                           "monotonic without a reset flag - "
                           "inconsistent evidence")
        r.confidence_reason.append("negative elapsed monotonic time "
                                   "cannot produce a valid interval")
        return r

    # ---- interval arithmetic (Swift-portable formulas) -------------
    drift_slack = delta_t * clock.drift_ppm * 1e-6
    half_rtt = anchor.rtt_seconds / 2.0
    res = clock.monotonic_resolution_s

    base = anchor.server_received_at
    earliest = base + timedelta(seconds=(
        -half_rtt + delta_t - drift_slack - res))
    latest = base + timedelta(seconds=(
        +half_rtt + delta_t + drift_slack + res))
    # the honest point estimate is the interval CENTER - a bare
    # midpoint without the interval is never reported by itself
    estimated = base + timedelta(seconds=delta_t)
    uncertainty = (latest - earliest).total_seconds() / 2

    r.earliest_possible = earliest
    r.latest_possible = latest
    r.estimated = estimated
    r.uncertainty_seconds = uncertainty

    # ---- anchor age / expiry ---------------------------------------
    age = (now or datetime.now(timezone.utc)) - base
    expired = age.total_seconds() > anchor.ttl_seconds
    long_extrap = delta_t > anchor.ttl_seconds
    if expired or long_extrap:
        r.state = "expired"
        r.confidence_reason.append(
            f"anchor {'age' if expired else 'extrapolation span'} "
            f"exceeds ttl={anchor.ttl_seconds:.0f}s - interval "
            f"computed but DEGRADED; drift bound "
            f"{clock.drift_ppm}ppm may no longer hold over that span")
    else:
        r.state = "extrapolated"
        r.confidence_reason.append(
            f"monotonic elapsed {delta_t:.3f}s from a signed anchor; "
            f"bounds: half_rtt={half_rtt:.3f}s, "
            f"drift={drift_slack:.3f}s ({clock.drift_ppm}ppm), "
            f"resolution={res}s")

    # ---- wall-clock cross-check (anomaly signal, never input) ------
    wall = capture.wall_clock_at_capture
    if wall < earliest - timedelta(seconds=0):
        if wall < earliest:
            r.anomalies.append(
                f"device wall clock ({wall.isoformat()}) is BEFORE "
                f"the interval floor - recorded as a rollback/anomaly "
                f"signal; the interval itself is unaffected")
    elif wall > latest:
        r.anomalies.append(
            f"device wall clock ({wall.isoformat()}) is AFTER the "
            f"interval ceiling - suspicious jump forward; signal "
            f"only, the interval stands")

    r.confidence_reason.append(
        "EXTRAPOLATED time is a BOUNDED ESTIMATE, not a "
        "server-certified capture time (TM10 honesty rule)")
    return r


# -------------------------------------------------- Swift-portable spec


SWIFT_PORTABLE_SPEC = """\
// TM10 - Swift port of the interval arithmetic (Mac executes; this
// file documents the EXACT formulas the Windows engine uses - any
// cross-language difference is a bug in one of the two).

struct TimeUncertainty {
    // inputs (receipt is server-SIGNED)
    let serverReceivedAt: Date        // anchor landing time
    let rttSeconds: Double            // request->response RTT
    let anchorMonotonic: Double      // device monotonic @ anchor
    let captureMonotonic: Double     // device monotonic @ shutter
    let driftPPM: Double              // conservative per-model bound
    let resolutionSeconds: Double

    var deltaT: Double { captureMonotonic - anchorMonotonic }

    // where inside the RTT the true anchor moment sits is unknowable:
    var anchorTrueEarliest: Date {
        serverReceivedAt - rttSeconds / 2 }
    var anchorTrueLatest: Date {
        serverReceivedAt + rttSeconds / 2 }

    var driftSlack: Double { deltaT * driftPPM * 1e-6 }

    var earliest: Date {
        anchorTrueEarliest + deltaT - driftSlack - resolutionSeconds }
    var latest: Date {
        anchorTrueLatest + deltaT + driftSlack + resolutionSeconds }

    var uncertaintySeconds: Double { latest.timeIntervalSince(earliest) / 2 }

    // honesty rules to keep in the Swift port:
    // 1. NEVER report a point estimate without the interval
    // 2. monotonic reset between anchor and capture -> UNKNOWN
    // 3. wall-clock is a cross-check signal, never an interval input
    // 4. extrapolated is NOT server-certified time
}
"""
