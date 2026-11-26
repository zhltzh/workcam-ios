"""TM88: golden-vector consumer test (Python side).

Re-computes every case in geometry-golden-vectors.json through the
live implementation and compares against the stored expected
outputs. This is the Python half of the cross-language contract; the
Swift half runs against the SAME file on Mac (CROSS_PLATFORM_PENDING).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.geometry import area as ga  # noqa: E402
from techlab.geometry import camera as gc  # noqa: E402
from techlab.geometry import geo as gg  # noqa: E402
from techlab.geometry import measurement as gm  # noqa: E402
from techlab.geometry import plane as gp  # noqa: E402

VEC = (Path(__file__).resolve().parents[3] / "test-vectors"
       / "geometry" / "geometry-golden-vectors.json")


def _load():
    return json.loads(VEC.read_text(encoding="utf-8"))


def test_vector_file_exists_and_versioned():
    doc = _load()
    assert doc["version"] == "geometry-vectors/1.0"
    assert "CROSS_PLATFORM_PENDING" in doc["swift_status"]
    assert len(doc["cases"]) >= 30


@pytest.mark.parametrize("case", _load()["cases"],
                         ids=lambda c: c["name"])
def test_case(case: dict):
    i = case["inputs"]
    e = case["expected"]
    tol = case.get("tolerance", 1e-9)
    cat = case["category"]

    def close(a, b, key):
        assert a == pytest.approx(b, abs=tol, rel=tol), \
            f"{case['name']}.{key}: {a} vs {b}"

    if cat == "backprojection":
        cam = gc.CameraIntrinsics(i["fx"], i["fy"], i["cx"], i["cy"],
                                  i["width"], i["height"])
        x, y, z = cam.backproject(i["u"], i["v"], i["depth"])
        u2, v2 = cam.project(x, y, z)
        close(x, e["x"], "x"); close(y, e["y"], "y")
        close(z, e["z"], "z")
        close(u2, e["u_roundtrip"], "u_roundtrip")
        close(v2, e["v_roundtrip"], "v_roundtrip")
    elif cat == "orientation":
        ou, ov = gc.oriented_pixel_to_upright(i["u"], i["v"],
                                             i["width"], i["height"],
                                             i["orientation"])
        close(ou, e["u"], "u"); close(ov, e["v"], "v")
    elif cat == "depth_mapping":
        u, v = gc.depth_pixel_to_color_pixel(
            i["du"], i["dv"], i["depth_width"], i["depth_height"],
            i["color_width"], i["color_height"])
        close(u, e["u"], "u"); close(v, e["v"], "v")
    elif cat == "distance":
        if "points" in i:
            pts = [tuple(p) for p in i["points"]]
            close(gm.polyline_length(pts), e["length"], "length")
            close(gm.polyline_length(pts, horizontal_only=True),
                  e["horizontal_length"], "horizontal_length")
        else:
            pts = (tuple(i["a"]), tuple(i["b"]))
            if "meters" in e:
                close(gm.distance_3d(*pts), e["meters"], "meters")
            if "horizontal" in e:
                close(gm.horizontal_distance(*pts),
                      e["horizontal"], "horizontal")
                close(gm.vertical_distance(*pts),
                      e["vertical"], "vertical")
    elif cat == "angle":
        close(gm.angle_between_vectors(tuple(i["a"]),
                                       tuple(i["b"])),
              e["degrees"], "degrees")
    elif cat == "slope":
        if "normal" in i:
            close(gm.deviation_from_vertical(tuple(i["normal"])),
                  e["deviation_deg"], "deviation_deg")
        else:
            s = gm.slope_from_vertical_delta(i["horizontal"],
                                             i["vertical"])
            close(s["percent"], e["percent"], "percent")
            close(s["degrees"], e["degrees"], "degrees")
    elif cat == "units":
        close(gm.meters_to_feet(i["meters"]), e["feet"], "feet")
    elif cat == "plane":
        if "outliers" in i:
            fit = gp.fit_plane_ransac(
                [tuple(p) for p in i["plane_points"] + i["outliers"]],
                threshold=i["threshold"], iterations=i["iterations"],
                seed=i["seed"])
            close(fit.inlier_ratio, e["inlier_ratio"], "inlier_ratio")
            assert fit.used_points == e["used_points"]
        else:
            pts = [tuple(p) for p in i["points"]]
            fit = gp.fit_plane_least_squares(pts)
            close(fit.normal[2], e["normal"][2], "normal.z")
            if "d" in e:
                close(fit.d, e["d"], "d")
    elif cat == "plane_class":
        fit = gp.fit_plane_least_squares(
            [tuple(p) for p in i["points"]])
        label = gp.classify_surface(fit, tuple(i["up"]))
        assert label == e["label"]
    elif cat == "area":
        if "vertices" in i:      # 2D shoelace
            val = abs(ga.polygon_area_2d(
                [tuple(p) for p in i["vertices"]]))
            close(val, e["abs_area"], "abs_area")
        else:                    # 3D on the fitted plane
            pts = [tuple(p) for p in i["points"]]
            fit = gp.PlaneFit(normal=tuple(i["plane_normal"]),
                              d=i["plane_d"], residual_rms=0.0,
                              max_residual=0.0, inlier_ratio=1.0,
                              method="vector", used_points=len(pts))
            close(ga.polygon_area_3d(pts, fit), e["area_sqm"],
                  "area_sqm")
    elif cat == "volume":
        if "base_area_sqm" in i:
            v = ga.extrusion_volume(i["base_area_sqm"],
                                    i["height_m"])
        else:
            v = ga.bounding_box_volume(
                [tuple(p) for p in i["points"]])
        close(v.cubic_meters, e["cubic_meters"], "cubic_meters")
    elif cat == "propagation":
        from techlab.geometry import quality as gq
        if "sigma_a" in i:
            close(gq.propagate_distance_sigma(i["sigma_a"],
                                              i["sigma_b"]),
                  e["sigma"], "sigma")
        elif "w" in i:
            close(gq.propagate_area_sigma(
                [i["sigma_w"], i["sigma_h"]], [i["w"], i["h"]]),
                e["sigma_sqm"], "sigma_sqm")
        else:
            close(gq.repeatability_std(i["values"]), e["std"],
                  "std")
    elif cat in ("geo", "geo_fence"):
        if cat == "geo_fence" or "vertices" in i:      # fence
            fence = gg.GeoFence(vertices=[
                gg.Geodetic(*v) for v in i["vertices"]])
            assert fence.contains(
                gg.Geodetic(*i["point"])) is e["contains"]
        elif "a" in i and "b" in i:
            a, b = gg.Geodetic(*i["a"]), gg.Geodetic(*i["b"])
            if "degrees" in e:
                close(gg.bearing_deg(a, b), e["degrees"], "degrees")
            else:
                close(gg.geodetic_distance_m(a, b), e["meters"],
                      "meters")
        elif "lat" in i:
            ecef = gg.geodetic_to_ecef(
                gg.Geodetic(i["lat"], i["lon"], i["alt"]))
            close(ecef.x, e["x"], "x"); close(ecef.y, e["y"], "y")
            close(ecef.z, e["z"], "z")
        else:                    # ENU
            frame = gg.make_local_frame(
                gg.Geodetic(*i["origin"]))
            enu = frame["to_local"](gg.Geodetic(*i["point"]))
            close(enu.east, e["east"], "east")
            close(enu.north, e["north"], "north")
            close(enu.up, e["up"], "up")
    else:
        pytest.fail(f"unknown vector category {cat!r} - update the "
                    f"consumer for every category you emit")
