"""B02-W024 - offline time & geo trust state machine tests.

All scenarios use simulated clock/location injection; no real device data.
Real-iPhone validation is DEVICE_PENDING (docs/mac-experiments/).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from techlab.location_meta import LocationMetadata, LocationQuality, LocationSource
from techlab.timegeo_trust import (
    AnchorEvidence,
    CaptureTimeEvidence,
    LocationAssessment,
    LocationTrustState,
    TimeAssessment,
    TimeCategory,
    TimeGeoTrustReport,
    TimeTrustEvent,
    TimeTrustState,
    assert_wording_safety,
    assess_location_state,
    assess_offline_session,
    assess_time_state,
    apply_late_anchor,
    extrapolate,
    transition,
    sequence_conflict,
)

UTC = datetime  # all datetimes below are aware, kept in named helpers


def iso(t: datetime) -> str:
    return t.isoformat()


# Fixed reference instants (UTC-aware) ---------------------------------------

def aware(y=2026, mo=11, d=20, h=0, mi=0, s=0, off_hours=0):
    from datetime import timezone as tz
    return datetime(y, mo, d, h, mi, s, tzinfo=tz(timedelta(hours=off_hours)))


def v3_anchor(mono_ms: int, boot: str = "boot-1",
              received=None, valid: bool = True) -> AnchorEvidence:
    return AnchorEvidence(
        anchor_id="anc-1",
        server_received_at=received or aware(h=1),
        signature_valid=valid,
        monotonic_ms_at_anchor=mono_ms,
        boot_id=boot,
    )


def v2_anchor(valid: bool = True) -> AnchorEvidence:
    """Production v2 anchor: signature + server time only, NO monotonic map."""
    return AnchorEvidence(
        anchor_id="anc-v2",
        server_received_at=aware(h=1),
        signature_valid=valid,
        monotonic_ms_at_anchor=None,
        boot_id=None,
    )


def cap(seq: int, wall: datetime, mono_ms: int, boot: str = "boot-1",
        offset: str = "+00:00", covered: str | None = None,
        rid: str | None = None) -> CaptureTimeEvidence:
    return CaptureTimeEvidence(
        record_id=rid or f"rec-{seq}",
        sequence=seq,
        device_wall_time=wall,
        utc_offset=offset,
        monotonic_ms=mono_ms,
        boot_id=boot,
        covered_by_anchor=covered,
    )


def loc(source=LocationSource.GPS, acc=None, lat=31.2304, lon=121.4737,
        fix_t: datetime | None = None, simulated: bool = False) -> LocationMetadata:
    return LocationMetadata(
        source=source, latitude=lat, longitude=lon,
        horizontal_accuracy=acc,
        fix_timestamp=iso(fix_t) if fix_t else None,
        is_simulated=simulated,
    )


# ---------------------------------------------------------------------------
# A. Four time categories + anchor coverage
# ---------------------------------------------------------------------------


def test_four_time_categories_are_distinct():
    cats = {c.value for c in TimeCategory}
    assert cats == {"server_anchored", "device_reported", "extrapolated", "unverifiable"}


def test_record_covered_by_signed_anchor_gets_existence_bound():
    anchor = v3_anchor(mono_ms=0)
    c = cap(1, aware(h=0, mi=30), mono_ms=1_800_000, covered="anc-1")
    a = assess_time_state(anchor, c)
    assert a.state == TimeTrustState.SERVER_ANCHORED
    assert a.category == TimeCategory.SERVER_ANCHORED
    assert a.existence_bound == anchor.server_received_at
    assert "not the capture time" in a.time_label()
    assert "record existed before" in " ".join(a.can_claim())
    assert "server_received_at is when the photo was taken" in " ".join(a.cannot_claim())


def test_v2_anchor_cannot_extrapolate():
    """Production v2 anchors carry no monotonic mapping: extrapolation is
    impossible. This is the documented motivation for the v3 proposal."""
    anchor = v2_anchor()
    c = cap(1, aware(h=2), mono_ms=3_600_000)
    a = assess_time_state(anchor, c)
    assert a.state == TimeTrustState.DEVICE_REPORTED_ONLY
    assert a.category == TimeCategory.DEVICE_REPORTED
    assert a.extrapolated is None


def test_invalid_signature_anchor_is_ignored():
    anchor = v3_anchor(mono_ms=0, valid=False)
    c = cap(1, aware(h=2), mono_ms=3_600_000, covered="anc-1")
    a = assess_time_state(anchor, c)
    assert a.state == TimeTrustState.DEVICE_REPORTED_ONLY


def test_no_time_fields_is_no_evidence():
    c = CaptureTimeEvidence(
        record_id="r", sequence=0, device_wall_time=None,
        utc_offset="+00:00", monotonic_ms=None, boot_id=None)
    a = assess_time_state(None, c)
    assert a.state == TimeTrustState.NO_EVIDENCE
    assert a.category == TimeCategory.UNVERIFIABLE


def test_anchor_conflict_inside_covered_batch():
    anchor = v3_anchor(mono_ms=0)
    # device claims capture AFTER the server received the batch digest
    c = cap(1, aware(h=3), mono_ms=1_800_000, covered="anc-1")
    a = assess_time_state(anchor, c)
    assert a.state == TimeTrustState.SERVER_ANCHORED
    kinds = [x.kind for x in a.anomalies]
    assert "anchor_conflict" in kinds
    # digest coverage itself remains provable
    assert a.existence_bound == anchor.server_received_at


# ---------------------------------------------------------------------------
# B. Extrapolation windows (fresh / stale / expired)
# ---------------------------------------------------------------------------


def test_extrapolation_fresh_inside_24h():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    c = cap(1, aware(h=1, mi=10), mono_ms=600_000)
    est = extrapolate(anchor, c)
    assert est is not None
    assert abs((est.estimate - aware(h=1, mi=10)).total_seconds()) < 0.001
    assert est.error_bound_s == pytest.approx(5.0 + (10 / 60) * 0.18, abs=0.01)
    a = assess_time_state(anchor, c)
    assert a.state == TimeTrustState.EXTRAPOLATED_FRESH
    assert a.category == TimeCategory.EXTRAPOLATED
    assert est.window() == "fresh"


def test_extrapolation_stale_between_24h_and_72h():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    thirty_h_ms = int(30 * 3600 * 1000)
    c = cap(1, aware(d=21, h=7), mono_ms=thirty_h_ms)
    a = assess_time_state(anchor, c)
    assert a.state == TimeTrustState.EXTRAPOLATED_STALE
    assert a.category == TimeCategory.EXTRAPOLATED
    assert a.extrapolated is not None
    assert a.extrapolated.window() == "stale"
    assert "low confidence" in a.reason


def test_extrapolation_expired_beyond_72h():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    eighty_h_ms = int(80 * 3600 * 1000)
    c = cap(1, aware(d=23, h=9), mono_ms=eighty_h_ms)
    a = assess_time_state(anchor, c)
    assert a.state == TimeTrustState.EXTRAPOLATED_EXPIRED
    assert a.extrapolated is None
    assert a.category == TimeCategory.DEVICE_REPORTED
    # no existence bound: this record did not exist when the anchor was made
    assert a.existence_bound is None


def test_extrapolation_expired_by_error_bound_before_window():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    sixty_h_ms = int(60 * 3600 * 1000)  # 60h: inside 72h window ...
    c = cap(1, aware(d=22, h=13), mono_ms=sixty_h_ms)
    # ... but error bound = 5 + 60 * 0.18 = 15.8s > 15s ceiling
    est = extrapolate(anchor, c)
    assert est is not None and est.error_bound_s > 15.0
    a = assess_time_state(anchor, c)
    assert a.state == TimeTrustState.EXTRAPOLATED_EXPIRED
    assert "error bound exceeded" in a.reason


def test_capture_before_anchor_is_not_extrapolation():
    anchor = v3_anchor(mono_ms=10_000_000, received=aware(h=1))
    c = cap(1, aware(h=0, mi=30), mono_ms=600_000)  # mono precedes anchor
    assert extrapolate(anchor, c) is None


# ---------------------------------------------------------------------------
# C. Wall clock rollback / jumps (system time edited)
# ---------------------------------------------------------------------------


def test_wall_rollback_with_anchor_keeps_monotonic_estimate():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    prev = cap(0, aware(h=1), mono_ms=0)
    # wall clock pulled back 20s while monotonic advanced normally
    c = cap(1, aware(h=1) - timedelta(seconds=20), mono_ms=10_000)
    a = assess_time_state(anchor, c, prev=prev, prev_mono=0)
    assert a.state == TimeTrustState.EXTRAPOLATED_FRESH
    kinds = [x.kind for x in a.anomalies]
    assert "wall_rollback" in kinds
    assert "device_time_disqualified" in kinds
    assert a.extrapolated is not None  # monotonic chain keeps the estimate


def test_wall_rollback_without_anchor_is_unverifiable():
    prev = cap(0, aware(h=1), mono_ms=0)
    c = cap(1, aware(h=1) - timedelta(seconds=30), mono_ms=30_000)
    a = assess_time_state(None, c, prev=prev, prev_mono=0)
    assert a.state == TimeTrustState.UNVERIFIABLE_ANOMALY
    assert a.category == TimeCategory.UNVERIFIABLE
    assert "tamper indicators" in a.reason or "only witness" in a.reason


def test_small_backstep_within_tolerance_is_not_anomaly():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    prev = cap(0, aware(h=1), mono_ms=0)
    c = cap(1, aware(h=1) - timedelta(seconds=1), mono_ms=10_000)
    a = assess_time_state(anchor, c, prev=prev, prev_mono=0)
    kinds = [x.kind for x in a.anomalies]
    assert "wall_rollback" not in kinds
    assert a.state == TimeTrustState.EXTRAPOLATED_FRESH


def test_forward_jump_flagged_but_estimate_kept_with_anchor():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    prev = cap(0, aware(h=1), mono_ms=0)
    c = cap(1, aware(h=9), mono_ms=600_000)  # +8h wall, only 10min monotonic
    a = assess_time_state(anchor, c, prev=prev, prev_mono=0)
    kinds = [x.kind for x in a.anomalies]
    assert "forward_jump" in kinds
    assert a.state == TimeTrustState.EXTRAPOLATED_FRESH


def test_forward_jump_without_anchor_is_unverifiable():
    prev = cap(0, aware(h=1), mono_ms=0)
    c = cap(1, aware(h=9), mono_ms=600_000)
    a = assess_time_state(None, c, prev=prev, prev_mono=0)
    assert a.state == TimeTrustState.UNVERIFIABLE_ANOMALY


# ---------------------------------------------------------------------------
# D. Reboot (monotonic clock reset)
# ---------------------------------------------------------------------------


def test_reboot_breaks_extrapolation_chain():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    prev = cap(0, aware(h=1), mono_ms=5_000_000)
    c = cap(1, aware(h=2), mono_ms=1_000, boot="boot-2")  # counter cleared
    a = assess_time_state(anchor, c, prev=prev, prev_mono=5_000_000)
    assert a.state == TimeTrustState.UNVERIFIABLE_ANOMALY
    kinds = [x.kind for x in a.anomalies]
    assert "monotonic_reset" in kinds
    assert a.extrapolated is None


def test_boot_mismatch_first_record_is_unverifiable():
    anchor = v3_anchor(mono_ms=100_000, boot="boot-1", received=aware(h=1))
    c = cap(1, aware(h=2), mono_ms=200_000, boot="boot-2")
    a = assess_time_state(anchor, c)
    assert a.state == TimeTrustState.UNVERIFIABLE_ANOMALY
    assert "monotonic" in a.reason


def test_post_reboot_reanchor_recovers_with_degraded_flag():
    # first anchor lost to reboot, second anchor on the new boot
    anchor2 = v3_anchor(mono_ms=5_000, boot="boot-2", received=aware(d=20, h=5))
    c = cap(1, aware(d=20, h=5, mi=10), mono_ms=65_000, boot="boot-2", covered="anc-1")
    a = assess_time_state(anchor2, c, degraded_session=True)
    assert a.state == TimeTrustState.DEGRADED_RECOVERED
    assert a.degraded_session is True
    assert a.existence_bound == anchor2.server_received_at
    # next capture on the new boot extrapolates normally, degraded flag kept
    c2 = cap(2, aware(d=20, h=5, mi=20), mono_ms=1_265_000, boot="boot-2")
    a2 = assess_time_state(anchor2, c2, degraded_session=True)
    assert a2.state == TimeTrustState.EXTRAPOLATED_FRESH
    assert a2.degraded_session is True


def test_session_walk_propagates_degraded_after_anomaly():
    anchor = None  # offline, no anchor at all
    records = [
        cap(0, aware(h=1), mono_ms=0),
        cap(1, aware(h=1) - timedelta(seconds=30), mono_ms=10_000),  # rollback
        cap(2, aware(h=1, mi=2), mono_ms=120_000),  # after the anomaly
    ]
    results = assess_offline_session(anchor, records)
    assert results[0].state == TimeTrustState.DEVICE_REPORTED_ONLY
    assert results[1].state == TimeTrustState.UNVERIFIABLE_ANOMALY
    assert results[2].state == TimeTrustState.DEVICE_REPORTED_ONLY
    assert results[2].degraded_session is True  # anomaly scars the session


# ---------------------------------------------------------------------------
# E. Timezone changes
# ---------------------------------------------------------------------------


def test_tz_shift_with_utc_continuity_is_info_only():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    # user flew west: local representation changed, UTC continuous
    # 09:00+08:00 == 01:00Z  ->  04:00:10+03:00 == 01:00:10Z
    prev = cap(0, aware(h=9, off_hours=8), mono_ms=0, offset="+08:00")
    c = cap(1, aware(h=4, s=10, off_hours=3), mono_ms=10_000, offset="+03:00")
    a = assess_time_state(anchor, c, prev=prev, prev_mono=0)
    kinds = [x.kind for x in a.anomalies]
    assert "timezone_shift" in kinds
    assert "manual_tz_change" not in kinds
    assert a.state == TimeTrustState.EXTRAPOLATED_FRESH


def test_manual_tz_change_utc_jump_downgrades_without_anchor():
    # offset edited +08:00 -> +09:00 while local clock text unchanged:
    # UTC value effectively jumps backward ~1h
    prev = cap(0, aware(h=9, off_hours=8), mono_ms=0, offset="+08:00")
    c = cap(1, aware(h=9, s=10, off_hours=9), mono_ms=10_000, offset="+09:00")
    a = assess_time_state(None, c, prev=prev, prev_mono=0)
    kinds = [x.kind for x in a.anomalies]
    assert "manual_tz_change" in kinds
    assert a.state == TimeTrustState.UNVERIFIABLE_ANOMALY


def test_manual_tz_change_with_anchor_keeps_estimate():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    prev = cap(0, aware(h=9, off_hours=8), mono_ms=0, offset="+08:00")
    c = cap(1, aware(h=9, s=10, off_hours=9), mono_ms=10_000, offset="+09:00")
    a = assess_time_state(anchor, c, prev=prev, prev_mono=0)
    assert a.state == TimeTrustState.EXTRAPOLATED_FRESH
    assert a.extrapolated is not None


# ---------------------------------------------------------------------------
# F. Sequence conflicts
# ---------------------------------------------------------------------------


def test_sequence_conflict_detected():
    records = [
        cap(0, aware(h=2), mono_ms=0),
        cap(1, aware(h=1), mono_ms=10_000),  # seq up, time down
    ]
    assert sequence_conflict(records) is True
    assert sequence_conflict([cap(0, aware(h=1), mono_ms=0),
                              cap(1, aware(h=2), mono_ms=10_000)]) is False


# ---------------------------------------------------------------------------
# G. Location states
# ---------------------------------------------------------------------------


def test_no_location():
    a = assess_location_state(None)
    assert a.state == LocationTrustState.NO_LOCATION
    assert not a.is_position_claim()
    a2 = assess_location_state(loc(source=LocationSource.NONE, lat=None, lon=None))
    assert a2.state == LocationTrustState.NO_LOCATION


def test_manual_input_is_input_not_gnss():
    m = loc(source=LocationSource.MANUAL, fix_t=aware(h=1))
    a = assess_location_state(m, capture_time=aware(h=1))
    assert a.state == LocationTrustState.MANUAL_INPUT
    assert not a.is_position_claim()
    assert "not a verified position" in a.location_label()
    # manual input keeps its state even when a simulation signal appears
    m2 = loc(source=LocationSource.MANUAL, simulated=True, fix_t=aware(h=1))
    a2 = assess_location_state(m2, capture_time=aware(h=1))
    assert a2.state == LocationTrustState.MANUAL_INPUT


@pytest.mark.parametrize("acc,expected", [
    (3.0, LocationTrustState.GNSS_GRADE_A),
    (10.0, LocationTrustState.GNSS_GRADE_B),
    (30.0, LocationTrustState.GNSS_GRADE_C),
    (80.0, LocationTrustState.GNSS_GRADE_D),
])
def test_gnss_accuracy_grades(acc, expected):
    t = aware(h=1)
    a = assess_location_state(loc(acc=acc, fix_t=t), capture_time=t)
    assert a.state == expected
    assert a.is_position_claim()
    # wording: GNSS is best-effort, never tamper-proof
    assert "not tamper-proof" in a.location_label()


def test_network_derived_capped_at_c():
    t = aware(h=1)
    a = assess_location_state(loc(source=LocationSource.NETWORK, acc=2.0, fix_t=t),
                              capture_time=t)
    assert a.state == LocationTrustState.NETWORK_DERIVED
    assert a.quality == LocationQuality.C


def test_stale_fix_predating_capture():
    a = assess_location_state(
        loc(acc=5.0, fix_t=aware(h=1)),
        capture_time=aware(h=1, mi=10))  # fix is 10 minutes older
    assert a.state == LocationTrustState.STALE_FIX
    assert not a.is_position_claim()
    assert "not at capture time" in a.location_label()


def test_fresh_fix_within_threshold():
    a = assess_location_state(
        loc(acc=5.0, fix_t=aware(h=1, mi=9)),
        capture_time=aware(h=1, mi=10))  # 60s old: fine
    assert a.state == LocationTrustState.GNSS_GRADE_A


def test_explicit_simulated_flag_is_suspect():
    t = aware(h=1)
    a = assess_location_state(loc(acc=5.0, fix_t=t, simulated=True), capture_time=t)
    assert a.state == LocationTrustState.SIMULATION_SUSPECT
    assert not a.is_position_claim()
    assert "heuristic" in a.reason
    assert "not a position claim" in a.location_label()


def test_perfect_fix_streak_suspects_simulation():
    t = aware(h=1)
    a = assess_location_state(loc(acc=0.8, fix_t=t), capture_time=t, perfect_fix_streak=3)
    assert a.state == LocationTrustState.SIMULATION_SUSPECT


def test_single_perfect_fix_is_not_suspect():
    t = aware(h=1)
    a = assess_location_state(loc(acc=0.8, fix_t=t), capture_time=t, perfect_fix_streak=1)
    assert a.state == LocationTrustState.GNSS_GRADE_A


def test_teleport_speed_suspects_simulation():
    prev = loc(acc=5.0, fix_t=aware(h=1), lat=31.2304, lon=121.4737)
    cur = loc(acc=5.0, fix_t=aware(h=1, s=10), lat=39.9042, lon=116.4074)  # ~1000km/10s
    a = assess_location_state(cur, capture_time=aware(h=1, s=10), prev=prev)
    assert a.state == LocationTrustState.SIMULATION_SUSPECT
    assert any("speed" in x.detail for x in a.anomalies)


def test_plausible_travel_speed_is_fine():
    prev = loc(acc=5.0, fix_t=aware(h=1), lat=31.2304, lon=121.4737)
    # ~600 m in 30 s = 20 m/s: plausible for a vehicle
    cur = loc(acc=5.0, fix_t=aware(h=1, mi=0, s=30), lat=31.2357, lon=121.4750)
    a = assess_location_state(cur, capture_time=aware(h=1, mi=0, s=30), prev=prev)
    assert a.state == LocationTrustState.GNSS_GRADE_A


# ---------------------------------------------------------------------------
# H. Wording red lines over representative states
# ---------------------------------------------------------------------------


def _report_for_every_time_state():
    """Build one representative TimeAssessment per TimeTrustState."""
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    out = {}
    covered = cap(1, aware(h=0, mi=30), mono_ms=1_800_000, covered="anc-1")
    out[TimeTrustState.SERVER_ANCHORED] = assess_time_state(anchor, covered)
    out[TimeTrustState.EXTRAPOLATED_FRESH] = assess_time_state(
        anchor, cap(2, aware(h=1, mi=10), mono_ms=600_000))
    out[TimeTrustState.EXTRAPOLATED_STALE] = assess_time_state(
        anchor, cap(3, aware(d=21, h=7), mono_ms=30 * 3600 * 1000))
    out[TimeTrustState.EXTRAPOLATED_EXPIRED] = assess_time_state(
        anchor, cap(4, aware(d=23, h=9), mono_ms=80 * 3600 * 1000))
    out[TimeTrustState.DEVICE_REPORTED_ONLY] = assess_time_state(
        v2_anchor(), cap(5, aware(h=2), mono_ms=3_600_000))
    out[TimeTrustState.UNVERIFIABLE_ANOMALY] = assess_time_state(
        None, cap(6, aware(h=1) - timedelta(seconds=30), mono_ms=30_000),
        prev=cap(5, aware(h=1), mono_ms=0), prev_mono=0)
    out[TimeTrustState.DEGRADED_RECOVERED] = assess_time_state(
        anchor, cap(7, aware(h=0, mi=30), mono_ms=1_800_000, covered="anc-1"),
        degraded_session=True)
    out[TimeTrustState.NO_EVIDENCE] = assess_time_state(
        None, CaptureTimeEvidence(record_id="r0", sequence=0, device_wall_time=None,
                                  utc_offset="+00:00"))
    return out


def _report_for_every_location_state():
    t = aware(h=1)
    lm = LocationAssessment(state=LocationTrustState.NO_LOCATION, quality=None)
    out = {
        LocationTrustState.NO_LOCATION: lm,
        LocationTrustState.GNSS_GRADE_A: assess_location_state(loc(acc=3.0, fix_t=t), t),
        LocationTrustState.GNSS_GRADE_B: assess_location_state(loc(acc=10.0, fix_t=t), t),
        LocationTrustState.GNSS_GRADE_C: assess_location_state(loc(acc=30.0, fix_t=t), t),
        LocationTrustState.GNSS_GRADE_D: assess_location_state(loc(acc=80.0, fix_t=t), t),
        LocationTrustState.NETWORK_DERIVED: assess_location_state(
            loc(source=LocationSource.NETWORK, acc=2.0, fix_t=t), t),
        LocationTrustState.MANUAL_INPUT: assess_location_state(
            loc(source=LocationSource.MANUAL, fix_t=t), t),
        LocationTrustState.STALE_FIX: assess_location_state(
            loc(acc=5.0, fix_t=aware(h=0)), t),
        LocationTrustState.SIMULATION_SUSPECT: assess_location_state(
            loc(acc=5.0, fix_t=t, simulated=True), t),
    }
    return out


def test_wording_safety_holds_for_all_state_pairs():
    times = _report_for_every_time_state()
    geos = _report_for_every_location_state()
    checked = 0
    for ts, ta in times.items():
        for ls, la in geos.items():
            r = TimeGeoTrustReport(record_id="r", time=ta, location=la)
            assert r.time.state == ts
            assert r.location.state == ls
            assert_wording_safety(r)
            checked += 1
    assert checked == len(TimeTrustState) * len(LocationTrustState)


def test_extrapolated_label_must_say_estimate():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    a = assess_time_state(anchor, cap(1, aware(h=1, mi=5), mono_ms=300_000))
    assert a.category == TimeCategory.EXTRAPOLATED
    label = a.time_label()
    assert "estimate" in label
    assert "server" not in label  # never "server-verified capture time"


# ---------------------------------------------------------------------------
# I. Transition table completeness
# ---------------------------------------------------------------------------


def test_transition_table_covers_reference_path():
    s = TimeTrustState.NO_EVIDENCE
    s = transition(s, TimeTrustEvent.ANCHOR_VERIFIED)
    assert s == TimeTrustState.SERVER_ANCHORED
    s = transition(s, TimeTrustEvent.CAPTURE)
    assert s == TimeTrustState.EXTRAPOLATED_FRESH
    s = transition(s, TimeTrustEvent.WINDOW_ELAPSED)
    assert s == TimeTrustState.EXTRAPOLATED_STALE
    s = transition(s, TimeTrustEvent.WINDOW_ELAPSED)
    assert s == TimeTrustState.EXTRAPOLATED_EXPIRED
    s = transition(s, TimeTrustEvent.RE_ANCHOR)
    assert s == TimeTrustState.SERVER_ANCHORED
    s = transition(s, TimeTrustEvent.MONOTONIC_RESET)
    assert s == TimeTrustState.UNVERIFIABLE_ANOMALY
    s = transition(s, TimeTrustEvent.RE_ANCHOR)
    assert s == TimeTrustState.DEGRADED_RECOVERED
    s = transition(s, TimeTrustEvent.CAPTURE)
    assert s == TimeTrustState.EXTRAPOLATED_FRESH


def test_unknown_transition_raises():
    with pytest.raises(ValueError):
        transition(TimeTrustState.NO_EVIDENCE, TimeTrustEvent.WINDOW_ELAPSED)


def test_evaluator_agrees_with_transition_table():
    """The reference evaluator must reproduce the explicit table semantics
    for the canonical offline walk."""
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    records = [
        cap(0, aware(h=0, mi=30), mono_ms=-1_800_000, covered="anc-1"),  # anchored
        cap(1, aware(h=1, mi=5), mono_ms=300_000),                       # fresh
        cap(2, aware(d=21, h=7), mono_ms=30 * 3600 * 1000),              # stale
        cap(3, aware(d=23, h=9), mono_ms=80 * 3600 * 1000),              # expired
    ]
    # monotonic before anchor: keep the anchor baseline at 0
    records[0] = cap(0, aware(h=0, mi=30), mono_ms=0, covered="anc-1")
    results = assess_offline_session(anchor, records)
    assert results[0].state == TimeTrustState.SERVER_ANCHORED
    assert results[1].state == TimeTrustState.EXTRAPOLATED_FRESH
    assert results[2].state == TimeTrustState.EXTRAPOLATED_STALE
    assert results[3].state == TimeTrustState.EXTRAPOLATED_EXPIRED


def test_wall_rollback_transitions_match_table():
    assert transition(TimeTrustState.DEVICE_REPORTED_ONLY, TimeTrustEvent.WALL_ROLLBACK) \
        == TimeTrustState.UNVERIFIABLE_ANOMALY
    assert transition(TimeTrustState.EXTRAPOLATED_FRESH, TimeTrustEvent.WALL_ROLLBACK) \
        == TimeTrustState.EXTRAPOLATED_FRESH  # self-loop: monotonic keeps estimate
    assert transition(TimeTrustState.DEVICE_REPORTED_ONLY, TimeTrustEvent.FORWARD_JUMP) \
        == TimeTrustState.UNVERIFIABLE_ANOMALY
    assert transition(TimeTrustState.SERVER_ANCHORED, TimeTrustEvent.MONOTONIC_RESET) \
        == TimeTrustState.UNVERIFIABLE_ANOMALY
    assert transition(TimeTrustState.EXTRAPOLATED_STALE, TimeTrustEvent.MONOTONIC_RESET) \
        == TimeTrustState.UNVERIFIABLE_ANOMALY
    assert transition(TimeTrustState.UNVERIFIABLE_ANOMALY, TimeTrustEvent.RE_ANCHOR) \
        == TimeTrustState.DEGRADED_RECOVERED
    assert transition(TimeTrustState.UNVERIFIABLE_ANOMALY, TimeTrustEvent.ANCHOR_VERIFIED) \
        == TimeTrustState.SERVER_ANCHORED


def test_location_transitions_table():
    from techlab.timegeo_trust import LOCATION_TRANSITIONS, LocationEvent
    assert LOCATION_TRANSITIONS[
        (LocationTrustState.NO_LOCATION, LocationEvent.MANUAL_ENTRY)
    ] == LocationTrustState.MANUAL_INPUT
    assert LOCATION_TRANSITIONS[
        (LocationTrustState.GNSS_GRADE_A, LocationEvent.SIMULATION_SIGNAL)
    ] == LocationTrustState.SIMULATION_SUSPECT
    assert LOCATION_TRANSITIONS[
        (LocationTrustState.GNSS_GRADE_B, LocationEvent.FIX_AGED)
    ] == LocationTrustState.STALE_FIX
    assert LOCATION_TRANSITIONS[
        (LocationTrustState.SIMULATION_SUSPECT, LocationEvent.GNSS_FIX)
    ] == LocationTrustState.SIMULATION_SUSPECT  # latched until reviewed


# ---------------------------------------------------------------------------
# J. Retroactive anchoring after long offline periods
# ---------------------------------------------------------------------------


def _retro_setup():
    anchor_new = AnchorEvidence(
        anchor_id="anc-late", server_received_at=aware(d=25, h=0),
        signature_valid=True, monotonic_ms_at_anchor=None, boot_id=None)
    caps = {
        "r1": cap(1, aware(h=1), mono_ms=600_000, rid="r1"),
        "r2": cap(2, aware(d=21), mono_ms=24 * 3600 * 1000, rid="r2"),
    }
    prior = {
        "r1": assess_time_state(v3_anchor(0), caps["r1"]),           # EXTRAPOLATED_FRESH
        "r2": assess_time_state(v3_anchor(0), caps["r2"]),           # EXTRAPOLATED_STALE
    }
    return prior, caps, anchor_new


def test_late_anchor_appends_bound_but_never_upgrades_state():
    prior, caps, anchor = _retro_setup()
    updates = apply_late_anchor(prior, caps, anchor)
    by_id = {u.record_id: u for u in updates}
    for rid in ("r1", "r2"):
        u = by_id[rid]
        assert u.existence_bound == anchor.server_received_at
        assert u.state_changed is True
        assert "NOT upgraded" in u.note
        # the assessed state object is untouched by the retroactive pass
        assert prior[rid].state in (TimeTrustState.EXTRAPOLATED_FRESH,
                                   TimeTrustState.EXTRAPOLATED_STALE)
        assert prior[rid].category == TimeCategory.EXTRAPOLATED
        assert prior[rid].existence_bound is None


def test_late_anchor_conflict_record_gets_no_bound():
    anchor = AnchorEvidence(anchor_id="anc-late",
                            server_received_at=aware(d=20, h=12),
                            signature_valid=True)
    caps = {"late-claim": cap(1, aware(d=21, h=0), mono_ms=0, rid="late-claim")}
    prior = {"late-claim": TimeAssessment(
        state=TimeTrustState.DEVICE_REPORTED_ONLY,
        category=TimeCategory.DEVICE_REPORTED)}
    updates = apply_late_anchor(prior, caps, anchor)
    u = updates[0]
    assert u.existence_bound is None
    assert "anchor conflict" in u.note
    assert u.state_changed is False


def test_late_anchor_invalid_signature_changes_nothing():
    prior, caps, _ = _retro_setup()
    bad = AnchorEvidence(anchor_id="anc-late", server_received_at=aware(d=25),
                         signature_valid=False)
    updates = apply_late_anchor(prior, caps, bad)
    for u in updates:
        assert u.existence_bound is None
        assert u.state_changed is False
        assert "signature invalid" in u.note


def test_late_anchor_existing_bound_left_unchanged():
    anchor = AnchorEvidence(anchor_id="anc-late",
                            server_received_at=aware(d=25, h=0),
                            signature_valid=True)
    caps = {"r1": cap(1, aware(h=1), mono_ms=0, rid="r1")}
    prior = {"r1": TimeAssessment(state=TimeTrustState.SERVER_ANCHORED,
                                  category=TimeCategory.SERVER_ANCHORED,
                                  existence_bound=aware(h=1))}
    updates = apply_late_anchor(prior, caps, anchor)
    assert updates[0].state_changed is False
    assert updates[0].existence_bound is None
    assert "already present" in updates[0].note


# ---------------------------------------------------------------------------
# K. Combined report
# ---------------------------------------------------------------------------


def test_combined_report_label_mentions_both_sides():
    anchor = v3_anchor(mono_ms=0, received=aware(h=1))
    t = aware(h=1, mi=5)
    ta = assess_time_state(anchor, cap(1, t, mono_ms=300_000))
    la = assess_location_state(loc(acc=3.0, fix_t=t), capture_time=t)
    r = TimeGeoTrustReport(record_id="rec-1", time=ta, location=la)
    label = r.combined_label()
    assert "rec-1" in label
    assert "estimate" in label
    assert "grade A" in label
    assert_wording_safety(r)
