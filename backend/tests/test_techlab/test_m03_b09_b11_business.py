"""M03-B09/B10/B11 tests - assets, measurements, daily log."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.business.records import (  # noqa: E402
    Asset, AssetError, AssetScan, DailyLog, DailyLogError,
    ManagedMeasurement, MeasurementLink, MeasurementRecordError,
    MeasurementSource, ScanSource)


class TestB09Assets:
    def test_basic_asset(self):
        a = Asset(asset_id="ast-1", asset_number="AHU-B2-01",
                  model="Trane XV18", manufacturer="Trane")
        assert a.asset_number == "AHU-B2-01"

    def test_scan_candidate_never_identity(self):
        a = Asset("ast-1", "AHU-B2-01")
        scan = AssetScan(source=ScanSource.ocr,
                         raw_value="AHU-B2-01 (OCR, typo risk)",
                         confidence=0.82)
        a.add_scan(scan)
        assert a.identity_scan is None          # unconfirmed = candidate
        assert len(a.pending_scans) == 1

    def test_confirmed_scan_becomes_identity(self):
        a = Asset("ast-1", "AHU-B2-01")
        scan = AssetScan(source=ScanSource.qr_code,
                         raw_value="AHU-B2-01")
        scan.confirm("AHU-B2-01", "alice")
        a.add_scan(scan)
        assert a.identity_scan is not None
        assert a.identity_scan.confirmed_value == "AHU-B2-01"

    def test_ocr_correction_recorded(self):
        a = Asset("ast-1", "AHU-B2-01")
        scan = AssetScan(source=ScanSource.ocr, raw_value="AHU-82-01",
                         confidence=0.6)     # OCR misread B as 8
        scan.confirm("AHU-B2-01", "bob")     # human corrected
        a.add_scan(scan)
        assert scan.raw_value == "AHU-82-01"      # raw preserved
        assert scan.confirmed_value == "AHU-B2-01"  # corrected

    def test_inspection_history(self):
        a = Asset("ast-1", "AHU-B2-01")
        a.record_inspection("2026-11-20", "pass", "filters clean",
                            ["ph-1"])
        a.record_inspection("2026-11-26", "fail", "bearing noise")
        assert len(a.inspection_history) == 2
        assert a.inspection_history[-1]["result"] == "fail"

    def test_empty_asset_refused(self):
        with pytest.raises(AssetError):
            Asset("", "")


class TestB10Measurements:
    def test_device_measurement(self):
        m = ManagedMeasurement(
            measurement_id="meas-1", measurement_type="distance_3d",
            value=2.471, unit="m",
            source=MeasurementSource.device_estimate,
            measurement_mode="scene_depth",
            raw_points=[(0, 0, -1), (0.3, 0.1, -3.4)],
            link=MeasurementLink(photo_id="ph-1", issue_id="iss-1"))
        assert "NOT a device spec" in m.honest_label()

    def test_manual_measurement(self):
        m = ManagedMeasurement(
            measurement_id="meas-2", measurement_type="area",
            value=12.5, unit="m2",
            source=MeasurementSource.manual_input)
        assert "human-entered" in m.honest_label()

    def test_device_needs_mode(self):
        with pytest.raises(MeasurementRecordError):
            ManagedMeasurement(
                measurement_id="m-bad",
                measurement_type="distance_3d", value=1.0, unit="m",
                source=MeasurementSource.device_estimate)  # no mode

    def test_manual_has_no_mode(self):
        with pytest.raises(MeasurementRecordError):
            ManagedMeasurement(
                measurement_id="m-bad2",
                measurement_type="distance_3d", value=1.0, unit="m",
                source=MeasurementSource.manual_input,
                measurement_mode="scene_depth")     # forbidden

    def test_volume_requires_experimental_note(self):
        with pytest.raises(MeasurementRecordError, match="EXPERIMENTAL"):
            ManagedMeasurement(
                measurement_id="m-bad3", measurement_type="volume",
                value=30.0, unit="m3",
                source=MeasurementSource.device_estimate,
                measurement_mode="scene_depth")
        # with the note it works
        m = ManagedMeasurement(
            measurement_id="m-vol", measurement_type="volume",
            value=30.0, unit="m3",
            source=MeasurementSource.device_estimate,
            measurement_mode="scene_depth",
            notes="experimental bounding-box estimate")
        assert "experimental" in m.notes

    def test_calibrated_label(self):
        m = ManagedMeasurement(
            measurement_id="m-cal", measurement_type="slope",
            value=15.0, unit="percent",
            source=MeasurementSource.device_estimate,
            measurement_mode="raycast",
            calibration_version="iPhone15-3/lab-2026-11")
        assert "calibrated" in m.honest_label()
        assert "NOT a device spec" not in m.honest_label()


class TestB11DailyLog:
    def test_activity_set_and_frozen(self):
        log = DailyLog(project_id="p-1", date="2026-11-26")
        log.set_activity(photos_captured=12, issues_created=2,
                         issues_resolved=1, checklist_runs=1,
                         measurements_recorded=3, reports_delivered=1)
        with pytest.raises(DailyLogError, match="frozen"):
            log.set_activity(photos_captured=99)   # frozen

    def test_negative_refused(self):
        log = DailyLog(project_id="p-1", date="2026-11-26")
        with pytest.raises(DailyLogError, match="negative"):
            log.set_activity(photos_captured=-5)

    def test_unknown_field_refused(self):
        log = DailyLog(project_id="p-1", date="2026-11-26")
        with pytest.raises(DailyLogError, match="unknown"):
            log.set_activity(fake_field=1)

    def test_weather_is_user_only(self):
        log = DailyLog(project_id="p-1", date="2026-11-26")
        log.set_activity(photos_captured=5)
        # user did NOT fill the weather -> the summary says so
        md = log.summary_markdown()
        assert "_(not filled)_" in md
        # the engine NEVER invents weather
        log.user_fill(weather="sunny, 22C", site_conditions="dry")
        md = log.summary_markdown()
        assert "sunny, 22C" in md

    def test_bad_date_refused(self):
        with pytest.raises(DailyLogError):
            DailyLog(project_id="p-1", date="2026/11/26")

    def test_json_roundtrip(self):
        log = DailyLog(project_id="p-1", date="2026-11-26")
        log.set_activity(photos_captured=8, issues_created=1)
        log.user_fill(general_notes="concrete pour completed")
        data = json.loads(log.to_json())
        assert data["photos_captured"] == 8
        assert data["general_notes"] == "concrete pour completed"


import json  # noqa: E402
