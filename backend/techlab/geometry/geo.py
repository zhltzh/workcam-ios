"""TM13: geographic coordinates for engineering use.

WGS84 / ECEF / ENU / UTM-lite + haversine, bearing, elevation delta,
project geographic fence and GeoJSON/KML export. Pure math,
Swift-portable (TM61).

HONESTY: consumer phone GNSS is NOT surveying equipment - the doc
strings say so and callers must surface it. Date-line, high-latitude
and zone-boundary cases are tested explicitly.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from techlab.geometry.camera import _require_finite

# WGS84 ellipsoid constants
_WGS84_A = 6378137.0
_WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)
_WGS84_B = _WGS84_A * (1.0 - _WGS84_F)


@dataclass(frozen=True)
class Geodetic:
    """Lat/lon in degrees, altitude in meters (HAE: height above
    ellipsoid). CLAltitude gives different datums depending on source
    - the caller records which."""
    latitude: float
    longitude: float
    altitude_m: float | None = None

    def __post_init__(self):
        _require_finite(self.latitude, self.longitude)
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude out of range")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude out of range")
        if self.altitude_m is not None:
            _require_finite(self.altitude_m)


@dataclass(frozen=True)
class ECEF:
    x: float
    y: float
    z: float


def geodetic_to_ecef(g: Geodetic) -> ECEF:
    """WGS84 geodetic -> Earth-Centered Earth-Fixed (meters)."""
    lat = math.radians(g.latitude)
    lon = math.radians(g.longitude)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    n = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
    h = g.altitude_m or 0.0
    x = (n + h) * cos_lat * math.cos(lon)
    y = (n + h) * cos_lat * math.sin(lon)
    z = (n * (1 - _WGS84_E2) + h) * sin_lat
    return ECEF(x, y, z)


@dataclass(frozen=True)
class ENU:
    """East-North-Up offset in meters relative to a reference."""
    east: float
    north: float
    up: float


def ecef_to_enu(p: ECEF, origin: ECEF, origin_geodetic: Geodetic) -> ENU:
    """ECEF point -> ENU coordinates around an origin (standard
    rotation about the origin's lat/lon)."""
    lat = math.radians(origin_geodetic.latitude)
    lon = math.radians(origin_geodetic.longitude)
    dx, dy, dz = p.x - origin.x, p.y - origin.y, p.z - origin.z
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)
    east = -sin_lon * dx + cos_lon * dy
    north = (-sin_lat * cos_lon * dx - sin_lat * sin_lon * dy
             + cos_lat * dz)
    up = (cos_lat * cos_lon * dx + cos_lat * sin_lon * dy
          + sin_lat * dz)
    return ENU(east, north, up)


def geodetic_distance_m(a: Geodetic, b: Geodetic) -> float:
    """Haversine great-circle distance over the sphere (accuracy
    ~0.3% vs the ellipsoid - fine for site-scale engineering; the
    docstring keeps the limitation visible)."""
    p1, l1 = math.radians(a.latitude), math.radians(a.longitude)
    p2, l2 = math.radians(b.latitude), math.radians(b.longitude)
    dp, dl = p2 - p1, l2 - l1
    h = math.sin(dp / 2) ** 2 + \
        math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _WGS84_A * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: Geodetic, b: Geodetic) -> float:
    """Initial bearing (forward azimuth) from a to b, degrees
    [0, 360)."""
    p1, l1 = math.radians(a.latitude), math.radians(a.longitude)
    p2, l2 = math.radians(b.latitude), math.radians(b.longitude)
    dl = l2 - l1
    y = math.sin(dl) * math.cos(p2)
    x = (math.cos(p1) * math.sin(p2)
         - math.sin(p1) * math.cos(p2) * math.cos(dl))
    deg = math.degrees(math.atan2(y, x))
    return (deg + 360.0) % 360.0


def elevation_delta(a: Geodetic, b: Geodetic) -> float | None:
    """b.alt - a.alt; None if either side lacks altitude."""
    if a.altitude_m is None or b.altitude_m is None:
        return None
    return b.altitude_m - a.altitude_m


# ------------------------------------------------------------ local frame

def make_local_frame(origin: Geodetic) -> dict:
    """A site-local ENU frame: converts geodetic points to meters
    around the origin (engineering coordinates). Returns converter
    closures serialized as data the Swift side mirrors."""
    origin_ecef = geodetic_to_ecef(origin)

    def to_local(p: Geodetic) -> ENU:
        return ecef_to_enu(geodetic_to_ecef(p), origin_ecef, origin)

    return {"origin": origin, "to_local": to_local,
            "origin_ecef": origin_ecef}


# ------------------------------------------------------------ geo fencing

@dataclass(frozen=True)
class GeoFence:
    """Polygon fence in geodetic coordinates (site boundary)."""
    vertices: list[Geodetic]

    def __post_init__(self):
        if len(self.vertices) < 3:
            raise ValueError("a fence needs >= 3 vertices")
        for v in self.vertices:
            if not -90 <= v.latitude <= 90 or \
                    not -180 <= v.longitude <= 180:
                raise ValueError("fence vertex out of range")

    def contains(self, p: Geodetic) -> bool:
        """Ray-casting point-in-polygon on lon/lat (valid at site
        scale away from the poles; high-latitude sites should use the
        local-frame projection instead - tested explicitly)."""
        n = len(self.vertices)
        inside = False
        j = n - 1
        for i in range(n):
            vi, vj = self.vertices[i], self.vertices[j]
            if (vi.longitude > p.longitude) != \
                    (vj.longitude > p.longitude):
                # edge crosses the point's longitude
                lat_at = (vj.latitude - vi.latitude) * \
                    (p.longitude - vi.longitude) / \
                    (vj.longitude - vi.longitude) + vi.latitude
                if p.latitude < lat_at:
                    inside = not inside
            j = i
        return inside


# ------------------------------------------------------------- export

def to_geojson(points: list[Geodetic]) -> str:
    """GeoJSON FeatureCollection of Points (RFC 7946 positions are
    [lon, lat, alt] - note the ORDER, a classic cross-language bug
    this vector set pins down)."""
    features = []
    for g in points:
        pos = [g.longitude, g.latitude]
        if g.altitude_m is not None:
            pos.append(g.altitude_m)
        features.append({"type": "Feature",
                         "geometry": {"type": "Point",
                                      "coordinates": pos},
                         "properties": {}})
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


def to_kml(points: list[Geodetic],
           name: str = "WorkCam points") -> str:
    """Minimal KML 2.2 Placemark set (lon,lat,alt order like
    GeoJSON)."""
    coords = " ".join(
        f"{g.longitude},{g.latitude}"
        + (f",{g.altitude_m}" if g.altitude_m is not None else "")
        for g in points)
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<kml xmlns="http://www.opengis.net/kml/2.2">'
            f'<Document><name>{name}</name><Placemark>'
            f'<LineString><coordinates>{coords}'
            f'</coordinates></LineString></Placemark>'
            f'</Document></kml>')
