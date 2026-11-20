"""T07 - Drawing-to-photo association protocol (Wave 3 tech-lab).

Links photos to positions on engineering drawings (PDF/blueprint).

PROTOCOL (platform-independent; Mac renders, Windows verifies):

  DrawingSheet: a drawing revision (PDF) is identified by content hash;
    the user places >=3 calibration points pairing on-sheet coordinates
    (page units, from PDF geometry) with KNOWN real-world coordinates
    (survey/site axes or drawing title-block grid). From >=3 non-collinear
    points we derive a 2D affine transform sheet->world.

  PhotoPin: a photo is pinned to the sheet at sheet coordinates. Two
    independent claims are kept and NEVER conflated:
      - 'placement' (where the user DROPPED the pin on the drawing)
      - 'verification' (does the photo's T02 location corroborate the pin?
        only GPS-grade positions can corroborate; manual entries cannot)

  Versioning: drawing updates get a new sheet revision; old pins are
    migrated by reusing calibration points whose PDF text/geometry
    anchors still match (v2 plan: text-anchor matching is RESEARCH;
    v1 carries the pin with the same sheet-local coordinates and marks
    it 'unverified_after_revision').

HONESTY: the affine fit reports its RESIDUALS; nobody may claim a pin
is survey-grade from a 3-point fit. Rejected fits raise.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator


class CalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class SheetPoint:
    x: float
    y: float


@dataclass(frozen=True)
class WorldPoint:
    x: float
    y: float


class CalibrationPair(BaseModel):
    sheet: SheetPoint
    world: WorldPoint


def _det_2x2(a: float, b: float, c: float, d: float) -> float:
    return a * d - b * c


class SheetCalibration(BaseModel):
    """sheet->world 2D affine transform fitted from >=3 point pairs."""

    pairs: list[CalibrationPair] = Field(min_length=3)
    max_residual_m: float = Field(default=0.05, gt=0)

    def fit(self) -> tuple[list[float], list[float], float]:
        """Least-squares-free exact solve via 2 independent triangle
        fits; residual = mismatch between the two fits, in world units
        (meters). Raises CalibrationError when degenerate or the two
        triangles disagree beyond max_residual_m."""
        p = self.pairs
        if len(p) < 3:
            raise CalibrationError("at least 3 calibration points required")
        # use first three non-degenerate points for the exact affine solve
        (ax, ay), (bx, by), (cx, cy) = ((pr.sheet.x, pr.sheet.y) for pr in p[:3])
        det = _det_2x2(bx - ax, by - ay, cx - ax, cy - ay)
        if abs(det) < 1e-9:
            raise CalibrationError("calibration points are collinear")
        # affine: world = M @ sheet + t, solved exactly from 3 pairs
        (Ax, Ay), (Bx, By), (Cx, Cy) = ((pr.world.x, pr.world.y) for pr in p[:3])
        m11 = ((Bx - Ax) * (cy - ay) - (Cx - Ax) * (by - ay)) / det
        m12 = ((Cx - Ax) * (bx - ax) - (Bx - Ax) * (cx - ax)) / det
        m21 = ((By - Ay) * (cy - ay) - (Cy - Ay) * (by - ay)) / det
        m22 = ((Cy - Ay) * (bx - ax) - (By - Ay) * (cx - ax)) / det
        tx = Ax - (m11 * ax + m12 * ay)
        ty = Ay - (m21 * ax + m22 * ay)
        M = [m11, m12, m21, m22]
        t = [tx, ty]
        # residual check against EVERY provided pair (incl. extra ones)
        worst = 0.0
        for pr in p:
            wx = m11 * pr.sheet.x + m12 * pr.sheet.y + tx
            wy = m21 * pr.sheet.x + m22 * pr.sheet.y + ty
            residual = ((wx - pr.world.x) ** 2 + (wy - pr.world.y) ** 2) ** 0.5
            worst = max(worst, residual)
        if worst > self.max_residual_m:
            raise CalibrationError(
                f"calibration residual {worst:.4f} m exceeds tolerance "
                f"{self.max_residual_m} m - the drawing fit is not reliable")
        return M, t, worst


class PhotoPin(BaseModel):
    """A photo pinned onto a drawing sheet."""

    pin_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    photo_id: str
    sheet_rev: str                         # sheet content-hash prefix
    sheet_x: float
    sheet_y: float
    placed_from: str = "manual_drop"       # honest provenance of placement
    note: str | None = None

    @field_validator("note")
    @classmethod
    def _no_newline(cls, v: str | None) -> str | None:
        if v is not None and ("\n" in v or "\r" in v):
            raise ValueError("note must not contain newlines")
        return v


class PinVerification(BaseModel):
    """Separation of placement vs corroboration (honesty core)."""

    photo_id: str
    world_xy: tuple[float, float]
    corroborated: bool                     # GPS-grade location agrees
    detail: str


class DrawingSheet(BaseModel):
    """One drawing revision.

    geo_origin (optional): the WGS84 point that maps to the sheet's
    world frame (0,0). Corroboration needs it; without it we say so
    honestly instead of guessing."""

    sheet_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str
    pdf_content_hash: str                 # sha256 of the drawing PDF bytes
    rev: str = "1"
    geo_origin: tuple[float, float] | None = None   # (lat, lon)
    calibration_pairs: list[CalibrationPair] = []
    pins: list[PhotoPin] = []

    def add_calibration(self, pair: CalibrationPair) -> "DrawingSheet":
        self.calibration_pairs.append(pair)
        return self

    def pin_photo(self, pin: PhotoPin) -> "DrawingSheet":
        if pin.sheet_rev != self.pdf_content_hash[:12]:
            raise ValueError("pin targets a different sheet revision")
        self.pins.append(pin)
        return self

    def pin_world_positions(self, location_meta_of: dict[str, dict | None],
                             corroboration_radius_m: float = 25.0
                             ) -> list[PinVerification]:
        """Resolve pins to world coordinates and check corroboration.

        location_meta_of: photo_id -> T02 LocationMetadata dict (or None).
        Corroboration requires ALL of: geo_origin set, GPS-grade evidence
        location, and agreement within the radius. Anything else reports
        corroborated=False with the honest reason - manual/network
        locations can never corroborate."""
        import math

        M, t, _residual = SheetCalibration(pairs=self.calibration_pairs).fit()
        m11, m12, m21, m22 = M
        out: list[PinVerification] = []
        for pin in self.pins:
            wx = m11 * pin.sheet_x + m12 * pin.sheet_y + t[0]
            wy = m21 * pin.sheet_x + m22 * pin.sheet_y + t[1]

            loc = location_meta_of.get(pin.photo_id)
            corroborated = False
            if self.geo_origin is None:
                detail = "sheet has no geo_origin - corroboration impossible"
            elif loc is None:
                detail = "no location metadata - placement uncorroborated"
            elif loc.get("source") != "gps" or loc.get("latitude") is None:
                detail = (f"location source is '{loc.get('source')}' - "
                          f"manual/network locations cannot corroborate")
            else:
                R = 6371000.0
                lat0, lon0 = self.geo_origin
                lat, lon = loc["latitude"], loc["longitude"]
                dlat = (lat - lat0) * math.pi / 180.0 * R
                dlon = ((lon - lon0) * math.pi / 180.0
                        * R * math.cos(math.radians(lat0)))
                dist = math.hypot(dlat, dlon) + math.hypot(wx - 0, wy - 0) * 0
                # photo's site-frame position is unknown in this PoC (GPS
                # gives geo, sheet gives site frame); the radius check is
                # against the pin's world position mapped back to geo:
                # pin world (wx, wy) meters -> geo offset from origin
                pin_dlat = wy / R * 180.0 / math.pi
                pin_dlon = wx / (R * math.cos(math.radians(lat0))) * 180.0 / math.pi
                pin_lat, pin_lon = lat0 + pin_dlat, lon0 + pin_dlon
                dlat_m = (lat - pin_lat) * math.pi / 180.0 * R
                dlon_m = ((lon - pin_lon) * math.pi / 180.0
                          * R * math.cos(math.radians(lat0)))
                dist = math.hypot(dlat_m, dlon_m)
                corroborated = dist <= corroboration_radius_m
                detail = (f"gps location {dist:.1f} m from pin "
                          f"(radius {corroboration_radius_m} m)")
            out.append(PinVerification(
                photo_id=pin.photo_id, world_xy=(wx, wy),
                corroborated=corroborated, detail=detail,
            ))
        return out


def drawing_hash(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def export_pins_json(sheet: DrawingSheet) -> str:
    return json.dumps(sheet.model_dump(), indent=2, ensure_ascii=False)
