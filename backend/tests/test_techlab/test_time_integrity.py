"""T03 - time integrity / clock anomaly detection tests."""

from __future__ import annotations

from techlab.time_integrity import (
    AnomalyKind,
    CaptureTick,
    Severity,
    analyze_session,
)


def _ticks(seq_times: list[tuple[int, str, int]], offset: str = "+08:00") -> list[CaptureTick]:
    return [
        CaptureTick(sequence=seq, device_time=t, utc_offset=offset, monotonic_ms=mono)
        for seq, t, mono in seq_times
    ]


def test_clean_session_is_consistent_but_low_confidence_without_anchor():
    ticks = _ticks([
        (0, "2026-11-19T09:00:00+08:00", 0),
        (1, "2026-11-19T09:00:10+08:00", 10_000),
        (2, "2026-11-19T09:00:20+08:00", 20_000),
    ])
    report = analyze_session(ticks)
    assert report.verdict == "internally_consistent"
    assert not report.anomalies
    # honesty: no external reference -> low confidence even when clean
    assert report.confidence == "low"


def test_wall_clock_rollback_detected_as_critical():
    ticks = _ticks([
        (0, "2026-11-19T09:00:00+08:00", 0),
        (1, "2026-11-19T09:00:10+08:00", 10_000),
        (2, "2026-11-19T08:59:50+08:00", 20_000),   # clock pulled back 20 s
    ])
    report = analyze_session(ticks)
    assert report.has_critical
    kinds = {a.kind for a in report.anomalies}
    assert AnomalyKind.CLOCK_ROLLBACK in kinds
    assert report.verdict == "tamper_indicators"


def test_monotonic_regression_detected():
    ticks = _ticks([
        (0, "2026-11-19T09:00:00+08:00", 50_000),
        (1, "2026-11-19T09:00:10+08:00", 10_000),   # monotonic went backwards
    ])
    report = analyze_session(ticks)
    kinds = {a.kind for a in report.anomalies}
    assert AnomalyKind.CLOCK_ROLLBACK in kinds


def test_large_forward_jump_flagged():
    ticks = _ticks([
        (0, "2026-11-19T09:00:00+08:00", 0),
        (1, "2026-11-19T09:00:10+08:00", 10_000),
        (2, "2026-11-19T18:00:00+08:00", 601_000),  # +9 h in 10 min
    ])
    report = analyze_session(ticks)
    kinds = {a.kind for a in report.anomalies}
    assert AnomalyKind.TIME_JUMP in kinds
    assert all(a.severity == Severity.WARNING for a in report.anomalies
               if a.kind == AnomalyKind.TIME_JUMP)


def test_timezone_change_is_info_not_tamper():
    ticks = [
        CaptureTick(sequence=0, device_time="2026-11-19T09:00:00+08:00",
                    utc_offset="+08:00", monotonic_ms=0),
        CaptureTick(sequence=1, device_time="2026-11-19T04:00:10+03:00",
                    utc_offset="+03:00", monotonic_ms=10_000),  # user flew west
    ]
    report = analyze_session(ticks)
    kinds = {a.kind for a in report.anomalies}
    assert AnomalyKind.TIMEZONE_CHANGE in kinds
    assert not report.has_critical
    assert report.verdict == "suspicious"


def test_sequence_order_disagrees_with_time():
    ticks = _ticks([
        (0, "2026-11-19T09:00:20+08:00", 0),
        (1, "2026-11-19T09:00:10+08:00", 10_000),
        (2, "2026-11-19T09:00:00+08:00", 20_000),   # seq up, time down
    ])
    report = analyze_session(ticks)
    kinds = {a.kind for a in report.anomalies}
    assert AnomalyKind.NON_MONOTONIC_SEQ in kinds


def test_anchor_conflict_capture_after_server_anchor():
    ticks = _ticks([
        (0, "2026-11-19T09:00:00+08:00", 0),   # = 01:00 UTC
        (1, "2026-11-19T09:00:10+08:00", 10_000),
    ])
    anchor = "2026-11-19T00:00:00+00:00"   # 00:00 UTC: BEFORE the captures
    report = analyze_session(ticks, server_anchor_time=anchor)
    kinds = {a.kind for a in report.anomalies}
    assert AnomalyKind.ANCHOR_CONFLICT in kinds
    assert report.has_critical


def test_anchored_clean_session_gets_medium_confidence_ceiling():
    ticks = _ticks([
        (0, "2026-11-19T01:00:00+00:00", 0),
        (1, "2026-11-19T01:00:10+00:00", 10_000),
    ])
    anchor = "2026-11-19T02:00:00+00:00"   # anchor AFTER captures: consistent
    report = analyze_session(ticks, server_anchor_time=anchor)
    assert report.verdict == "internally_consistent"
    assert report.confidence == "medium"   # documented ceiling


def test_empty_session():
    report = analyze_session([])
    assert report.verdict == "no_data"
    assert report.confidence == "none"
