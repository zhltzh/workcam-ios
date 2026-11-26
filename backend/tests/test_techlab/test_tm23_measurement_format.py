"""TM23: open measurement-format tests - record model, all four
serializations, honesty rules (manual/device split, uncalibrated
markers, anchor-less GeoJSON skips counted)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.geometry.geo import Geodetic  # noqa: E402
from techlab.geometry.measurement_format import (  # noqa: E402
    FORMAT_VERSION, CoordinateSystem, InputKind, MeasurementRecord,
    MeasurementType, Units, records_to_csv, records_to_geojson,
    records_to_ply, records_to_svg)


def _device_distance() -> MeasurementRecord:
    return MeasurementRecord(
        measurement_id="m-001",
        measurement_type=MeasurementType.distance_3d,
        input_kind=InputKind.device_estimate,
        coordinate_system=CoordinateSystem.camera,
        units=Units.meters,
        value=2.471,
        raw_points=[(0.0, 0.0, -1.0), (0.3, 0.1, -3.4)],
        project_id="p-1", photo_id="ph-1",
        device_model="iPhone15,3",
        measurement_mode="scene_depth",
        tracking_state="normal",
        confidence=0.87,
        uncertainty=0.019,
        notes="beam span")


def _manual_area() -> MeasurementRecord:
    return MeasurementRecord(
        measurement_id="m-002",
        measurement_type=MeasurementType.area,
        input_kind=InputKind.manual_input,
        coordinate_system=CoordinateSystem.world,
        units=Units.square_meters,
        value=12.5,
        raw_points=[(0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0)],
        calibration_version="uncalibrated",
        notes="tape-measured wall")


class TestRecordModel:
    def test_manual_and_device_are_structurally_split(self):
        with pytest.raises(ValueError):
            MeasurementRecord(
                measurement_id="m-bad",
                measurement_type=MeasurementType.distance_3d,
                input_kind=InputKind.manual_input,
                coordinate_system=CoordinateSystem.camera,
                units=Units.meters, value=1.0,
                raw_points=[(0, 0, 0), (1, 0, 0)],
                measurement_mode="scene_depth")   # forbidden combo

    def test_distance_needs_two_points(self):
        with pytest.raises(ValueError):
            MeasurementRecord(
                measurement_id="m-bad2",
                measurement_type=MeasurementType.distance_3d,
                input_kind=InputKind.device_estimate,
                coordinate_system=CoordinateSystem.camera,
                units=Units.meters, value=1.0,
                raw_points=[(0, 0, 0)])

    def test_nonfinite_rejected(self):
        with pytest.raises(ValueError):
            MeasurementRecord(
                measurement_id="m-nan",
                measurement_type=MeasurementType.area,
                input_kind=InputKind.manual_input,
                coordinate_system=CoordinateSystem.world,
                units=Units.square_meters, value=float("nan"))


class TestJSON:
    def test_roundtrip(self):
        rec = _device_distance()
        parsed = MeasurementRecord.from_json(rec.to_json())
        assert parsed.measurement_id == "m-001"
        assert parsed.input_kind == InputKind.device_estimate
        assert parsed.value == rec.value
        assert parsed.raw_points == rec.raw_points
        assert parsed.uncertainty == rec.uncertainty

    def test_version_refusal(self):
        bad = json.loads(_device_distance().to_json())
        bad["format_version"] = "measurement-format/9.9"
        with pytest.raises(ValueError):
            MeasurementRecord.from_json(json.dumps(bad))

    def test_uncalibrated_nature_is_explicit(self):
        rec = _device_distance()
        d = json.loads(rec.to_json())
        assert "not a certified measurement" in \
            d["measurement_nature"]
        manual = json.loads(_manual_area().to_json())
        assert manual["measurement_nature"] == "human-entered value"

    def test_anchor_serializes(self):
        rec = MeasurementRecord(
            measurement_id="m-geo",
            measurement_type=MeasurementType.horizontal_distance,
            input_kind=InputKind.device_estimate,
            coordinate_system=CoordinateSystem.local_enu,
            units=Units.meters, value=95.2,
            raw_points=[(0, 0, 0), (95, 2, 0)],
            geodetic_anchor=Geodetic(31.0, 121.0, 5.0))
        d = json.loads(rec.to_json())
        assert d["geodetic_anchor"]["latitude"] == 31.0
        parsed = MeasurementRecord.from_json(rec.to_json())
        assert parsed.geodetic_anchor is not None
        assert parsed.geodetic_anchor.longitude == 121.0


class TestCSV:
    def test_rows_and_header(self):
        out = records_to_csv([_device_distance(), _manual_area()])
        lines = out.strip().splitlines()
        assert lines[0].startswith("measurement_id")
        assert len(lines) == 3
        assert "m-001" in lines[1] and "m-002" in lines[2]
        # empty optional fields serialize as empty strings, not None
        assert ",," in lines[2]          # manual row lacks device cols

    def test_roundtrip_via_json_column(self):
        rec = _device_distance()
        row = rec.to_csv_row()
        pts = json.loads(row["raw_points"])
        assert pts == [[0.0, 0.0, -1.0], [0.3, 0.1, -3.4]]


class TestGeoJSON:
    def test_anchorless_skipped_and_counted(self):
        doc = json.loads(records_to_geojson(
            [_device_distance(), _manual_area()]))
        assert doc["features"] == []
        assert doc["x_workcam_notes"]["skipped_no_anchor"] == 2

    def test_anchored_feature_with_lon_first(self):
        rec = MeasurementRecord(
            measurement_id="m-g1",
            measurement_type=MeasurementType.horizontal_distance,
            input_kind=InputKind.device_estimate,
            coordinate_system=CoordinateSystem.wgs84,
            units=Units.meters, value=50.0,
            raw_points=[(0, 0, 0), (50, 0, 0)],
            geodetic_anchor=Geodetic(10.0, 20.0, 3.0))
        doc = json.loads(records_to_geojson([rec]))
        f = doc["features"][0]
        assert f["geometry"]["coordinates"][0] == 20.0   # LON first
        assert f["properties"]["value"] == 50.0
        assert f["properties"]["input_kind"] == "device_estimate"


class TestPLY:
    def test_points_and_record_comments(self):
        out = records_to_ply([_device_distance()],
                              comment="sketch cloud")
        assert out.startswith("ply")
        assert "comment sketch cloud" in out
        assert "record m-001" in out
        assert "element vertex 2" in out


class TestSVG:
    def test_sketch_shape(self):
        svg = records_to_svg([_device_distance(), _manual_area()])
        assert svg.startswith("<svg")
        assert svg.rstrip().endswith("</svg>")
        assert "not to scale" in svg
        assert 'stroke="#c0392b"' in svg     # record 1 color
        assert 'stroke="#2980b9"' in svg     # record 2 color
        assert "[manual_input]" in svg

    def test_empty_refused(self):
        with pytest.raises(ValueError):
            records_to_svg([])


class TestDXFPoc:
    """Experimental R12 ASCII PoC - structure + traceability only
    (it is NOT a production format; no CAD round-trip on Windows)."""

    def test_r12_structure_and_layers(self):
        from techlab.geometry.dxf_poc import records_to_dxf
        out = records_to_dxf([_device_distance()])
        assert "0\nSECTION\n2\nENTITIES" in out
        assert "0\nENDSEC\n0\nEOF" in out
        assert "8\nWCM-MEASURE-m-001" in out       # traceable layer
        assert "POLYLINE" in out and "VERTEX" in out \
            and "SEQEND" in out
        assert "distance_3d=2.471m [device_estimate]" in out

    def test_header_note_marks_experimental(self):
        from techlab.geometry.dxf_poc import records_to_dxf
        out = records_to_dxf([_manual_area()])
        assert "experimental DXF R12 PoC" in out
        # single-point records become CIRCLE (visibility)
        single = MeasurementRecord(
            measurement_id="m-single",
            measurement_type=MeasurementType.area,
            input_kind=InputKind.manual_input,
            coordinate_system=CoordinateSystem.world,
            units=Units.square_meters, value=1.0,
            raw_points=[(1, 2, 3)])
        assert "CIRCLE" in records_to_dxf([single])

    def test_empty_refused(self):
        from techlab.geometry.dxf_poc import records_to_dxf
        with pytest.raises(ValueError):
            records_to_dxf([])
