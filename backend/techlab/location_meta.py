"""T02 - Offline GNSS & location trustworthiness (tech-lab experiment).

Location metadata v2 + quality grading.

DESIGN RULES (from risk-register T02):
- Every location carries an explicit `source` (gps / network / manual / none).
- `manual` (user-typed) locations are NEVER presented as verified positions;
  they are honest input for report forms, not evidence.
- Quality grading is threshold-driven and auditable, not guessed.
- A "simulated" flag exists so test fixtures (Xcode Simulate Location) can
  never silently mix with field data.

EXPERIMENTAL - not wired into the production `app` package.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

import math


class LocationSource(str, Enum):
    GPS = "gps"            # Core Location GNSS fix (best effort offline)
    NETWORK = "network"    # Wi-Fi / cell positioning (offline: often stale)
    MANUAL = "manual"      # user-typed coordinates - honest, NOT evidence
    NONE = "none"          # no location obtained


class LocationQuality(str, Enum):
    """Audit-friendly grade. Thresholds from typical field-work needs:

    A: survey-grade usable for mapping   (<= 5 m horizontal)
    B: field-grade usable for pin-pointing (<= 15 m)
    C: coarse context only               (<= 50 m)
    D: unusable for position claims      (> 50 m)
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"


# Horizontal accuracy thresholds in meters (documented, testable, adjustable).
QUALITY_THRESHOLDS: dict[LocationQuality, float] = {
    LocationQuality.A: 5.0,
    LocationQuality.B: 15.0,
    LocationQuality.C: 50.0,
    # D = anything above 50 m
}


class LocationMetadata(BaseModel):
    """v2 location record attached to evidence."""

    source: LocationSource
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    horizontal_accuracy: float | None = Field(default=None, gt=0.0)
    altitude: float | None = Field(default=None, ge=-1000.0, le=100000.0)
    fix_timestamp: str | None = None        # ISO 8601, device-reported
    is_simulated: bool = False               # Xcode Simulate Location fixture
    note: str | None = None                  # free text, e.g. "B2 basement"

    @field_validator("latitude", "longitude", "horizontal_accuracy", "altitude")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("NaN/Infinity not allowed in location fields")
        return v

    @field_validator("note")
    @classmethod
    def _no_newline(cls, v: str | None) -> str | None:
        if v is not None and ("\n" in v or "\r" in v):
            raise ValueError("note must not contain newlines")
        return v

    def is_evidence_grade(self) -> bool:
        """Can this location be attached to evidence as a POSITION claim?

        manual/none sources are honest context but never a position claim.
        simulated fixtures are never evidence.
        """
        if self.source not in (LocationSource.GPS, LocationSource.NETWORK):
            return False
        if self.is_simulated:
            return False
        return self.latitude is not None and self.longitude is not None

    def grade(self) -> LocationQuality:
        """Threshold-driven horizontal-accuracy grade (D when unknown)."""
        if self.source in (LocationSource.MANUAL, LocationSource.NONE):
            return LocationQuality.D
        if self.source == LocationSource.NETWORK:
            # offline Wi-Fi/cell fixes are routinely 30-100 m off: cap at C
            return LocationQuality.C
        acc = self.horizontal_accuracy
        if acc is None:
            return LocationQuality.D
        for quality, threshold in QUALITY_THRESHOLDS.items():
            if acc <= threshold:
                return quality
        return LocationQuality.D

    def trust_label(self) -> str:
        """Human-facing honesty label. NEVER let manual look like verified."""
        if self.is_simulated:
            return f"{self.source.value}: SIMULATED - not evidence"
        if not self.is_evidence_grade():
            base = {
                LocationSource.MANUAL: "user-entered (not verified)",
                LocationSource.NONE: "no location",
                LocationSource.GPS: "not a position claim",
                LocationSource.NETWORK: "not a position claim",
            }[self.source]
            return f"{self.source.value}: {base}"
        return f"verified {self.source.value}, grade {self.grade().value}"


def grade_offline_batch(records: list[LocationMetadata]) -> dict:
    """Assess a capture session's location data quality.

    Returns counts per grade/source plus a session verdict used by field
    QA tooling. Does not invent data: records without fix are counted as
    missing rather than graded.
    """
    summary: dict[str, int] = {
        "total": len(records),
        "missing_fix": 0,
        "manual": 0,
        "simulated": 0,
        "grades": {g.value: 0 for g in LocationQuality},
    }
    for rec in records:
        if rec.is_simulated:
            summary["simulated"] += 1
        if rec.source == LocationSource.MANUAL:
            summary["manual"] += 1
        if rec.latitude is None or rec.longitude is None:
            summary["missing_fix"] += 1
        summary["grades"][rec.grade().value] += 1

    evidence = [r for r in records if r.is_evidence_grade()]
    summary["evidence_gradeable"] = len(evidence)
    if not records:
        summary["verdict"] = "no_data"
    elif summary["evidence_gradeable"] == 0:
        summary["verdict"] = "no_position_claims"
    else:
        summary["verdict"] = "usable"
    return summary
