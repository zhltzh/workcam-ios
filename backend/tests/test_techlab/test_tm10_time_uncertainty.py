"""TM10: time-uncertainty interval engine tests."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.time_uncertainty import (  # noqa: E402
    AnchorReceipt, CaptureObservation, DeviceClockModel,
    TimeIntervalResult, TimeUncertaintyError, compute_interval,
    SWIFT_PORTABLE_SPEC)

T0 = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)


def anchor(rtt=0.4, mono=100.0, ttl=14 * 24 * 3600, at=T0):
    return AnchorReceipt(server_received_at=at, rtt_seconds=rtt,
                         anchor_monotonic=mono, ttl_seconds=ttl)


def capture(mono, wall_offset_s=300.0, wall_base=T0):
    wall = wall_base + timedelta(seconds=wall_offset_s)
    return CaptureObservation(capture_monotonic=mono,
                              wall_clock_at_capture=wall)


def test_basic_interval_contains_true_capture_time():
    """Capture 5 minutes after the anchor: the interval must be
    centered near T0+300s with width driven by rtt+drift+res."""
    r = compute_interval(anchor(rtt=0.5), capture(100 + 300),
                         DeviceClockModel(drift_ppm=100, monotonic_resolution_s=0.001))
    assert r.state == "extrapolated"
    est_off = (r.estimated - T0).total_seconds()
    assert 299 <= est_off <= 301, est_off
    lo = (r.earliest_possible - T0).total_seconds()
    hi = (r.latest_possible - T0).total_seconds()
    assert lo < 300 < hi
    assert r.uncertainty_seconds == pytest.approx(
        (hi - lo) / 2, abs=1e-6)


def test_interval_width_matches_formula():
    clock = DeviceClockModel(drift_ppm=250, monotonic_resolution_s=0.01)
    rtt = 0.8
    a = anchor(rtt=rtt, mono=10.0)
    c = capture(10.0 + 1000.0)
    r = compute_interval(a, c, clock)
    expected_half = rtt / 2 + 1000 * 250e-6 + 0.01
    assert r.uncertainty_seconds == pytest.approx(expected_half,
                                                   rel=1e-3)


def test_zero_rtt_tight_interval():
    clock = DeviceClockModel(drift_ppm=0, monotonic_resolution_s=0)
    r = compute_interval(anchor(rtt=0.0, mono=0.0),
                         capture(60.0), clock)
    lo = (r.earliest_possible - T0).total_seconds()
    hi = (r.latest_possible - T0).total_seconds()
    assert lo == 60.0 and hi == 60.0
    assert r.uncertainty_seconds == 0.0


def test_restart_breaks_chain_to_unknown():
    r = compute_interval(anchor(), capture(50.0),     # monotonic went
                         monotonic_reset_detected=True)   # BACKWARD
    assert r.state == "unknown"
    assert r.estimated is None and r.earliest_possible is None
    assert any("reset" in a.lower() for a in r.anomalies)
    assert any("fabricated" in c for c in r.confidence_reason)


def test_negative_monotonic_without_flag_is_unknown():
    r = compute_interval(anchor(mono=100.0), capture(50.0))
    assert r.state == "unknown"
    assert any("PRECEDES" in a for a in r.anomalies)


def test_expired_anchor_degrades_not_upgrades():
    """An anchor older than ttl still yields an interval, but the
    state must SAY so - silent trust is forbidden."""
    from datetime import datetime as dt
    old = dt(2026, 10, 1, tzinfo=timezone.utc)
    a = anchor(at=old, ttl=24 * 3600)
    r = compute_interval(a, capture(100.0, wall_base=old), now=T0)
    assert r.state == "expired"
    assert any("DEGRADED" in c for c in r.confidence_reason)
    # long extrapolation span relative to ttl also expires
    r2 = compute_interval(anchor(ttl=3600), capture(100.0 + 100 * 3600))
    assert r2.state == "expired"
    assert any("span" in c for c in r2.confidence_reason)


def test_wall_rollback_is_signal_not_input():
    """Device wall clock BEFORE the interval floor: reported as an
    anomaly, but the interval itself must not move."""
    a = anchor(mono=0.0)
    c_ok = capture(300.0, wall_offset_s=300.0)         # inside interval
    c_rollback = capture(300.0, wall_offset_s=-3600.0)   # 1h behind
    r_ok = compute_interval(a, c_ok)
    r_bad = compute_interval(a, c_rollback)
    assert not r_ok.anomalies, r_ok.anomalies
    assert r_ok.earliest_possible == r_bad.earliest_possible
    assert r_ok.latest_possible == r_bad.latest_possible
    assert any("rollback/anomaly" in x for x in r_bad.anomalies)


def test_wall_forward_jump_is_signal():
    c_jump = capture(300.0, wall_offset_s=999999.0)
    r = compute_interval(anchor(mono=0.0), c_jump)
    assert any("ceiling" in a for a in r.anomalies)
    assert r.state == "extrapolated"       # interval stands


def test_naive_datetimes_rejected():
    from datetime import datetime as dt
    with pytest.raises(TimeUncertaintyError):
        AnchorReceipt(server_received_at=dt(2026, 11, 25),
                      rtt_seconds=0.1, anchor_monotonic=0,
                      ttl_seconds=100).validate()
    with pytest.raises(TimeUncertaintyError):
        CaptureObservation(capture_monotonic=0,
                           wall_clock_at_capture=dt(2026, 11, 25)).validate()


def test_honesty_wording_in_every_confident_result():
    r = compute_interval(anchor(), capture(120.0))
    assert any("server-certified capture time" in c
               for c in r.confidence_reason)
    assert r.time_source == "server_anchor+monotonic_extrapolation"


def test_swift_spec_documented_and_consistent():
    """The Swift-portable spec must carry the exact formula pieces and
    the honesty rules - drift in one port is a bug in one of them."""
    for needle in ("driftPPM", "rttSeconds / 2", "uncertaintySeconds",
                   "NEVER report a point estimate without the interval",
                   "monotonic reset", "NOT server-certified"):
        assert needle in SWIFT_PORTABLE_SPEC
