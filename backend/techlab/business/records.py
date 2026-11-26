"""M03-B09/B10/B11: assets, measurement records, daily log.

B09: engineering asset model (equipment, QR/barcode/OCR fields with
     human confirmation; independent module).
B10: measurement record management (TM23 extension: raw points,
     units, calibration, uncertainty, photo/issue links, manual vs
     device source).
B11: engineering daily log (per-date activity summary; user-entered
     weather/conditions; NEVER fabricating site progress).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ============================================================ B09: assets

class ScanSource(str, Enum):
    qr_code = "qr_code"
    barcode = "barcode"
    ocr = "ocr"
    manual = "manual"


@dataclass
class AssetScan:
    """One scan observation - a CANDIDATE value that a human must
    confirm before it becomes the asset's identity."""
    source: ScanSource
    raw_value: str
    confidence: float | None = None       # OCR only; None = hardware
    confirmed_by: str | None = None       # None until human confirms
    confirmed_value: str | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_by is not None

    def confirm(self, value: str, actor: str) -> None:
        """Human confirmation: the confirmed value may CORRECT the
        raw OCR (typos happen); it is recorded beside the raw."""
        if not value:
            raise ValueError("confirmed value cannot be empty")
        self.confirmed_value = value
        self.confirmed_by = actor


class AssetError(ValueError):
    pass


@dataclass
class Asset:
    """Engineering asset/equipment record. Independent module: not
    wired into the MVP sync ops (task rule)."""
    asset_id: str
    asset_number: str
    model: str = ""
    manufacturer: str = ""
    serial_number: str = ""
    installation_date: str | None = None
    location_description: str = ""
    related_photo_ids: list[str] = field(default_factory=list)
    related_issue_ids: list[str] = field(default_factory=list)
    scans: list[AssetScan] = field(default_factory=list)
    inspection_history: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.asset_id or not self.asset_number:
            raise AssetError("asset needs id and number")

    def add_scan(self, scan: AssetScan) -> None:
        self.scans.append(scan)

    @property
    def identity_scan(self) -> AssetScan | None:
        """The scan used as the asset's identity - must be human-
        confirmed; an unconfirmed OCR reading is a CANDIDATE only."""
        for s in reversed(self.scans):
            if s.is_confirmed:
                return s
        return None

    @property
    def pending_scans(self) -> list[AssetScan]:
        return [s for s in self.scans if not s.is_confirmed]

    def record_inspection(self, date: str, result: str,
                         note: str = "",
                         photo_ids: list[str] | None = None) -> None:
        if not date or not result:
            raise AssetError("inspection needs date and result")
        self.inspection_history.append({
            "date": date, "result": result, "note": note,
            "photo_ids": photo_ids or []})


# ================================================ B10: measurement records

class MeasurementSource(str, Enum):
    """TM23's manual/device split, carried into the business layer."""
    manual_input = "manual_input"
    device_estimate = "device_estimate"


@dataclass(frozen=True)
class MeasurementLink:
    photo_id: str | None = None
    issue_id: str | None = None


class MeasurementRecordError(ValueError):
    pass


@dataclass
class ManagedMeasurement:
    """A measurement under management: value + full context + links.
    Reuses the TM23 honesty rules (uncalibrated never certified)."""
    measurement_id: str
    measurement_type: str       # distance_3d / area / angle / slope /
    #                              vertical_distance / volume (exp)
    value: float
    unit: str                   # m / ft / m2 / ft2 / deg / percent / m3
    source: MeasurementSource
    raw_points: list[tuple[float, float, float]] = \
        field(default_factory=list)
    calibration_version: str = "uncalibrated"
    uncertainty: float | None = None
    algorithm_version: str = "geometry-core/1.0"
    link: MeasurementLink = field(default_factory=MeasurementLink)
    measurement_mode: str | None = None      # scene_depth / raycast
    created_at: str = field(default_factory=lambda: datetime.now(
        timezone.utc).isoformat())
    notes: str = ""

    def __post_init__(self):
        if self.source == MeasurementSource.device_estimate \
                and self.measurement_mode is None:
            raise MeasurementRecordError(
                "device_estimate needs a measurement_mode "
                "(scene_depth / raycast / ...)")
        if self.source == MeasurementSource.manual_input \
                and self.measurement_mode is not None:
            raise MeasurementRecordError(
                "manual_input carries no measurement_mode")
        if self.measurement_type == "volume":
            if not self.notes or "experimental" not in self.notes:
                raise MeasurementRecordError(
                    "volume measurements are EXPERIMENTAL - the "
                    "notes field must say so (task rule)")

    def honest_label(self) -> str:
        if self.source == MeasurementSource.manual_input:
            return f"human-entered {self.value:g}{self.unit}"
        cal = ("uncalibrated - conservative estimate, NOT a device "
               "spec" if self.calibration_version == "uncalibrated"
               else f"calibrated ({self.calibration_version})")
        return (f"device estimate {self.value:g}{self.unit}, {cal}, "
                f"mode={self.measurement_mode}")


# ==================================================== B11: daily log

class DailyLogError(ValueError):
    pass


@dataclass
class DailyLog:
    """Per-date engineering activity summary. The COUNTS come from
    real activity; the TEXT fields are USER-ENTERED. The engine
    NEVER fabricates weather or progress - it leaves them as
    user-empty with a named reason."""
    project_id: str
    date: str                            # YYYY-MM-DD
    photos_captured: int = 0
    issues_created: int = 0
    issues_resolved: int = 0
    checklist_runs: int = 0
    measurements_recorded: int = 0
    reports_delivered: int = 0
    weather: str = ""                    # USER-ENTERED, never fetched
    site_conditions: str = ""             # USER-ENTERED
    general_notes: str = ""               # USER-ENTERED
    _activity_frozen: bool = False

    def __post_init__(self):
        import re
        if not self.date or not re.match(
                r"^\d{4}-\d{2}-\d{2}$", self.date):
            raise DailyLogError(
                f"date must be YYYY-MM-DD, got {self.date!r}")

    def set_activity(self, **counts: int) -> None:
        """The ACTIVITY counts come from the data store (real
        queries); set once, then freeze - a log entry is a snapshot."""
        if self._activity_frozen:
            raise DailyLogError(
                "activity counts are frozen once set - append a new "
                "log or regenerate")
        for k, v in counts.items():
            if not hasattr(self, k) or k in ("weather",
                                             "site_conditions",
                                             "general_notes"):
                raise DailyLogError(f"unknown activity field {k!r}")
            if v < 0:
                raise DailyLogError(f"{k} cannot be negative")
            setattr(self, k, v)
        self._activity_frozen = True

    def user_fill(self, weather: str = "", site_conditions: str = "",
                  general_notes: str = "") -> None:
        """The human fills the narrative; empty strings are HONEST
        'user did not fill this' - the engine never writes them."""
        self.weather = weather
        self.site_conditions = site_conditions
        self.general_notes = general_notes

    def summary_markdown(self) -> str:
        lines = [
            f"# Daily Log - {self.date}",
            "",
            f"## Activity",
            f"- Photos captured: {self.photos_captured}",
            f"- Issues created: {self.issues_created}",
            f"- Issues resolved: {self.issues_resolved}",
            f"- Checklist runs: {self.checklist_runs}",
            f"- Measurements: {self.measurements_recorded}",
            f"- Reports delivered: {self.reports_delivered}",
            "",
            f"## Conditions",
            f"- Weather: "
            + (self.weather or "_(not filled)_"),
            f"- Site conditions: "
            + (self.site_conditions or "_(not filled)_"),
            "",
            f"## Notes",
            self.general_notes or "_(not filled)_",
        ]
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({
            "project_id": self.project_id, "date": self.date,
            "photos_captured": self.photos_captured,
            "issues_created": self.issues_created,
            "issues_resolved": self.issues_resolved,
            "checklist_runs": self.checklist_runs,
            "measurements_recorded": self.measurements_recorded,
            "reports_delivered": self.reports_delivered,
            "weather": self.weather,
            "site_conditions": self.site_conditions,
            "general_notes": self.general_notes,
        }, indent=1, ensure_ascii=False)
