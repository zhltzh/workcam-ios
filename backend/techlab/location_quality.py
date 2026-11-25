"""TM12: location quality & anomaly risk assessment.

W024's location state machine grades SOURCE trust (gnss/manual/none).
This module adds QUALITY + ANOMALY judgment over a STREAM of fixes:

  - range validation          (lat/lon/accuracy sanity)
  - stale-fix detection       (age of fix at capture time)
  - accuracy grading          (meters -> grade band)
  - teleport detection        (implied speed between fixes)
  - altitude anomalies        (jump rate, impossible deltas)
  - consistency across samples (multi-fix coherence)

HONESTY RULES (task brief):
  - risk scores are RISK SIGNALS, never "GPS anti-spoofing proof"
  - an unverifiable source is NEVER reported as an authenticated fix
  - every anomaly names its evidence; absence of anomalies is NOT
    proof of genuineness
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import math


R_EARTH_M = 6_371_000.0
GRAVITY_STANDARD_M = 9.80665


@dataclass
class LocationFix:
    latitude: float
    longitude: float
    accuracy_m: float | None          # horizontalAccuracy
    altitude_m: float | None = None
    vertical_accuracy_m: float | None = None
    timestamp: datetime | None = None      # fix timestamp (device)
    source: str = "gnss"                # gnss / manual / none / unknown

    def validate(self) -> list[str]:
        problems = []
        if not -90 <= self.latitude <= 90:
            problems.append("latitude out of range")
        if not -180 <= self.longitude <= 180:
            problems.append("longitude out of range")
        if self.accuracy_m is not None and self.accuracy_m < 0:
            problems.append("negative accuracy")
        if self.altitude_m is not None and abs(self.altitude_m) > 20000:
            problems.append("altitude beyond plausible range")
        if not math.isfinite(self.latitude) or \
                not math.isfinite(self.longitude):
            problems.append("non-finite coordinates")
        return problems


def haversine_m(lat1: float, lon1: float,
                lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * \
        math.sin(dl / 2) ** 2
    return 2 * R_EARTH_M * math.asin(math.sqrt(a))


@dataclass
class LocationRiskReport:
    grade: str                      # excellent/good/fair/poor/none
    stale: bool
    stale_age_s: float | None = None
    anomalies: list[str] = field(default_factory=list)
    spoof_risk_signals: list[str] = field(default_factory=list)
    honest_boundary: str = (
        "Risk signals are anomaly prompts for review, NOT proof of "
        "position authenticity; consumer GNSS remains spoofable and an "
        "absence of anomalies is not evidence of genuineness.")

    def as_dict(self) -> dict:
        return {
            "grade": self.grade, "stale": self.stale,
            "stale_age_s": self.stale_age_s,
            "anomalies": self.anomalies,
            "spoof_risk_signals": self.spoof_risk_signals,
            "honest_boundary": self.honest_boundary,
        }


ACCURACY_BANDS = [
    (5.0, "excellent"),
    (20.0, "good"),
    (75.0, "fair"),
    (float("inf"), "poor"),
]

STALENESS_S = 30.0                # fix older than 30s at shutter
TELEPORT_SPEED_MPS = 415.0        # >~1500 km/h implied ground speed
ALTITUDE_JUMP_RATE_MPS = 50.0     # 3 m/s vertical is already extreme;
                                  # 50 is physically impossible on foot


def assess_location(fix: LocationFix,
                    prior_fixes: list[LocationFix] | None = None,
                    capture_time: datetime | None = None) \
        -> LocationRiskReport:
    """Assess ONE fix (optionally against the recent stream). The
    grade describes PRECISION claims; anomalies describe motion or
    data inconsistencies - neither is ever 'authenticated'."""
    structural = fix.validate()
    rep = LocationRiskReport(grade="none", stale=False,
                             anomalies=list(structural))
    if fix.source == "manual":
        rep.spoof_risk_signals.append(
            "manual location: human-entered, no sensor basis - "
            "cannot be graded for precision at all")
        rep.grade = "none"
    elif fix.source == "none" or (fix.latitude == 0 and
                                  fix.longitude == 0 and
                                  fix.accuracy_m is None):
        rep.grade = "none"
    else:
        # accuracy grade
        if fix.accuracy_m is None:
            rep.anomalies.append("missing accuracy field - "
                                 "precision ungradeable")
        else:
            rep.grade = next(
                band for lim, band in ACCURACY_BANDS
                if fix.accuracy_m <= lim)
            if fix.accuracy_m > 500:
                rep.spoof_risk_signals.append(
                    f"very coarse fix ({fix.accuracy_m:.0f}m) - "
                    f"indoor/cold-start or degraded reception")

    # staleness vs shutter time
    if fix.timestamp is not None and capture_time is not None:
        age = (capture_time - fix.timestamp).total_seconds()
        if age > STALENESS_S:
            rep.stale = True
            rep.stale_age_s = age
            rep.anomalies.append(
                f"fix is {age:.0f}s older than the capture - position "
                f"may predate the shutter by a meaningful walk")

    # stream anomalies
    prior = prior_fixes or []
    if prior:
        prev = prior[-1]
        if prev.timestamp is not None and fix.timestamp is not None:
            dt = (fix.timestamp - prev.timestamp).total_seconds()
            if dt > 0:
                d = haversine_m(prev.latitude, prev.longitude,
                                fix.latitude, fix.longitude)
                speed = d / dt
                if speed > TELEPORT_SPEED_MPS:
                    rep.anomalies.append(
                        f"TELEPORT: implied ground speed "
                        f"{speed:.0f} m/s over {dt:.1f}s "
                        f"({d:.0f}m) - physically implausible jump")
                    rep.spoof_risk_signals.append(
                        "position jump beyond any vehicular speed "
                        "- spoofing or mislabeled timestamps are "
                        "candidate explanations (not conclusions)")
                # accuracy consistency: a fix claiming 3m precision
                # jumping 400m between two adjacent seconds is odd
                if (fix.accuracy_m is not None
                        and fix.accuracy_m <= 5 and d > 200 and dt < 2):
                    rep.anomalies.append(
                        "claimed-precise fix with a large jump - "
                        "precision claim and motion disagree")
                # altitude jump rate
                if (fix.altitude_m is not None
                        and prev.altitude_m is not None):
                    v_speed = abs(fix.altitude_m - prev.altitude_m) / dt
                    if v_speed > ALTITUDE_JUMP_RATE_MPS:
                        rep.anomalies.append(
                            f"altitude changes at {v_speed:.0f} m/s - "
                            f"barometer glitch or spoofed altitude")

    if not rep.anomalies and not rep.spoof_risk_signals:
        rep.anomalies.append(
            "no anomalies observed - NOT evidence of genuineness")
    return rep
