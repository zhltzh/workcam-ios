"""TM12: location quality & anomaly tests."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.location_quality import (  # noqa: E402
    LocationFix, assess_location, haversine_m)

T = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)


def fix(**kw):
    base = dict(latitude=31.2304, longitude=121.4737,
                accuracy_m=8.0, altitude_m=5.0, timestamp=T,
                source="gnss")
    base.update(kw)
    return LocationFix(**base)


def test_accuracy_bands():
    assert assess_location(fix(accuracy_m=3)).grade == "excellent"
    assert assess_location(fix(accuracy_m=15)).grade == "good"
    assert assess_location(fix(accuracy_m=50)).grade == "fair"
    assert assess_location(fix(accuracy_m=300)).grade == "poor"


def test_range_validation_flags():
    r = assess_location(fix(latitude=120.0))
    assert any("latitude out of range" in a for a in r.anomalies)
    r2 = assess_location(fix(altitude_m=30000))
    assert any("altitude beyond" in a for a in r2.anomalies)


def test_manual_source_never_graded():
    r = assess_location(fix(source="manual", accuracy_m=2))
    assert r.grade == "none"
    assert any("human-entered" in s for s in r.spoof_risk_signals)


def test_missing_accuracy_is_anomaly():
    r = assess_location(fix(accuracy_m=None))
    assert any("missing accuracy" in a for a in r.anomalies)
    assert r.grade == "none"


def test_stale_fix_detected():
    old = fix(timestamp=T - timedelta(seconds=120))
    r = assess_location(old, capture_time=T)
    assert r.stale and r.stale_age_s == 120
    assert any("predate the shutter" in a for a in r.anomalies)


def test_fresh_fix_not_stale():
    r = assess_location(fix(), capture_time=T)
    assert not r.stale


def test_teleport_detection():
    # 0.5s apart, ~5 km apart -> 10000 m/s implied
    p1 = fix(timestamp=T)
    p2 = fix(latitude=31.27, longitude=121.52,
             timestamp=T + timedelta(seconds=0.5))
    r = assess_location(p2, prior_fixes=[p1])
    assert any("TELEPORT" in a for a in r.anomalies)
    assert any("spoofing or mislabeled" in s
               for s in r.spoof_risk_signals)


def test_walking_pace_is_clean_of_teleport():
    p1 = fix(timestamp=T)
    p2 = fix(latitude=31.23050, longitude=121.4737,
             timestamp=T + timedelta(seconds=3))     # ~5.5m in 3s
    r = assess_location(p2, prior_fixes=[p1])
    assert not any("TELEPORT" in a for a in r.anomalies)


def test_precise_claim_with_jump_flagged():
    p1 = fix(accuracy_m=3, timestamp=T)
    p2 = fix(latitude=31.2335, longitude=121.4737, accuracy_m=3,
             timestamp=T + timedelta(seconds=1))     # ~340m in 1s
    r = assess_location(p2, prior_fixes=[p1])
    assert any("precision claim and motion disagree" in a
               for a in r.anomalies)


def test_altitude_jump_rate():
    p1 = fix(altitude_m=10.0, timestamp=T)
    p2 = fix(altitude_m=310.0, timestamp=T + timedelta(seconds=1))
    r = assess_location(p2, prior_fixes=[p1])
    assert any("altitude changes" in a for a in r.anomalies)


def test_honest_boundary_wording():
    r = assess_location(fix())
    assert "NOT proof" in r.honest_boundary
    assert "not evidence of genuineness" in \
        r.anomalies[-1] or "no anomalies" in r.anomalies[-1]


def test_haversine_known_distance():
    # Shanghai People's Square to the Bund ~ 1.1-1.9 km
    d = haversine_m(31.2304, 121.4737, 31.2397, 121.4900)
    assert 1000 < d < 2000
    # zero distance
    assert haversine_m(0, 0, 0, 0) == 0


def test_antimeridian_distance_reasonable():
    d = haversine_m(0, 179.999, 0, -179.999)
    assert d < 500        # ~222m across the antimeridian seam
