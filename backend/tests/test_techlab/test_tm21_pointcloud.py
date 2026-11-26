"""TM21: point-cloud processing tests - deterministic, honest reports."""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.geometry.pointcloud import (  # noqa: E402
    CloudPoint, confidence_filter, edge_protect_smooth, median_filter,
    normal_estimation, radius_outlier_removal, segment_clusters,
    statistical_outlier_removal, to_ply, voxel_downsample)


def _plane_cloud(n: int = 100, size: float = 1.0, z: float = 3.0
                 ) -> list[CloudPoint]:
    """Deterministic scattered points on a horizontal plane."""
    pts = []
    for i in range(n):
        x = size * ((i * 37 % 100) / 100)
        y = size * ((i * 61 % 100) / 100)
        pts.append(CloudPoint(x, y, z, 0.9))
    return pts


class TestVoxelDownsample:
    def test_merges_dense_plane(self):
        pts = _plane_cloud(400, size=0.5)
        out, rep = voxel_downsample(pts, 0.25)
        assert len(out) < len(pts)
        assert rep.kept == len(out) and rep.removed == len(pts) - len(out)
        assert rep.total == len(pts)
        # every centroid stays on the plane
        for p in out:
            assert p.z == pytest.approx(3.0, abs=1e-9)

    def test_empty_and_bad_params(self):
        with pytest.raises(ValueError):
            voxel_downsample([], 0.1)
        with pytest.raises(ValueError):
            voxel_downsample(_plane_cloud(9), 0.0)
        with pytest.raises(ValueError):
            voxel_downsample([CloudPoint(float("nan"), 0, 0)], 0.1)

    def test_confidence_mean_and_deterministic_order(self):
        pts = [CloudPoint(0.1, 0.1, 0, 0.8),
               CloudPoint(0.15, 0.15, 0, 0.6),
               CloudPoint(2.5, 2.5, 0, 1.0)]
        out1, _ = voxel_downsample(pts, 1.0)
        out2, _ = voxel_downsample(pts, 1.0)
        assert [p.xyz for p in out1] == [p.xyz for p in out2]
        first = next(p for p in out1 if p.x < 1)
        assert first.confidence == pytest.approx(0.7)


class TestOutliers:
    def test_statistical_removal_isolated_speckle(self):
        pts = _plane_cloud(150)
        speckle = CloudPoint(50.0, 50.0, 50.0)
        out, rep = statistical_outlier_removal(pts + [speckle], k=8,
                                               sigma_factor=2.0)
        assert all(math.dist(p.xyz, (50, 50, 50)) > 1
                   for p in out)
        assert rep.removed >= 1

    def test_radius_removal_count(self):
        pts = _plane_cloud(100, size=0.2)     # dense patch
        far = [CloudPoint(10 + i, 10 + i, 0) for i in range(3)]
        out, rep = radius_outlier_removal(pts + far, 0.05, 5)
        assert rep.removed == 3
        assert all(p.xyz not in [f.xyz for f in far] for p in out)

    def test_confidence_gate_keeps_missing_confidence(self):
        pts = [CloudPoint(0, 0, 0), CloudPoint(1, 1, 1, 0.2),
               CloudPoint(2, 2, 2, 0.9)]
        out, rep = confidence_filter(pts, 0.5)
        assert rep.removed == 1
        assert out[0].confidence is None      # kept despite no signal
        assert [p.confidence for p in out] == [None, 0.9]


class TestSmoothing:
    def test_median_filter_bounds_displacement(self):
        pts = _plane_cloud(60, size=0.3)
        out, rep = median_filter(pts, k=6)
        assert len(out) == len(pts)
        assert "displacement" in rep.reasons[0]
        # on a plane the median moves points very little
        for o in out:
            assert o.z == pytest.approx(3.0, abs=0.05)

    def test_edge_protect_leaves_true_corner(self):
        """An L-corner (floor plane z=0 meets wall plane x=0): the
        CORNER point's k-NN neighborhood spans both planes, its
        normal spread is high -> the heuristic must leave it
        untouched. Mid-surface points (all-floor neighborhoods) may
        be smoothed - that is the intended asymmetry."""
        pts: list[CloudPoint] = [CloudPoint(0.0, 0.0, 0.0)]  # corner
        # floor grid near the corner
        for x in (0.05, 0.1, 0.15, 0.2):
            for y in (0.05, 0.1):
                pts.append(CloudPoint(x, y, 0.0))
        # wall grid rising from the corner (plane x=0)
        for z in (0.05, 0.1, 0.15, 0.2):
            for y in (0.05, 0.1):
                pts.append(CloudPoint(0.0, y, z))
        out, rep = edge_protect_smooth(pts, k=6,
                                       normal_angle_limit_deg=30)
        assert len(out) == len(pts)
        # the corner is untouched (mixed neighborhood)
        assert out[0].xyz == (0.0, 0.0, 0.0)
        # a mid-floor point IS smoothed by its planar neighborhood
        assert any("smoothed" in r for r in rep.reasons)


class TestNormals:
    def test_plane_normals_point_up(self):
        pts = _plane_cloud(40, size=0.5)
        normals = normal_estimation(pts, k=8)
        valid = [n for n in normals if n is not None]
        assert len(valid) >= 1
        for n in valid:
            assert n[2] == pytest.approx(1.0, abs=1e-6)

    def test_degenerate_neighborhood_is_none_not_guessed(self):
        pts = [CloudPoint(0, 0, 0), CloudPoint(1, 1, 1),
               CloudPoint(2, 2, 2)]              # collinear
        normals = normal_estimation(pts, k=2)
        assert all(n is None for n in normals)


class TestSegmentation:
    def test_two_surfaces_split(self):
        a = [CloudPoint(i * 0.05, 0, 0) for i in range(10)]
        b = [CloudPoint(5 + i * 0.05, 0, 0) for i in range(10)]
        clusters = segment_clusters(a + b, max_dist=0.06)
        assert len(clusters) == 2
        sizes = sorted(len(c) for c in clusters)
        assert sizes == [10, 10]

    def test_single_blob(self):
        pts = _plane_cloud(20, size=0.1)
        assert len(segment_clusters(pts, 0.05)) == 1


class TestPLY:
    def test_header_declares_what_is_present(self):
        pts = [CloudPoint(0, 0, 1, 0.5), CloudPoint(1, 0, 1, 0.6)]
        ply = to_ply(pts, comment="WorkCam sample\nDESKTOP_SIMULATION")
        assert ply.startswith("ply\nformat ascii 1.0")
        assert "comment WorkCam sample" in ply
        assert "element vertex 2" in ply
        assert "property float confidence" in ply
        assert "property float nx" not in ply     # normals absent
        assert "end_header" in ply

    def test_normals_columns(self):
        # confidence-free cloud: only normal columns may appear
        pts = [CloudPoint(0, 0, 0), CloudPoint(0.1, 0.05, 0),
               CloudPoint(0.05, 0.1, 0), CloudPoint(0.3, 0.3, 0.001),
               CloudPoint(0.2, 0.05, 0)]
        normals = normal_estimation(pts, k=4)
        ply = to_ply(pts, normals)
        assert "property float nx" in ply
        assert "property float confidence" not in ply

    def test_mixed_confidence_refused(self):
        pts = [CloudPoint(0, 0, 0, 0.5), CloudPoint(1, 0, 0)]
        with pytest.raises(ValueError):
            to_ply(pts)

    def test_roundtrip_parse(self):
        pts = [CloudPoint(0.25, 1.5, -2.0, 0.75)]
        ply = to_ply(pts)
        header_end = ply.index("end_header") + len("end_header")
        data_line = ply[header_end:].strip().splitlines()[0]
        vals = [float(v) for v in data_line.split()]
        assert vals == pytest.approx([0.25, 1.5, -2.0, 0.75])


class TestHonestReports:
    def test_every_filter_counts_removed(self):
        pts = _plane_cloud(50) + [CloudPoint(99, 99, 99)]
        _, rep = statistical_outlier_removal(pts)
        assert rep.kept + rep.removed == len(pts)
        assert rep.reasons
        _, rep2 = voxel_downsample(pts, 0.1)
        assert rep2.kept + rep2.removed == len(pts)
        _, rep3 = confidence_filter(pts, 0.95)
        # all plane points are 0.9 -> gated; speckle too
        assert rep3.kept + rep3.removed == len(pts)
