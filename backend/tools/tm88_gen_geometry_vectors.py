#!/usr/bin/env python
"""TM88: golden test-vector generator for the geometry stack.

Emits ONE versioned JSON consumed by BOTH sides of the cross-language
contract (Python tests here, Swift tests in the TM61 package on Mac):
every case carries exact inputs, the expected outputs computed by
the Python reference implementation, and a numeric tolerance.

Swift status: CROSS_PLATFORM_PENDING (no Windows toolchain - Mac runs
this vector file; Windows certifies the PYTHON side only).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from techlab.geometry import area as ga
from techlab.geometry import camera as gc
from techlab.geometry import geo as gg
from techlab.geometry import measurement as gm
from techlab.geometry import plane as gp
from techlab.geometry import quality as gq

VERSION = "geometry-vectors/1.0"
OUT = (Path(__file__).resolve().parents[2]
       / "test-vectors" / "geometry" / "geometry-golden-vectors.json")


def case(name, category, inputs, expected, tol=1e-9, note=None):
    out = {"name": name, "category": category, "inputs": inputs,
           "expected": expected, "tolerance": tol}
    if note:
        out["note"] = note
    return out


def main() -> None:
    cases: list[dict] = []

    # ---------------- TM15: backprojection round-trips ----------
    cam = gc.CameraIntrinsics(fx=1500.0, fy=1400.0, cx=640.0,
                              cy=480.0, width=1280, height=960)
    for (u, v, d) in ((0.0, 0.0, 3.0), (640.0, 480.0, 1.0),
                      (1279.0, 959.0, 2.5), (100.5, 900.25, 0.75),
                      (320.0, 240.0, 12.0)):
        x, y, z = cam.backproject(u, v, d)
        u2, v2 = cam.project(x, y, z)
        cases.append(case(
            f"backproject-{u}-{v}-{d}", "backprojection",
            {"fx": 1500.0, "fy": 1400.0, "cx": 640.0, "cy": 480.0,
             "width": 1280, "height": 960, "u": u, "v": v,
             "depth": d},
            {"x": x, "y": y, "z": z, "u_roundtrip": u2,
             "v_roundtrip": v2}, tol=1e-6))
    # orientation maps (corner probes)
    w, h = 1920, 1080
    for o, pt in (("up", (100.0, 200.0)), ("down", (1919.0, 0.0)),
                  ("right", (1919.0, 0.0)), ("left", (0.0, 1079.0))):
        ou, ov = gc.oriented_pixel_to_upright(*pt, w, h, o)
        uw, uh = gc.upright_dimensions(w, h, o)
        cases.append(case(
            f"orientation-{o}", "orientation",
            {"width": w, "height": h, "orientation": o,
             "u": pt[0], "v": pt[1]},
            {"u": ou, "v": ov, "upright_width": uw,
             "upright_height": uh}))
    # depth->color mapping
    du, dv = gc.depth_pixel_to_color_pixel(128.0, 64.0, 256, 192,
                                           1920, 1080)
    cases.append(case("depth-to-color-256x192-to-1920x1080",
                      "depth_mapping",
                      {"du": 128.0, "dv": 64.0, "depth_width": 256,
                       "depth_height": 192, "color_width": 1920,
                       "color_height": 1080},
                      {"u": du, "v": dv}, tol=1e-9))

    # ---------------- TM16/TM20: distances & angles --------------
    cases.append(case("distance-345", "distance",
                      {"a": [0, 0, 0], "b": [3, 4, 0]},
                      {"meters": gm.distance_3d((0, 0, 0),
                                               (3, 4, 0))}))
    cases.append(case("horizontal-vertical-split", "distance",
                      {"a": [0, 0, 10], "b": [30, 40, 13]},
                      {"horizontal": gm.horizontal_distance(
                          (0, 0, 10), (30, 40, 13)),
                       "vertical": gm.vertical_distance(
                          (0, 0, 10), (30, 40, 13))}))
    pl = [(0, 0, 0), (3, 0, 0), (3, 4, 0), (3, 4, 12)]
    cases.append(case("polyline-zigzag", "distance",
                      {"points": [list(p) for p in pl]},
                      {"length": gm.polyline_length(pl),
                       "horizontal_length":
                           gm.polyline_length(pl,
                                              horizontal_only=True)}))
    cases.append(case("angle-90", "angle",
                      {"a": [1, 0, 0], "b": [0, 1, 0]},
                      {"degrees":
                       gm.angle_between_vectors((1, 0, 0),
                                                (0, 1, 0))}))
    s = gm.slope_from_vertical_delta(4.0, 1.0)
    cases.append(case("slope-1-in-4", "slope",
                      {"horizontal": 4.0, "vertical": 1.0},
                      {"percent": s["percent"],
                       "degrees": s["degrees"]}, tol=1e-9))
    cases.append(case("plumb-perfect", "slope",
                      {"normal": [0, 0, 1]},
                      {"deviation_deg":
                       gm.deviation_from_vertical((0, 0, 1))}))
    cases.append(case("meters-to-feet", "units",
                      {"meters": 3.048},
                      {"feet": gm.meters_to_feet(3.048)}, tol=1e-12))

    # ---------------- TM17: plane fits ----------------------------
    exact_pts = [[0, 0, 5], [1, 0, 5], [0, 1, 5], [1, 1, 5],
                 [0.5, 0.5, 5]]
    fit = gp.fit_plane_least_squares([tuple(p) for p in exact_pts])
    cases.append(case("plane-horizontal-exact", "plane",
                      {"points": exact_pts},
                      {"normal": list(fit.normal), "d": fit.d,
                       "residual_rms": fit.residual_rms},
                      tol=1e-12))
    tilt_pts = [[0, 0, 0], [1, 0, 2], [0, 1, 3], [1, 1, 5],
                [2, 2, 10]]
    fit2 = gp.fit_plane_least_squares([tuple(p) for p in tilt_pts])
    cases.append(case("plane-z-eq-2x-plus-3y", "plane",
                      {"points": tilt_pts},
                      {"normal": list(fit2.normal), "d": fit2.d},
                      tol=1e-9,
                      note="plane z=2x+3y; normal is +-(-2,-3,1)/n"
                           " with z-up convention"))
    plane_pts = [[x * 0.5, y * 0.5, 3.0] for x in range(4)
                 for y in range(5)]
    outliers = [[1.0, 1.0, 4.0], [2.0, 0.5, 2.0], [0.5, 2.0, 4.5],
                [1.5, 1.5, 5.0], [0.0, 0.5, 1.0]]
    rfit = gp.fit_plane_ransac(
        [tuple(p) for p in plane_pts + outliers],
        threshold=0.05, iterations=300, seed=42)
    cases.append(case("ransac-outlier-rejection", "plane",
                      {"plane_points": plane_pts,
                       "outliers": outliers, "threshold": 0.05,
                       "iterations": 300, "seed": 42},
                      {"normal": list(rfit.normal),
                       "inlier_ratio": rfit.inlier_ratio,
                       "used_points": rfit.used_points}, tol=1e-9))
    cases.append(case("classify-wall", "plane_class",
                      {"points": [[0, 0, 0], [0, 1, 0], [0, 0, 3],
                                  [0, 1, 3]], "up": [0, 0, 1]},
                      {"label": gp.classify_surface(
                          gp.fit_plane_least_squares(
                              [(0, 0, 0), (0, 1, 0), (0, 0, 3),
                               (0, 1, 3)]))}))

    # ---------------- TM18/TM19: areas & volumes -------------------
    l_shape = [[0, 0], [6, 0], [6, 3], [3, 3], [3, 6], [0, 6]]
    cases.append(case("area-l-shape", "area",
                      {"vertices": l_shape},
                      {"abs_area":
                       abs(ga.polygon_area_2d(
                           [tuple(p) for p in l_shape]))}))
    para = [(0, 0, 0), (2, 0, 4), (2, 4, 4), (0, 4, 0)]
    pfit = gp.fit_plane_least_squares(para)
    cases.append(case("area-parallelogram-tilted", "area",
                      {"points": [list(p) for p in para],
                       "plane_normal": list(pfit.normal),
                       "plane_d": pfit.d},
                      {"area_sqm": ga.polygon_area_3d(para, pfit)},
                      tol=1e-9))
    cases.append(case("volume-extrusion", "volume",
                      {"base_area_sqm": 10.0, "height_m": 3.0},
                      {"cubic_meters": 30.0}))
    bb = ga.bounding_box_volume([(0, 0, 0), (2, 0, 0), (0, 3, 0),
                                (0, 0, 4)])
    cases.append(case("volume-bounding-box", "volume",
                      {"points": [[0, 0, 0], [2, 0, 0], [0, 3, 0],
                                  [0, 0, 4]]},
                      {"cubic_meters": bb.cubic_meters}))

    # ---------------- TM22: quality & propagation -------------------
    cases.append(case("sigma-distance-combine", "propagation",
                      {"sigma_a": 0.03, "sigma_b": 0.04},
                      {"sigma": gq.propagate_distance_sigma(
                          0.03, 0.04)}))
    s_area = gq.propagate_area_sigma([0.02, 0.03], [4.0, 3.0])
    cases.append(case("sigma-area-rectangle", "propagation",
                      {"w": 4.0, "h": 3.0, "sigma_w": 0.02,
                       "sigma_h": 0.03},
                      {"sigma_sqm": s_area}))
    rep = gq.repeatability_std([10.00, 10.02, 9.98, 10.01])
    cases.append(case("repeatability-four-shots", "propagation",
                      {"values": [10.00, 10.02, 9.98, 10.01]},
                      {"std": rep}, tol=1e-6))

    # ---------------- TM13: geodesy ---------------------------------
    a = gg.Geodetic(31.2304, 121.4737)
    b = gg.Geodetic(31.2397, 121.4900)
    cases.append(case("haversine-shanghai-short-hop", "geo",
                      {"a": [a.latitude, a.longitude],
                       "b": [b.latitude, b.longitude]},
                      {"meters": gg.geodetic_distance_m(a, b)},
                      tol=1e-6))
    dl_a = gg.Geodetic(0.0, 179.999)
    dl_b = gg.Geodetic(0.0, -179.999)
    cases.append(case("antimeridian-short-arc", "geo",
                      {"a": [0.0, 179.999], "b": [0.0, -179.999]},
                      {"meters": gg.geodetic_distance_m(dl_a, dl_b)},
                      tol=1e-6,
                      note="must be ~222m, never the long way"))
    ecef = gg.geodetic_to_ecef(gg.Geodetic(31.2304, 121.4737, 10.0))
    cases.append(case("ecef-shanghai", "geo",
                      {"lat": 31.2304, "lon": 121.4737, "alt": 10.0},
                      {"x": ecef.x, "y": ecef.y, "z": ecef.z},
                      tol=1e-3))
    origin = gg.Geodetic(31.0, 121.0, 0.0)
    frame = gg.make_local_frame(origin)
    enu = frame["to_local"](gg.Geodetic(31.0, 121.001, 0.0))
    cases.append(case("enu-east-001-deg-lon", "geo",
                      {"origin": [31.0, 121.0],
                       "point": [31.0, 121.001]},
                      {"east": enu.east, "north": enu.north,
                       "up": enu.up}, tol=1e-3))
    fence = gg.GeoFence(vertices=[
        gg.Geodetic(31.00, 121.00), gg.Geodetic(31.00, 121.01),
        gg.Geodetic(31.01, 121.01), gg.Geodetic(31.01, 121.00)])
    cases.append(case("geofence-inside", "geo_fence",
                      {"vertices": [[31.00, 121.00], [31.00, 121.01],
                                    [31.01, 121.01], [31.01, 121.00]],
                       "point": [31.005, 121.005]},
                      {"contains": True}))
    cases.append(case("bearing-north", "geo",
                      {"a": [31.0, 121.0], "b": [32.0, 121.0]},
                      {"degrees": gg.bearing_deg(
                          gg.Geodetic(31.0, 121.0),
                          gg.Geodetic(32.0, 121.0))}, tol=1e-6))

    doc = {"version": VERSION,
           "generator": "backend/tools/tm88_gen_geometry_vectors.py",
           "reference": "Python 3.12 implementation at the same SHA",
           "swift_status": "CROSS_PLATFORM_PENDING - Mac executes "
                           "the port against THIS file; Windows "
                           "certifies the Python side only",
           "cases": cases}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    print(f"{len(cases)} cases -> {OUT}")


if __name__ == "__main__":
    main()
