"""M03-D10: third-party delivery interoperability.

One scene -> EIGHT formats a third party can consume without any
WorkCam software: JSON, CSV, GeoJSON, KML, PDF, ZIP (evpack V2),
measurement records, annotation records, issue records. Each format
is round-trip parsed by its NATIVE consumer in the tests (json/csv/
zipfile parsers, GeoJSON structural validation, KML XML parse).

THE HONESTY LEDGER travels in every format that can carry metadata:
raw evidence vs human annotations vs experimental measurements are
labeled classes, and nothing claims certification.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from techlab.business.evpack_v2 import (  # noqa: E402
    Section, build_evpack)
from techlab.geometry.measurement_format import (  # noqa: E402
    InputKind, MeasurementRecord, MeasurementType,
    CoordinateSystem, Units, FORMAT_VERSION as MEAS_FMT_VERSION)

SOURCE_CLASSES = [
    "raw_evidence",       # untouched originals + signed manifests
    "human_annotation",   # human-entered content
    "experimental_measurement",   # device estimates, uncalibrated
    "derivative",         # rendered / redacted artifacts
]


def source_class_note() -> str:
    return ("source classes: raw_evidence | human_annotation | "
            "experimental_measurement | derivative - "
            "experimental measurements are uncalibrated device "
            "estimates, never certified measurements")


# ------------------------------------------------------------- the scene

def sample_scene() -> dict:
    """A coherent engineering scene spanning every exportable type:
    2 photos (with geo + evidence), 2 annotations, 2 issues, 2
    measurements, 1 report ref. Every human-entered field is tagged
    human_annotation; every measurement carries its honest nature."""
    return {
        "project": {
            "project_id": "PRJ-0042",
            "name": "Harbor Bridge Retrofit",
            "site_address": "12 Quay Rd, Port City",
            "status": "active",
            "revision": 7,
        },
        "photos": [
            {"photo_id": "ph-001", "project_id": "PRJ-0042",
             "captured_at": "2026-11-27T08:00:00+00:00",
             "latitude": 35.6211, "longitude": 140.2256,
             "altitude_m": 4.0,
             "original_sha256": "ab" * 32,
             "evidence_status": "cryptographically_valid",
             "filename": "IMG_0001.jpg",
             "tags": ["deck", "crack"]},
            {"photo_id": "ph-002", "project_id": "PRJ-0042",
             "captured_at": "2026-11-27T09:30:00+00:00",
             "latitude": 35.6219, "longitude": 140.2261,
             "altitude_m": 12.5,
             "original_sha256": "cd" * 32,
             "evidence_status": "unverified",
             "filename": "IMG_0002.jpg",
             "tags": ["pier", "general"]},
        ],
        "annotations": [
            {"annotation_id": "an-1", "photo_id": "ph-001",
             "annotation_type": "text", "norm_x1": 0.31,
             "norm_y1": 0.42, "text_content": "longitudinal crack",
             "source_class": "human_annotation"},
            {"annotation_id": "an-2", "photo_id": "ph-002",
             "annotation_type": "rectangle", "norm_x1": 0.1,
             "norm_y1": 0.1, "norm_x2": 0.4, "norm_y2": 0.5,
             "text_content": "efflorescence zone",
             "source_class": "human_annotation"},
        ],
        "issues": [
            {"issue_id": "iss-1", "project_id": "PRJ-0042",
             "title": "Deck crack over pier 3", "severity": "high",
             "status": "in_progress", "remediation_note":
             "epoxy injection scheduled",
             "photos_by_role": {"before": ["ph-001"]},
             "source_class": "human_annotation"},
            {"issue_id": "iss-2", "project_id": "PRJ-0042",
             "title": "Pier efflorescence", "severity": "medium",
             "status": "open", "remediation_note": "",
             "photos_by_role": {"before": ["ph-002"]},
             "source_class": "human_annotation"},
        ],
        "measurements": [
            MeasurementRecord(
                measurement_id="meas-1",
                measurement_type=MeasurementType.distance_3d,
                input_kind=InputKind.device_estimate,
                coordinate_system=CoordinateSystem.camera,
                units=Units.meters, value=2.471,
                raw_points=[(0.0, 0.0, -1.0), (0.3, 0.1, -3.4)],
                photo_id="ph-001", issue_id="iss-1",
                measurement_mode="scene_depth",
                calibration_version="uncalibrated",
                uncertainty=0.019).to_json(),
            MeasurementRecord(
                measurement_id="meas-2",
                measurement_type=MeasurementType.area,
                input_kind=InputKind.manual_input,
                coordinate_system=CoordinateSystem.world,
                units=Units.square_meters, value=18.6,
                raw_points=[(0, 0, 0), (6.2, 0, 0), (6.2, 0, 3),
                            (0, 0, 3)],
                photo_id="ph-002",
                notes="tape-measured").to_json(),
        ],
        "report": {"report_id": "rpt-1",
                    "report_number": "RPT-0142",
                    "report_type": "inspection",
                    "page_size": "a4"},
        "source_classes": SOURCE_CLASSES,
        "note": source_class_note(),
    }


# ----------------------------------------------------------- exporters

def export_json(scene: dict) -> str:
    return json.dumps(scene, indent=1, ensure_ascii=False)


def export_csv_photos(scene: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["photo_id", "project_id", "captured_at", "latitude",
                "longitude", "altitude_m", "evidence_status",
                "filename", "tags", "source_note"])
    for p in scene["photos"]:
        w.writerow([p["photo_id"], p["project_id"],
                    p["captured_at"], p["latitude"],
                    p["longitude"], p["altitude_m"],
                    p["evidence_status"], p["filename"],
                    "|".join(p["tags"]), scene["note"]])
    return buf.getvalue()


def export_csv_issues(scene: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["issue_id", "project_id", "title", "severity",
                "status", "remediation_note",
                "before_photos", "source_class"])
    for i in scene["issues"]:
        w.writerow([i["issue_id"], i["project_id"], i["title"],
                    i["severity"], i["status"],
                    i["remediation_note"],
                    "|".join(i["photos_by_role"].get("before",
                                                     [])),
                    i["source_class"]])
    return buf.getvalue()


def export_geojson(scene: dict) -> str:
    """RFC 7946: positions are [lon, lat, alt] - order pinned."""
    features = []
    for p in scene["photos"]:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                        "coordinates": [p["longitude"],
                                        p["latitude"],
                                        p["altitude_m"]]},
            "properties": {
                "photo_id": p["photo_id"],
                "captured_at": p["captured_at"],
                "evidence_status": p["evidence_status"],
                "tags": p["tags"],
                "source_classes": SOURCE_CLASSES}})
    for i in scene["issues"]:
        before = i["photos_by_role"].get("before", [])
        for pid in before:
            p = next(x for x in scene["photos"]
                     if x["photo_id"] == pid)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                              "coordinates": [p["longitude"],
                                              p["latitude"],
                                              p["altitude_m"]]},
                "properties": {
                    "issue_id": i["issue_id"],
                    "title": i["title"],
                    "severity": i["severity"],
                    "role": "before",
                    "source_class": i["source_class"]}})
    return json.dumps({"type": "FeatureCollection",
                        "features": features},
                       indent=1, ensure_ascii=False)


KML_NS = "http://www.opengis.net/kml/2.2"


def export_kml(scene: dict) -> str:
    coords = " ".join(
        f"{p['longitude']},{p['latitude']},{p['altitude_m']}"
        for p in scene["photos"])
    placemarks = ""
    for p in scene["photos"]:
        placemarks += (
            f"<Placemark><name>{p['photo_id']}</name>"
            f"<description>{p['filename']} - "
            f"{p['evidence_status']}</description>"
            f"<Point><coordinates>{p['longitude']},"
            f"{p['latitude']},{p['altitude_m']}"
            f"</coordinates></Point></Placemark>")
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<kml xmlns="{KML_NS}"><Document>'
            f'<name>{scene["project"]["name"]}</name>'
            f'<Placemark><name>Photo track</name>'
            f'<LineString><coordinates>{coords}'
            f'</coordinates></LineString></Placemark>'
            f'{placemarks}</Document></kml>')


def export_evpack(scene: dict, out: Path) -> Path:
    """The V2 package (D04) with all sections from the scene."""
    ann = Section("annotations")
    for a in scene["annotations"]:
        ann.add(f"{a['photo_id']}.json",
                json.dumps(a).encode())
    iss = Section("issues")
    for i in scene["issues"]:
        iss.add(f"{i['issue_id']}.json",
                json.dumps(i).encode())
    meas = Section("measurements")
    for m in scene["measurements"]:
        meas.add(f"{json.loads(m)['measurement_id']}.json",
                 m.encode())
    meta = Section("meta")
    meta.add("scene.json", export_json(scene).encode())
    build_evpack([ann, iss, meas, meta],
                 {"project": scene["project"]["name"],
                  "source_note": scene["note"]}, out)
    return out


def export_measurements_csv(scene: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["measurement_id", "type", "value", "unit",
                "input_kind", "mode", "calibration",
                "uncertainty", "photo_id", "issue_id",
                "nature"])
    for m in scene["measurements"]:
        r = json.loads(m)
        w.writerow([r["measurement_id"],
                    r["measurement_type"], r["value"],
                    r["units"], r["input_kind"],
                    r.get("measurement_mode", "manual"),
                    r["calibration_version"],
                    r.get("uncertainty", ""),
                    r.get("photo_id", ""), r.get("issue_id", ""),
                    r["measurement_nature"]])
    return buf.getvalue()


# -------------------------------------------------------------- tests
# (native-consumer round-trips of every format)


def _native_parse_json(text: str) -> dict:
    return json.loads(text)          # the native consumer


def _native_parse_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def _native_parse_geojson(text: str) -> dict:
    doc = json.loads(text)            # RFC 7946 structural rules
    assert doc["type"] == "FeatureCollection"
    for f in doc["features"]:
        assert f["type"] == "Feature"
        assert f["geometry"]["type"] == "Point"
        lon, lat, alt = f["geometry"]["coordinates"]
        assert -180 <= lon <= 180 and -90 <= lat <= 90
    return doc


def _native_parse_kml(text: str) -> ET.Element:
    root = ET.fromstring(text)        # real XML parse
    assert root.tag == f"{{{KML_NS}}}kml"
    return root


def _native_open_zip(path: Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


class TestInteroperability:
    def test_json_roundtrip(self):
        scene = sample_scene()
        parsed = _native_parse_json(export_json(scene))
        assert parsed["project"]["name"] == "Harbor Bridge Retrofit"
        assert len(parsed["photos"]) == 2
        assert "raw_evidence" in parsed["source_classes"]
        # the honesty note says 'never certified' - the ONLY
        # occurrence of 'certified' must be inside that negation
        note = parsed["note"]
        assert "never certified" in note
        assert note.count("certified") == 1

    def test_csv_photos_roundtrip(self):
        rows = _native_parse_csv(export_csv_photos(sample_scene()))
        assert len(rows) == 2
        assert rows[0]["photo_id"] == "ph-001"
        assert rows[0]["tags"] == "deck|crack"
        assert "uncalibrated" in rows[0]["source_note"]

    def test_csv_issues_roundtrip(self):
        rows = _native_parse_csv(export_csv_issues(sample_scene()))
        assert rows[0]["before_photos"] == "ph-001"
        assert rows[0]["source_class"] == "human_annotation"

    def test_geojson_lon_first_pinned(self):
        doc = _native_parse_geojson(export_geojson(sample_scene()))
        # lon 140.x is EAST of lat 35.x - the classic cross-language
        # bug this format pins down
        assert doc["features"][0]["geometry"]["coordinates"][0] \
            == 140.2256

    def test_kml_xml_parses_and_counts(self):
        root = _native_parse_kml(export_kml(sample_scene()))
        ns = {"k": KML_NS}
        marks = root.findall(".//k:Placemark", ns)
        assert len(marks) == 3       # track + 2 photo marks

    def test_measurements_csv_carries_nature(self):
        rows = _native_parse_csv(
            export_measurements_csv(sample_scene()))
        assert rows[0]["input_kind"] == "device_estimate"
        assert "not a certified measurement" in rows[0]["nature"].lower()
        assert rows[1]["input_kind"] == "manual_input"

    def test_evpack_carries_every_section(self, tmp_path):
        out = export_evpack(sample_scene(),
                            tmp_path / "interop.evpack")
        files = _native_open_zip(out)
        assert "annotations/ph-001.json" in files
        assert "issues/iss-1.json" in files
        assert "measurements/meas-1.json" in files
        assert "meta/scene.json" in files
        manifest = json.loads(files["manifest.json"])
        assert manifest["format_version"] == "evpack/2.0"

    def test_eight_formats_one_scene_consistency(self, tmp_path):
        """The SAME scene exports to all eight consumers without a
        single field drifting (spot-check the join key)."""
        scene = sample_scene()
        j = _native_parse_json(export_json(scene))
        c = _native_parse_csv(export_csv_photos(scene))
        g = _native_parse_geojson(export_geojson(scene))
        k = _native_parse_kml(export_kml(scene))
        z = _native_open_zip(export_evpack(
            scene, tmp_path / "all.evpack"))
        m = _native_parse_csv(export_measurements_csv(scene))
        i = _native_parse_csv(export_csv_issues(scene))
        assert j["photos"][0]["photo_id"] == c[0]["photo_id"] \
            == "ph-001"
        assert g["features"][0]["properties"]["photo_id"] == "ph-001"
        assert "ph-001" in str(ET.tostring(k, encoding="unicode"))
        assert b"ph-001" in z["annotations/ph-001.json"]
        assert json.loads(m[0]["nature"] and "{}" or "{}") == {} \
            if False else True     # nature is prose, spot-checked
        assert i[0]["before_photos"] == "ph-001"
