"""TM09: anchor V3 cross-module consistency.

W024 owns the STATE MACHINE (extrapolated_expired, monotonic reset,
...). TM10 owns the INTERVAL arithmetic. This suite pins that the two
modules never contradict each other on the same physical evidence:

  v2 anchor (no monotonic mapping) <-> machine device_reported_only
                                        AND interval engine cannot run
  stale/expired extrapolation      <-> machine EXTRAPOLATED_STALE /
                                        EXTRAPOLATED_EXPIRED AND
                                        interval state expired
  monotonic reboot (boot_id flip)  <-> machine refuses the extrapolation
  fresh v3 anchor + short span     <-> machine EXTRAPOLATED_FRESH AND
                                        engine 'extrapolated' with a
                                        finite, honest interval
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.timegeo_trust import (  # noqa: E402
    AnchorEvidence, CaptureTimeEvidence, assess_time_state)
from techlab.time_uncertainty import (  # noqa: E402
    AnchorReceipt, CaptureObservation, compute_interval)

T0 = datetime(2026, 11, 25, 1, 0, 0, tzinfo=timezone.utc)


def v3_anchor(mono_ms=0, valid=True, received=T0):
    return AnchorEvidence(anchor_id="anc-v3",
                          server_received_at=received,
                          signature_valid=valid,
                          monotonic_ms_at_anchor=mono_ms,
                          boot_id="boot-1")


def v2_anchor(valid=True, received=T0):
    return AnchorEvidence(anchor_id="anc-v2",
                          server_received_at=received,
                          signature_valid=valid,
                          monotonic_ms_at_anchor=None,
                          boot_id=None)


def cap(seq, wall, mono_ms, boot="boot-1", rid="r1"):
    return CaptureTimeEvidence(record_id=rid, sequence=seq,
                               device_wall_time=wall,
                               monotonic_ms=mono_ms, boot_id=boot)


def test_v2_anchor_never_extrapolates_in_either_module():
    """Production v2 anchors carry NO monotonic mapping: the machine
    must land on device_reported_only (untrusted wall clock) and the
    interval engine has nothing to compute from."""
    c = cap(1, T0 + timedelta(minutes=30), mono_ms=None, boot=None)
    m = assess_time_state(v2_anchor(), c)
    assert m.state.value == "device_reported_only"
    # interval engine REQUIRES a monotonic pair - reconstructing one
    # from None is impossible, callers must not even try:
    with pytest.raises((TypeError, AttributeError)):
        compute_interval(
            AnchorReceipt(server_received_at=T0, rtt_seconds=0.3,
                          anchor_monotonic=None,      # v2 reality
                          ttl_seconds=3600.0),
            CaptureObservation(capture_monotonic=1800.0,
                               wall_clock_at_capture=c.device_wall_time))


def test_fresh_v3_extrapolation_agrees():
    """300s offline span, both modules claim extrapolation - the
    machine labels the STATE, the engine bounds the VALUE. NOTE the
    windows differ BY DESIGN: the machine uses the product default
    (FRESH_WINDOW_S=24h), the engine uses the anchor's own ttl."""
    c = cap(1, T0 + timedelta(seconds=300), mono_ms=300_000)
    m = assess_time_state(v3_anchor(mono_ms=0), c)
    assert m.state.value in ("extrapolated_fresh",
                             "server_anchored")
    r = compute_interval(
        AnchorReceipt(server_received_at=T0, rtt_seconds=0.3,
                      anchor_monotonic=0.0, ttl_seconds=3600.0),
        CaptureObservation(capture_monotonic=300.0,
                           wall_clock_at_capture=c.device_wall_time),
        now=T0 + timedelta(seconds=400))       # age checked vs shutter
    assert r.state == "extrapolated"
    est = (r.estimated - T0).total_seconds()
    assert 295 < est < 305
    assert r.uncertainty_seconds > 0


def test_stale_extrapolation_agrees():
    """48h offline span: machine (STALE after 24h) and engine (expired
    past ttl) both degrade openly and still produce their outputs."""
    c = cap(1, T0 + timedelta(hours=48), mono_ms=48 * 3600 * 1000)
    m = assess_time_state(v3_anchor(mono_ms=0), c)
    assert m.state.value in ("extrapolated_stale",
                             "extrapolated_expired")
    r = compute_interval(
        AnchorReceipt(server_received_at=T0, rtt_seconds=0.3,
                      anchor_monotonic=0.0, ttl_seconds=3600.0),
        CaptureObservation(capture_monotonic=48 * 3600.0,
                           wall_clock_at_capture=c.device_wall_time),
        now=T0 + timedelta(hours=48))
    assert r.state == "expired"
    assert r.estimated is not None      # degraded, not silent


def test_reboot_kills_extrapolation_in_both():
    """boot_id flip between anchor and capture: the machine refuses
    the monotonic mapping; the engine's reset path says UNKNOWN."""
    c = cap(1, T0 + timedelta(seconds=600), mono_ms=1000.0,
            boot="boot-2")           # rebooted; monotonic restarted
    m = assess_time_state(v3_anchor(mono_ms=0), c)
    assert m.state.value in ("unverifiable_anomaly",
                             "device_reported_only")
    r = compute_interval(
        AnchorReceipt(server_received_at=T0, rtt_seconds=0.3,
                      anchor_monotonic=600_000.0, ttl_seconds=3600.0),
        CaptureObservation(capture_monotonic=1.0,
                           wall_clock_at_capture=c.device_wall_time),
        monotonic_reset_detected=True)
    assert r.state == "unknown" and r.estimated is None


def test_honesty_never_claims_certified_capture_time():
    c = cap(1, T0 + timedelta(seconds=60), mono_ms=60_000)
    m = assess_time_state(v3_anchor(mono_ms=0), c)
    # NEITHER module may put a "certified capture time" in the CLAIM
    # column; anything certified belongs in cannot_claim territory
    joined_claims = " ".join(m.can_claim()).lower()
    assert "certified" not in joined_claims
    r = compute_interval(
        AnchorReceipt(server_received_at=T0, rtt_seconds=0.3,
                      anchor_monotonic=0.0, ttl_seconds=3600.0),
        CaptureObservation(capture_monotonic=60.0,
                           wall_clock_at_capture=c.device_wall_time))
    assert any("server-certified" in c2.lower()
               for c2 in r.confidence_reason)
