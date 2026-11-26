"""TM15/TM16/TM17/TM18/TM19/TM20/TM22/TM13: geometry core tests.

Every numeric case doubles as a TM88 shared vector (the Swift port
must reproduce these exact values within tolerance).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.geometry import area as ga  # noqa: E402
from techlab.geometry import camera as gc  # noqa: E402
from techlab.geometry import geo as gg  # noqa: E402
from techlab.geometry import measurement as gm  # noqa: E402
from techlab.geometry import plane as gp  # noqa: E402
from techlab.geometry import quality as gq  # noqa: E402

TOL = 1e-9


# ------------------------------------------------------------- TM15

class TestIntrinsics:
    def test_backproject_principal_point(self):
        cam = gc.CameraIntrinsics(fx=1000, fy=1000, cx=960, cy=540,
                                   width=1920, height=1080)
        x, y, z = cam.backproject(960, 540, 2.0)
        assert abs(x) < TOL and abs(y) < TOL and z == pytest.approx(-2.0)

    def test_backproject_project_roundtrip(self):
        cam = gc.CameraIntrinsics(fx=1500, fy=1400, cx=640, cy=480,
                                  width=1280, height=960)
        for (u, v, d) in ((0, 0, 3.0), (1279, 959, 1.5),
                          (640, 480, 10.0), (100, 900, 0.7)):
            p = cam.backproject(u, v, d)
            u2, v2 = cam.project(*p)
            assert u2 == pytest.approx(u, abs=1e-6)
            assert v2 == pytest.approx(v, abs=1e-6)

    def test_v_axis_flips(self):
        """Image v grows DOWN, camera y grows UP - a v BELOW the
        center must give a NEGATIVE y (looking down at the floor)."""
        cam = gc.CameraIntrinsics(fx=100, fy=100, cx=50, cy=50,
                                  width=100, height=100)
        _, y, _ = cam.backproject(50, 75, 1.0)      # v = cy + 25
        assert y < 0

    def test_negative_depth_rejected(self):
        cam = gc.CameraIntrinsics(fx=1, fy=1, cx=1, cy=1, width=10,
                                  height=10)
        with pytest.raises(ValueError):
            cam.backproject(1, 1, 0.0)
        with pytest.raises(ValueError):
            cam.backproject(1, 1, -3.0)

    def test_normalized_coordinates(self):
        cam = gc.CameraIntrinsics(fx=1, fy=1, cx=0, cy=0, width=100,
                                  height=100)
        assert cam.pixel_to_normalized(0, 0) == (-1.0, 1.0)
        assert cam.pixel_to_normalized(50, 50) == (0.0, 0.0)
        assert cam.pixel_to_normalized(100, 100) == (1.0, -1.0)

    def test_nonfinite_rejected(self):
        cam = gc.CameraIntrinsics(fx=1, fy=1, cx=1, cy=1, width=10,
                                  height=10)
        with pytest.raises(ValueError):
            cam.backproject(float("nan"), 1, 1.0)
        with pytest.raises(ValueError):
            gc.CameraIntrinsics(fx=float("inf"), fy=1, cx=0, cy=0,
                                width=10, height=10)


class TestOrientation:
    def test_all_four_rotations_roundtrip(self):
        w, h = 1920, 1080
        for o, (uw, uh) in (("up", (w, h)), ("down", (w, h)),
                            ("left", (h, w)), ("right", (h, w))):
            assert gc.upright_dimensions(w, h, o) == (uw, uh)
        # corners map to corners
        assert gc.oriented_pixel_to_upright(0, 0, w, h, "up") == (0, 0)
        assert gc.oriented_pixel_to_upright(w - 1, 0, w, h,
                                            "down") == (0, h - 1)
        # right (EXIF 6, 90 CCW): raw top-right -> upright top-left
        assert gc.oriented_pixel_to_upright(w - 1, 0, w, h,
                                            "right") == (0, 0)
        # left (EXIF 8, 90 CW): raw top-left -> upright top-right
        assert gc.oriented_pixel_to_upright(0, 0, w, h,
                                            "left") == (h - 1, 0)

    def test_avfoundation_aliases(self):
        w, h = 1920, 1080
        for alias, exif in (("portrait", "right"),
                            ("portraitUpsideDown", "left"),
                            ("landscapeLeft", "up"),
                            ("landscapeRight", "down")):
            for (u, v) in ((3, 7), (100, 200)):
                assert gc.oriented_pixel_to_upright(
                    u, v, w, h, alias) == \
                    gc.oriented_pixel_to_upright(u, v, w, h, exif)

    def test_unknown_orientation_rejected(self):
        with pytest.raises(ValueError):
            gc.oriented_pixel_to_upright(0, 0, 10, 10, "diagonal")


class TestDepthMapping:
    def test_depth_to_color_pixel_center_based(self):
        # depth 256x192 -> color 1920x1080 (7.5/5.625 scale)
        u, v = gc.depth_pixel_to_color_pixel(0, 0, 256, 192,
                                             1920, 1080)
        assert u == pytest.approx(-0.5 + 3.75, abs=1e-9)
        assert v == pytest.approx(-0.5 + 2.8125, abs=1e-9)

    def test_sample_depth_bounds_are_nan_not_clamped(self):
        depth = [[1.0, 2.0], [3.0, float("nan")]]
        assert gc.sample_depth_nearest(depth, 0, 0) == 1.0
        assert math.isnan(gc.sample_depth_nearest(depth, 5, 5))
        assert math.isnan(gc.sample_depth_nearest(depth, 1, 1))
        assert math.isnan(gc.sample_depth_nearest(depth, 0, 5))

    def test_scale_pixel_roundtrip(self):
        u2, v2 = gc.scale_pixel(10, 20, 100, 200, 200, 400)
        assert u2 == pytest.approx(20.5, abs=1e-9)
        assert v2 == pytest.approx(40.5, abs=1e-9)


class TestCropScale:
    def test_crop_roundtrip(self):
        crop = gc.CropRect(x=0.25, y=0.25, w=0.5, h=0.5)
        w, h = 1000, 800
        cu, cv = crop.original_pixel_to_cropped(500, 400, w, h)
        u, v = crop.cropped_pixel_to_original(cu, cv, w, h)
        assert u == pytest.approx(500, abs=1e-6)
        assert v == pytest.approx(400, abs=1e-6)

    def test_crop_outside_rejected(self):
        crop = gc.CropRect(x=0.5, y=0.5, w=0.25, h=0.25)
        with pytest.raises(ValueError):
            crop.original_pixel_to_cropped(100, 100, 1000, 800)
        with pytest.raises(ValueError):
            gc.CropRect(x=0.9, y=0.0, w=0.2, h=0.5)   # > 1.0


# ------------------------------------------------------------- TM16/TM20

class TestDistances:
    def test_distance_3d(self):
        assert gm.distance_3d((0, 0, 0), (3, 4, 0)) == 5.0
        assert gm.distance_3d((1, 1, 1), (1, 1, 1)) == 0.0

    def test_horizontal_and_vertical(self):
        a, b = (0, 0, 10), (30, 40, 13)
        assert gm.horizontal_distance(a, b) == 50.0
        assert gm.vertical_distance(a, b) == 3.0

    def test_polyline(self):
        pts = [(0, 0, 0), (3, 0, 0), (3, 4, 0), (3, 4, 12)]
        assert gm.polyline_length(pts) == 3 + 4 + 12
        assert gm.polyline_length(pts, horizontal_only=True) == 7.0
        with pytest.raises(ValueError):
            gm.polyline_length([])

    def test_angle_between_vectors(self):
        assert gm.angle_between_vectors((1, 0, 0), (0, 1, 0)) == \
            pytest.approx(90.0)
        assert gm.angle_between_vectors((1, 0, 0), (1, 0, 0)) == \
            pytest.approx(0.0)
        assert gm.angle_between_vectors((1, 0, 0), (-1, 0, 0)) == \
            pytest.approx(180.0)
        with pytest.raises(ValueError):
            gm.angle_between_vectors((0, 0, 0), (1, 0, 0))

    def test_angle_between_planes(self):
        # 30 deg between normals -> 30 deg between planes (acute)
        n1 = (0, 0, 1)
        n2 = (math.sin(math.radians(30)), 0, math.cos(math.radians(30)))
        assert gm.angle_between_planes(n1, n2) == pytest.approx(30.0)
        # 150 deg between normals -> still 30 deg between planes
        n3 = (-math.sin(math.radians(30)), 0,
              math.cos(math.radians(150 - 90)))
        assert gm.angle_between_planes(n1, n3) <= 90.0

    def test_slope(self):
        s = gm.slope_from_vertical_delta(4.0, 1.0)
        assert s["percent"] == 25.0
        assert s["degrees"] == pytest.approx(
            math.degrees(math.atan(0.25)))
        with pytest.raises(ValueError):
            gm.slope_from_vertical_delta(0.0, 1.0)   # wall

    def test_plumb_and_level(self):
        assert gm.deviation_from_vertical((0, 0, 1)) == \
            pytest.approx(0.0)
        assert gm.deviation_from_vertical((1, 0, 0)) == \
            pytest.approx(90.0)
        assert gm.deviation_from_horizontal((0, 0, 1)) == \
            pytest.approx(90.0)   # a floor normal is vertical

    def test_units(self):
        assert gm.meters_to_feet(0.3048) == pytest.approx(1.0)
        assert gm.feet_to_meters(1.0) == pytest.approx(0.3048)
        assert gm.sqm_to_sqft(1.0) == pytest.approx(10.7639,
                                                    rel=1e-4)


# ------------------------------------------------------------- TM17

class TestPlaneFitting:
    def test_exact_horizontal_plane(self):
        pts = [(0, 0, 5), (1, 0, 5), (0, 1, 5), (1, 1, 5), (.5, .5, 5)]
        fit = gp.fit_plane_least_squares(pts)
        assert fit.normal[2] == pytest.approx(1.0)
        assert fit.d == pytest.approx(-5.0)
        assert fit.residual_rms < 1e-12

    def test_tilted_plane(self):
        # z = 2x + 3y  ->  normal ~ (-2,-3,1)/|.|, d = 0
        pts = [(0, 0, 0), (1, 0, 2), (0, 1, 3), (1, 1, 5), (2, 2, 10)]
        fit = gp.fit_plane_least_squares(pts)
        n = fit.normal
        # direction match (sign-free): normal parallel to (-2,-3,1)
        cosv = abs((n[0] * -2 + n[1] * -3 + n[2] * 1)
                   / (math.hypot(*n) * math.hypot(-2, -3, 1)))
        assert cosv > 1 - 1e-9
        assert fit.residual_rms < 1e-9

    def test_too_few_points_rejected(self):
        with pytest.raises(ValueError):
            gp.fit_plane_least_squares([(0, 0, 0), (1, 1, 1)])

    def test_ransac_rejects_outliers(self):
        # 20 points on a plane + 5 outliers 1m away
        plane_pts = [(x * 0.5, y * 0.5, 3.0)
                     for x in range(4) for y in range(5)]
        outliers = [(1.0, 1.0, 4.0), (2.0, 0.5, 2.0),
                    (0.5, 2.0, 4.5), (1.5, 1.5, 5.0),
                    (0.0, 0.5, 1.0)]
        fit = gp.fit_plane_ransac(plane_pts + outliers,
                                  threshold=0.05, iterations=300,
                                  seed=42)
        assert fit.normal[2] == pytest.approx(1.0)
        assert fit.inlier_ratio == pytest.approx(20 / 25)
        # an LSQ fit over everything would be dragged off the plane
        lsq = gp.fit_plane_least_squares(plane_pts + outliers)
        assert lsq.max_residual > 0.1

    def test_classification(self):
        pts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
        floor = gp.fit_plane_least_squares(pts)
        assert gp.classify_surface(floor) == "horizontal_candidate"
        # z-up convention cannot split floor from ceiling - documented;
        # the CAMERA POSE context supplies that (caller's job)
        wall_pts = [(0, 0, 0), (0, 1, 0), (0, 0, 3), (0, 1, 3)]
        wall = gp.fit_plane_least_squares(wall_pts)
        assert gp.classify_surface(wall) == "wall_candidate"
        # a 45-degree ramp is neither
        ramp_pts = [(0, 0, 0), (1, 0, 1), (0, 1, 1), (1, 1, 2)]
        ramp = gp.fit_plane_least_squares(ramp_pts)
        assert gp.classify_surface(ramp) == "inclined_candidate"
        # an explicit -up vector flips the frame (ceiling view)
        ceil_pts = [(0, 0, 5), (1, 0, 5), (0, 1, 5), (1, 1, 5)]
        ceil = gp.fit_plane_least_squares(ceil_pts)
        assert gp.classify_surface(ceil,
                                   up_vector=(0, 0, -1)) == \
            "ceiling_candidate"


# ------------------------------------------------------------- TM18/TM19

class TestAreaVolume:
    def test_shoelace_square(self):
        assert ga.polygon_area_2d([(0, 0), (4, 0), (4, 4), (0, 4)]) \
            == pytest.approx(16.0)
        # winding order only flips the sign
        assert ga.polygon_area_2d([(0, 4), (4, 4), (4, 0), (0, 0)]) \
            == pytest.approx(-16.0)

    def test_concave_polygon(self):
        # L-shape: 6x6 square minus 3x3 top-right = 27
        pts = [(0, 0), (6, 0), (6, 3), (3, 3), (3, 6), (0, 6)]
        assert abs(ga.polygon_area_2d(pts)) == pytest.approx(27.0)

    def test_self_intersection_detected(self):
        # bowtie: (0,0)-(4,4)-(4,0)-(0,4)
        bowtie = [(0, 0), (4, 4), (4, 0), (0, 4)]
        assert ga.polygon_is_self_intersecting(bowtie) is True
        assert ga.polygon_is_self_intersecting(
            [(0, 0), (4, 0), (4, 4), (0, 4)]) is False

    def test_area_3d_on_tilted_plane(self):
        # parallelogram in the plane z = 2x with edges a=(2,0,4),
        # b=(0,4,0): on-plane area = |a x b| = sqrt(320)
        pts = [(0, 0, 0), (2, 0, 4), (2, 4, 4), (0, 4, 0)]
        fit = gp.fit_plane_least_squares(pts)
        assert ga.polygon_area_3d(pts, fit) == pytest.approx(
            math.sqrt(320.0), rel=1e-9)

    def test_perimeter(self):
        pts = [(0, 0, 0), (3, 0, 0), (3, 4, 0)]
        assert ga.perimeter_3d(pts) == pytest.approx(12.0)

    def test_volumes_with_assumptions(self):
        v = ga.extrusion_volume(base_area_sqm=10.0, height_m=3.0)
        assert v.cubic_meters == 30.0 and v.assumptions
        bb = ga.bounding_box_volume([(0, 0, 0), (2, 0, 0),
                                     (0, 3, 0), (0, 0, 4)])
        assert bb.cubic_meters == 24.0
        assert any("overestimates" in a for a in bb.assumptions)
        cyl = ga.cylinder_volume(radius_m=1.0, height_m=2.0)
        assert cyl.cubic_meters == pytest.approx(math.pi * 2)
        with pytest.raises(ValueError):
            ga.extrusion_volume(-1, 2)
        with pytest.raises(ValueError):
            ga.polygon_area_2d([(0, 0), (1, 1)])


# ------------------------------------------------------------- TM22

class TestQuality:
    def test_uncalibrated_profile_is_explicit(self):
        cal = gq.CalibrationProfile.uncalibrated()
        assert cal.calibration_version == "uncalibrated"
        assert "never a device-calibrated claim" in \
            gq.MeasurementQualityReport.honesty_note

    def test_tracking_states_downgrade(self):
        good = gq.assess_point_measurement(
            3.0, 0.02, gq.TrackingState.normal)
        bad = gq.assess_point_measurement(
            3.0, 0.02, gq.TrackingState.limited_excessive_motion)
        worst = gq.assess_point_measurement(
            3.0, 0.02, gq.TrackingState.not_available)
        assert good.quality == gq.MeasurementQuality.good
        assert bad.quality == gq.MeasurementQuality.poor
        assert worst.quality == gq.MeasurementQuality.invalid
        assert worst.confidence_interval_m is None

    def test_factors_recorded_never_hidden(self):
        r = gq.assess_point_measurement(
            2.0, 0.02, gq.TrackingState.normal,
            depth_confidence=0.3, surface_material="glass",
            illumination="low_light")
        assert len(r.factors) >= 3

    def test_error_propagation_distance(self):
        # sigma of |A-B|: 3cm and 4cm -> 5cm
        assert gq.propagate_distance_sigma(0.03, 0.04) == \
            pytest.approx(0.05)

    def test_error_propagation_area(self):
        # 4m x 3m wall, 2cm and 3cm edge sigma:
        # A=12, sigma = 12*sqrt((0.02/4)^2+(0.03/3)^2)
        s = gq.propagate_area_sigma([0.02, 0.03], [4.0, 3.0])
        expect = 12.0 * math.hypot(0.02 / 4, 0.03 / 3)
        assert s == pytest.approx(expect)

    def test_error_propagation_slope(self):
        out = gq.propagate_slope_sigma(4.0, 0.05, 1.0, 0.05)
        assert out["valid"]
        assert 0.3 < out["sigma_deg"] < 1.5
        invalid = gq.propagate_slope_sigma(0.0, 0.01, 1.0, 0.01)
        assert not invalid["valid"]

    def test_repeatability(self):
        assert gq.repeatability_std([5.0]) is None
        vals = [10.00, 10.02, 9.98, 10.01]
        assert gq.repeatability_std(vals) == pytest.approx(
            0.017078, rel=1e-3)

    def test_volume_propagation(self):
        s = gq.propagate_volume_extrusion_sigma(
            base_area=12.0, area_sigma=0.1, height=3.0,
            height_sigma=0.05)
        expect = 36.0 * math.hypot(0.1 / 12, 0.05 / 3)
        assert s == pytest.approx(expect)


# ------------------------------------------------------------- TM13

class TestGeo:
    def test_ecef_roundtrip(self):
        g = gg.Geodetic(latitude=31.2304, longitude=121.4737,
                        altitude_m=10.0)
        e = gg.geodetic_to_ecef(g)
        # |ECEF| ~ 6378137 + altitude at the equator; Shanghai less
        assert 6.37e6 < math.hypot(e.x, e.y, e.z) < 6.39e6

    def test_enu_local_frame(self):
        origin = gg.Geodetic(latitude=31.0, longitude=121.0,
                             altitude_m=0.0)
        frame = gg.make_local_frame(origin)
        east_pt = gg.Geodetic(latitude=31.0,
                             longitude=121.001, altitude_m=0.0)
        enu = frame["to_local"](east_pt)
        assert enu.east > 80 and enu.east < 100     # ~91m east
        assert abs(enu.north) < 1
        north_pt = gg.Geodetic(latitude=31.001, longitude=121.0,
                               altitude_m=0.0)
        enu2 = frame["to_local"](north_pt)
        assert enu2.north > 100 and enu2.north < 120   # ~111m

    def test_haversine_known_distance(self):
        a = gg.Geodetic(latitude=31.2304, longitude=121.4737)
        b = gg.Geodetic(latitude=31.2397, longitude=121.4900)
        d = gg.geodetic_distance_m(a, b)
        assert 1000 < d < 2100

    def test_bearing(self):
        north = gg.Geodetic(latitude=32.0, longitude=121.0)
        south = gg.Geodetic(latitude=31.0, longitude=121.0)
        assert gg.bearing_deg(south, north) == pytest.approx(0.0,
                                                             abs=1e-6)
        assert gg.bearing_deg(north, south) == pytest.approx(180.0,
                                                             abs=1e-6)
        east = gg.Geodetic(latitude=31.0, longitude=122.0)
        assert gg.bearing_deg(south, east) == pytest.approx(90.0,
                                                            abs=0.5)

    def test_antimeridian(self):
        """Distance across the dateline must use the SHORT arc."""
        a = gg.Geodetic(latitude=0.0, longitude=179.999)
        b = gg.Geodetic(latitude=0.0, longitude=-179.999)
        d = gg.geodetic_distance_m(a, b)
        assert d < 500        # ~222m, not 40,000km

    def test_geofence_contains(self):
        fence = gg.GeoFence(vertices=[
            gg.Geodetic(31.00, 121.00),
            gg.Geodetic(31.00, 121.01),
            gg.Geodetic(31.01, 121.01),
            gg.Geodetic(31.01, 121.00)])
        inside = gg.Geodetic(31.005, 121.005)
        outside = gg.Geodetic(31.02, 121.005)
        assert fence.contains(inside) is True
        assert fence.contains(outside) is False

    def test_geojson_lon_lat_order(self):
        fc = json.loads(gg.to_geojson([
            gg.Geodetic(latitude=10.0, longitude=20.0)]))
        pos = fc["features"][0]["geometry"]["coordinates"]
        assert pos[0] == 20.0 and pos[1] == 10.0   # LON FIRST

    def test_kml_output_shape(self):
        kml = gg.to_kml([gg.Geodetic(1.0, 2.0, 3.0)])
        assert "<coordinates>2.0,1.0,3.0" in kml

    def test_range_validation(self):
        with pytest.raises(ValueError):
            gg.Geodetic(latitude=91.0, longitude=0.0)
        with pytest.raises(ValueError):
            gg.Geodetic(latitude=0.0, longitude=181.0)
