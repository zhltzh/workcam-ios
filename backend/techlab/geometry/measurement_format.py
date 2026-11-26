"""TM23: open measurement-record formats.

One measurement = one self-describing record. The record carries ALL
the context the task brief demands (project/photo refs, raw points,
coordinate system, units, device + mode, tracking state, confidence,
calibration version, uncertainty, algorithm version) and serializes
to JSON / CSV / GeoJSON / PLY / SVG.

HONESTY RULES (task brief):
  - manual input and device estimate are EXPLICITLY different
    (input_kind field; a manual tape value never pretends to be a
    LiDAR reading and vice versa)
  - an uncalibrated device measurement is marked by its
    calibration_version ("uncalibrated") - never presented as a
    certified measurement
  - this format is a TECH-LAB exchange format. It is NOT part of the
    production evidence protocol; evidence linking stays with the
    signed-photo pipeline (the isolation is the point).
"""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass, field
from enum import Enum

from techlab.geometry.camera import _require_finite
from techlab.geometry.geo import Geodetic, to_geojson

FORMAT_VERSION = "measurement-format/1.0"


class MeasurementType(str, Enum):
    distance_3d = "distance_3d"
    horizontal_distance = "horizontal_distance"
    vertical_distance = "vertical_distance"
    polyline_length = "polyline_length"
    area = "area"
    volume = "volume"
    angle = "angle"
    slope = "slope"


class InputKind(str, Enum):
    """THE manual/device split (task requirement)."""
    manual_input = "manual_input"        # human-entered (tape, ruler)
    device_estimate = "device_estimate"  # ARKit/LiDAR-derived
    mixed = "mixed"                       # e.g. two picked AR points


class Units(str, Enum):
    meters = "m"
    feet = "ft"
    square_meters = "m2"
    square_feet = "ft2"
    cubic_meters = "m3"
    cubic_feet = "ft3"
    degrees = "deg"
    percent = "percent"


class CoordinateSystem(str, Enum):
    camera = "camera"        # ARKit camera space (x right, y up,
    #                          z away, meters)
    world = "world"          # ARKit world/session space
    local_enu = "local_enu"   # site ENU frame (TM13)
    wgs84 = "wgs84"          # geodetic


@dataclass(frozen=True)
class MeasurementRecord:
    measurement_id: str
    measurement_type: MeasurementType
    input_kind: InputKind
    coordinate_system: CoordinateSystem
    units: Units
    value: float
    raw_points: list[tuple[float, float, float]] = field(default_factory=list)
    project_id: str | None = None
    photo_id: str | None = None
    issue_id: str | None = None
    device_model: str | None = None
    measurement_mode: str | None = None       # raycast / scene_depth /
    #                                           smoothed_scene_depth
    tracking_state: str | None = None
    confidence: float | None = None
    calibration_version: str = "uncalibrated"
    uncertainty: float | None = None          # 1-sigma, in `units`
    algorithm_version: str = "geometry-core/1.0"
    geodetic_anchor: Geodetic | None = None    # for wgs84/local_enu
    notes: str = ""

    def __post_init__(self):
        _require_finite(self.value)
        for p in self.raw_points:
            _require_finite(*p)
        if self.confidence is not None:
            _require_finite(self.confidence)
        if self.uncertainty is not None:
            _require_finite(self.uncertainty)
        if self.input_kind == InputKind.manual_input:
            # a manual value has no device semantics
            if self.measurement_mode is not None:
                raise ValueError(
                    "manual_input measurements carry no measurement_mode"
                    " - the manual/device split is structural")
        if self.measurement_type == MeasurementType.distance_3d \
                and len(self.raw_points) != 2:
            raise ValueError("distance_3d needs exactly 2 raw points")

    # ---------------------------------------------------------- JSON

    def to_json(self) -> str:
        return json.dumps(self._as_dict(), indent=1, ensure_ascii=False)

    def _as_dict(self) -> dict:
        d = {
            "format_version": FORMAT_VERSION,
            "measurement_id": self.measurement_id,
            "measurement_type": self.measurement_type.value,
            "input_kind": self.input_kind.value,
            "coordinate_system": self.coordinate_system.value,
            "units": self.units.value,
            "value": self.value,
            "raw_points": [list(p) for p in self.raw_points],
            "calibration_version": self.calibration_version,
            "algorithm_version": self.algorithm_version,
        }
        for key in ("project_id", "photo_id", "issue_id",
                    "device_model", "measurement_mode",
                    "tracking_state", "notes"):
            v = getattr(self, key)
            if v is not None and v != "":
                d[key] = v
        if self.confidence is not None:
            d["confidence"] = self.confidence
        if self.uncertainty is not None:
            d["uncertainty"] = self.uncertainty
        if self.geodetic_anchor is not None:
            g = self.geodetic_anchor
            d["geodetic_anchor"] = {
                "latitude": g.latitude, "longitude": g.longitude,
                "altitude_m": g.altitude_m}
        if self.input_kind == InputKind.device_estimate:
            # honest marker carried beside the value, not buried
            d["measurement_nature"] = (
                "device ESTIMATE, calibration="
                f"{self.calibration_version} - not a certified "
                f"measurement") if self.calibration_version == \
                "uncalibrated" else (
                f"device estimate, calibration="
                f"{self.calibration_version}")
        else:
            d["measurement_nature"] = "human-entered value"
        return d

    @classmethod
    def from_json(cls, text: str) -> "MeasurementRecord":
        d = json.loads(text)
        if d.get("format_version") != FORMAT_VERSION:
            raise ValueError(
                f"unknown format_version {d.get('format_version')!r} "
                f"- expected {FORMAT_VERSION}")
        anchor = None
        if "geodetic_anchor" in d:
            a = d["geodetic_anchor"]
            anchor = Geodetic(latitude=a["latitude"],
                              longitude=a["longitude"],
                              altitude_m=a.get("altitude_m"))
        return cls(
            measurement_id=d["measurement_id"],
            measurement_type=MeasurementType(d["measurement_type"]),
            input_kind=InputKind(d["input_kind"]),
            coordinate_system=CoordinateSystem(
                d["coordinate_system"]),
            units=Units(d["units"]),
            value=d["value"],
            raw_points=[tuple(p) for p in d["raw_points"]],
            project_id=d.get("project_id"),
            photo_id=d.get("photo_id"),
            issue_id=d.get("issue_id"),
            device_model=d.get("device_model"),
            measurement_mode=d.get("measurement_mode"),
            tracking_state=d.get("tracking_state"),
            confidence=d.get("confidence"),
            calibration_version=d.get("calibration_version",
                                       "uncalibrated"),
            uncertainty=d.get("uncertainty"),
            algorithm_version=d.get("algorithm_version",
                                    "geometry-core/1.0"),
            geodetic_anchor=anchor,
            notes=d.get("notes", ""))

    # ---------------------------------------------------------- CSV

    def to_csv_row(self) -> dict:
        """Flat CSV projection (geometry columns quoted as JSON)."""
        row = {
            "measurement_id": self.measurement_id,
            "measurement_type": self.measurement_type.value,
            "input_kind": self.input_kind.value,
            "coordinate_system": self.coordinate_system.value,
            "units": self.units.value,
            "value": self.value,
            "raw_points": json.dumps([list(p)
                                      for p in self.raw_points]),
            "calibration_version": self.calibration_version,
            "algorithm_version": self.algorithm_version,
        }
        for key in ("project_id", "photo_id", "issue_id",
                    "device_model", "measurement_mode",
                    "tracking_state"):
            row[key] = getattr(self, key) or ""
        row["confidence"] = self.confidence \
            if self.confidence is not None else ""
        row["uncertainty"] = self.uncertainty \
            if self.uncertainty is not None else ""
        return row


def records_to_csv(records: list[MeasurementRecord]) -> str:
    if not records:
        raise ValueError("no records")
    buf = io.StringIO()
    fieldnames = list(records[0].to_csv_row().keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in records:
        writer.writerow(r.to_csv_row())
    return buf.getvalue()


# ---------------------------------------------------------- GeoJSON

def records_to_geojson(records: list[MeasurementRecord]) -> str:
    """GeoJSON FeatureCollection: each record anchored at its
    geodetic_anchor (wgs84 measurements with anchors only - records
    without an anchor are SKIPPED and COUNTED, not fabricated)."""
    features = []
    skipped = 0
    for r in records:
        if r.geodetic_anchor is None:
            skipped += 1
            continue
        g = r.geodetic_anchor
        pos = [g.longitude, g.latitude]
        if g.altitude_m is not None:
            pos.append(g.altitude_m)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": pos},
            "properties": {
                "measurement_id": r.measurement_id,
                "measurement_type": r.measurement_type.value,
                "input_kind": r.input_kind.value,
                "units": r.units.value,
                "value": r.value,
                "uncertainty": r.uncertainty,
                "calibration_version": r.calibration_version,
            }})
    doc = {"type": "FeatureCollection", "features": features,
           "x_workcam_notes": {
               "format_version": FORMAT_VERSION,
               "skipped_no_anchor": skipped,
               "lon_lat_order": "RFC 7946: positions are "
                                "[lon, lat, alt]"}}
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------- PLY

def records_to_ply(records: list[MeasurementRecord],
                   comment: str = "") -> str:
    """All raw points of all records as one ASCII PLY cloud. The
    measurement_id each point belongs to rides in the header comment
    block (PLY has no per-point string properties in the portable
    subset - honest limitation, stated here)."""
    from techlab.geometry.pointcloud import CloudPoint, to_ply
    cloud: list[CloudPoint] = []
    header_notes = [f"workcam {FORMAT_VERSION}"]
    for r in records:
        header_notes.append(f"record {r.measurement_id}: "
                            f"{r.measurement_type.value}="
                            f"{r.value}{r.units.value} "
                            f"({r.input_kind.value})")
        for p in r.raw_points:
            cloud.append(CloudPoint(p[0], p[1], p[2], r.confidence))
    return to_ply(cloud, comment="\n".join(
        [comment] if comment else []) + "\n".join(header_notes))


# ---------------------------------------------------------- SVG

def records_to_svg(records: list[MeasurementRecord],
                   width: int = 640, height: int = 480,
                   title: str = "WorkCam measurement sketch"
                   ) -> str:
    """SIMPLE engineering sketch: raw points projected to the XY
    plane, auto-fitted viewport, per-record polylines + value labels.
    This is a DIAGRAM, not a scaled drawing (no real-world scale bar
    without a known reference length - stated)."""
    if not records:
        raise ValueError("no records")
    pts = [p for r in records for p in r.raw_points]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span = max(max_x - min_x, max_y - min_y, 1e-6)
    pad = 40.0
    scale = (min(width, height) - 2 * pad) / span

    def map_pt(p: tuple[float, float, float]) -> tuple[float, float]:
        sx = pad + (p[0] - min_x) * scale
        sy = height - pad - (p[1] - min_y) * scale   # SVG y is down
        return sx, sy

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        f'<title>{title}</title>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{pad}" y="20" font-size="12" fill="#333">'
        f'WorkCam {FORMAT_VERSION} - XY projection sketch (not to '
        f'scale without a reference length)</text>',
    ]
    palette = ["#c0392b", "#2980b9", "#27ae60", "#8e44ad", "#d35400"]
    for i, r in enumerate(records):
        color = palette[i % len(palette)]
        if len(r.raw_points) >= 2:
            d = " ".join(
                f"{x:.1f},{y:.1f}"
                for x, y in map(map_pt, r.raw_points))
            parts.append(
                f'<polyline points="{d}" fill="none" stroke="{color}"'
                f' stroke-width="2" stroke-linejoin="round"/>')
        for j, p in enumerate(r.raw_points):
            sx, sy = map_pt(p)
            parts.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" '
                         f'r="3" fill="{color}"/>')
        sx, sy = map_pt(r.raw_points[0]) if r.raw_points \
            else (pad, pad)
        label = (f"{r.measurement_type.value}="
                 f"{r.value:g}{r.units.value} "
                 f"[{r.input_kind.value}]")
        parts.append(
            f'<text x="{sx + 8:.1f}" y="{sy - 8:.1f}" font-size="11" '
            f'fill="{color}">{label}</text>')
    parts.append("</svg>")
    return "\n".join(parts)
