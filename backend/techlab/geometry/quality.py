"""TM22: measurement uncertainty, error propagation & quality.

Pure math, Swift-portable (TM61). HONESTY CONTRACTS:

  1. No calibration data -> NO fabricated confidence intervals.
     Quality states carry the reason they are unknown.
  2. Error propagation is the standard first-order variance formula:
     var(f) = sum (df/dxi)^2 var(xi)  - implemented for the actual
     WorkCam measurement chains (distance, area, volume, slope).
  3. Tracking-state / material / lighting influences are recorded as
     QUALITY FACTORS, never silently folded into a number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from techlab.geometry.camera import _require_finite
from techlab.geometry.measurement import Point3


class TrackingState(str, Enum):
    """ARKit tracking states as measurement quality context."""
    normal = "normal"
    limited_excessive_motion = "limited_excessive_motion"
    limited_insufficient_features = "limited_insufficient_features"
    initializing = "initializing"
    not_available = "not_available"


class MeasurementQuality(str, Enum):
    good = "good"
    fair = "fair"
    poor = "poor"
    invalid = "invalid"


_TRACKING_PENALTY = {
    TrackingState.normal: 0,
    TrackingState.initializing: 1,
    TrackingState.limited_insufficient_features: 1,
    TrackingState.limited_excessive_motion: 2,
    TrackingState.not_available: 3,
}


@dataclass(frozen=True)
class CalibrationProfile:
    """Device-model calibration: per-distance sigma and bias. An
    UNCALIBRATED profile (None) is the honest default until Mac runs
    the real-device calibration experiments (TM22 device half)."""
    device_model: str
    sigma_intercept_m: float         # distance-independent noise
    sigma_per_meter: float           # distance-proportional noise
    bias_m: float = 0.0              # systematic offset (signed)
    calibration_version: str = "uncalibrated"

    def sigma_at(self, distance_m: float) -> float:
        _require_finite(distance_m)
        return math.hypot(self.sigma_intercept_m,
                          self.sigma_per_meter * distance_m)

    @classmethod
    def uncalibrated(cls) -> "CalibrationProfile":
        """The profile every report MUST use until real calibration
        data lands: conservative defaults, explicitly versioned so no
        output can masquerade as calibrated."""
        return cls(device_model="unknown",
                   sigma_intercept_m=0.05,
                   sigma_per_meter=0.02,
                   bias_m=0.0,
                   calibration_version="uncalibrated")


@dataclass(frozen=True)
class MeasurementQualityReport:
    quality: MeasurementQuality
    tracking_state: TrackingState
    calibration: CalibrationProfile
    sigma_m: float | None            # None = cannot even estimate
    factors: list[str] = field(default_factory=list)
    confidence_interval_m: tuple[float, float] | None = None
    honesty_note: str = (
        "quality factors are recorded, never averaged into a score; "
        "an uncalibrated profile yields a CONSERVATIVE interval "
        "explicitly marked as such, never a device-calibrated claim")


def assess_point_measurement(
        measured_distance_m: float,
        point_sigma_m: float,
        tracking: TrackingState,
        calibration: CalibrationProfile | None = None,
        depth_confidence: float | None = None,
        surface_material: str | None = None,
        illumination: str | None = None) -> MeasurementQualityReport:
    """Quality assessment for a distance/point measurement.

    measured_distance_m is the |baseline distance| the user reads.
    point_sigma_m is the PER-POINT sigma (from depth confidence or
    the calibration model). depth_confidence, surface_material and
    illumination are FACTORS: they degrade the report and appear in
    factors[]; they never fake-boost it.
    """
    _require_finite(measured_distance_m)
    cal = calibration or CalibrationProfile.uncalibrated()
    factors: list[str] = []
    sigma = point_sigma_m

    if cal.calibration_version == "uncalibrated":
        factors.append("device not calibrated - conservative model "
                       "used, interval is NOT a device spec")
    penalty = _TRACKING_PENALTY.get(tracking, 2)
    if tracking != TrackingState.normal:
        factors.append(f"tracking degraded: {tracking.value}")
    if depth_confidence is not None:
        _require_finite(depth_confidence)
        if depth_confidence < 0.5:
            factors.append(f"low depth confidence "
                           f"({depth_confidence:.2f})")
    if surface_material in ("glass", "mirror", "dark", "shiny"):
        factors.append(f"difficult surface for depth: "
                       f"{surface_material}")
    if illumination == "low_light":
        factors.append("low light degrades depth quality")

    # effective sigma inflates with degraded tracking (multiplicative
    # guard band - deliberately conservative, factor-stated)
    inflation = 1.0 + 0.5 * penalty
    sigma_eff = sigma * inflation
    quality = MeasurementQuality.good
    if penalty >= 1 or (depth_confidence is not None
                       and depth_confidence < 0.5):
        quality = MeasurementQuality.fair
    if penalty >= 2:
        quality = MeasurementQuality.poor
    if penalty >= 3 or not math.isfinite(sigma) or sigma <= 0:
        quality = MeasurementQuality.invalid

    ci = None
    if quality != MeasurementQuality.invalid:
        ci = (measured_distance_m - 2 * sigma_eff,
              measured_distance_m + 2 * sigma_eff)
    return MeasurementQualityReport(quality=quality,
                                    tracking_state=tracking,
                                    calibration=cal,
                                    sigma_m=sigma_eff,
                                    factors=factors,
                                    confidence_interval_m=ci)


# ------------------------------------------------------- error propagation

def propagate_distance_sigma(a_sigma: float, b_sigma: float) -> float:
    """|A - B| where A, B independent: sigma_d = sqrt(sa^2 + sb^2)."""
    _require_finite(a_sigma, b_sigma)
    return math.hypot(a_sigma, b_sigma)


def propagate_area_sigma(edge_sigmas: list[float],
                          edges: list[float]) -> float:
    """Rectangle area A = w*h with independent sigmas:
     sigma_A = A * sqrt((sw/w)^2 + (sh/h)^2) - exact first-order."""
    if len(edge_sigmas) != 2 or len(edges) != 2:
        raise ValueError("rectangle propagation needs (sw, sh), (w, h)")
    sw, sh = edge_sigmas
    w, h = edges
    _require_finite(sw, sh, w, h)
    if w <= 0 or h <= 0:
        raise ValueError("edges must be positive")
    a = w * h
    return a * math.hypot(sw / w, sh / h)


def propagate_volume_extrusion_sigma(
        base_area: float, area_sigma: float,
        height: float, height_sigma: float) -> float:
    """V = A*h: sigma_V = V * sqrt((sA/A)^2 + (sh/h)^2)."""
    _require_finite(base_area, area_sigma, height, height_sigma)
    if base_area <= 0 or height <= 0:
        raise ValueError("area and height must be positive")
    v = base_area * height
    return v * math.hypot(area_sigma / base_area,
                          height_sigma / height)


def propagate_slope_sigma(horizontal: float, h_sigma: float,
                          vertical: float,
                          v_sigma: float) -> dict:
    """First-order sigma for slope_deg = atan2(v, h):
     dsigma = |grad| . (sh, sv) with grad computed analytically.
    Returns {'sigma_deg': s, 'valid': bool, 'reason': str}."""
    _require_finite(horizontal, h_sigma, vertical, v_sigma)
    if horizontal <= 0:
        return {"sigma_deg": float("nan"), "valid": False,
                "reason": "non-positive horizontal run - slope "
                          "sigma undefined (vertical face)"}
    r2 = horizontal * horizontal + vertical * vertical
    # d(theta)/dh = -v / (h^2 + v^2); d(theta)/dv = h / (h^2+v^2)
    dth_dh = -vertical / r2
    dth_dv = horizontal / r2
    var = (dth_dh * h_sigma) ** 2 + (dth_dv * v_sigma) ** 2
    sigma_rad = math.sqrt(var)
    return {"sigma_deg": math.degrees(sigma_rad), "valid": True,
            "reason": ""}


def repeatability_std(values: list[float]) -> float | None:
    """Sample standard deviation of repeated measurements under
    IDENTICAL conditions. < 2 values -> None (a single measurement has
    no repeatability; returning 0 would be a lie)."""
    n = len(values)
    if n < 2:
        return None
    for v in values:
        _require_finite(v)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)
