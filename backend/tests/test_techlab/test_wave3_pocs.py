"""Wave 3 PoC tests: T07 drawing pins, T08 AR persistence,
T12 upload protocol, T14 C2PA bridge."""

from __future__ import annotations

import hashlib
import math

import pytest

from techlab.ar_persistence import (
    ARExperimentError,
    ARFrameRecord,
    CameraIntrinsics,
    CameraPose,
    FocalUnits,
    Measurement3D,
    TrackingState,
    degrade_report,
    merge_cross_session,
    project_world_to_pixel,
    verify_record_projection,
)
from techlab.c2pa_bridge import (
    BridgeError,
    bridge_manifest,
    serialize_bridge,
    verify_bridge_consistency,
)
from techlab.drawing_pins import (
    CalibrationError,
    CalibrationPair,
    DrawingSheet,
    PhotoPin,
    SheetPoint,
    WorldPoint,
    drawing_hash,
    export_pins_json,
)
from techlab.upload_protocol import (
    ClientResume,
    UploadError,
    UploadServerModel,
    make_batch,
)


# ---------------------------------------------------------------------- T07


def _sheet() -> DrawingSheet:
    pdf_hash = hashlib.sha256(b"drawing-pdf-v1").hexdigest()
    sheet = DrawingSheet(title="Level 2 plan", pdf_content_hash=pdf_hash,
                         geo_origin=(39.9000, 116.4000))
    # identity-ish calibration: sheet mm -> world m with a 0.001 scale
    for s, w in (((0, 0), (0, 0)), ((1000, 0), (1, 0)), ((0, 1000), (0, 1))):
        sheet.add_calibration(CalibrationPair(
            sheet=SheetPoint(x=s[0], y=s[1]), world=WorldPoint(x=w[0], y=w[1])))
    return sheet


def test_calibration_fit_and_residuals():
    from techlab.drawing_pins import SheetCalibration
    cal = SheetCalibration(pairs=[
        CalibrationPair(sheet=SheetPoint(0, 0), world=WorldPoint(0, 0)),
        CalibrationPair(sheet=SheetPoint(1000, 0), world=WorldPoint(1, 0)),
        CalibrationPair(sheet=SheetPoint(0, 1000), world=WorldPoint(0, 1)),
    ])
    M, t, residual = cal.fit()
    assert residual == 0.0                       # exact 3-point fit
    # sanity: 1000 sheet-units map to 1 world-meter
    assert M[0] == pytest.approx(0.001)


def test_collinear_calibration_rejected():
    from techlab.drawing_pins import SheetCalibration
    with pytest.raises(CalibrationError, match="collinear"):
        SheetCalibration(pairs=[
            CalibrationPair(sheet=SheetPoint(0, 0), world=WorldPoint(0, 0)),
            CalibrationPair(sheet=SheetPoint(1, 1), world=WorldPoint(1, 1)),
            CalibrationPair(sheet=SheetPoint(2, 2), world=WorldPoint(2, 2)),
        ]).fit()


def test_pin_targets_sheet_revision():
    sheet = _sheet()
    pin = PhotoPin(photo_id="p1", sheet_rev=sheet.pdf_content_hash[:12],
                   sheet_x=500, sheet_y=300)
    sheet.pin_photo(pin)
    assert sheet.pins[0].photo_id == "p1"
    # wrong revision -> rejected
    with pytest.raises(ValueError, match="different sheet revision"):
        sheet.pin_photo(PhotoPin(photo_id="p2", sheet_rev="deadbeefcafe",
                                 sheet_x=0, sheet_y=0))


def test_manual_location_never_corroborates_pin():
    sheet = _sheet()
    sheet.pin_photo(PhotoPin(photo_id="p-manual",
                             sheet_rev=sheet.pdf_content_hash[:12],
                             sheet_x=500, sheet_y=300))
    sheet.pin_photo(PhotoPin(photo_id="p-gps",
                             sheet_rev=sheet.pdf_content_hash[:12],
                             sheet_x=500, sheet_y=300))
    locations = {
        # manual location a few meters from the pin's world position
        "p-manual": {"source": "manual", "latitude": 39.9001,
                     "longitude": 116.4001, "horizontal_accuracy": None},
        # GPS location ~11 m north of geo origin = close to the pin
        "p-gps": {"source": "gps", "latitude": 39.9001,
                  "longitude": 116.4000, "horizontal_accuracy": 4.0},
    }
    results = {r.photo_id: r for r in sheet.pin_world_positions(locations)}
    assert results["p-manual"].corroborated is False
    assert "cannot corroborate" in results["p-manual"].detail
    assert results["p-gps"].corroborated is True
    assert "radius" in results["p-gps"].detail


def test_no_geo_origin_means_no_corroboration_honestly():
    sheet = _sheet()
    sheet.geo_origin = None
    sheet.pin_photo(PhotoPin(photo_id="p1",
                             sheet_rev=sheet.pdf_content_hash[:12],
                             sheet_x=100, sheet_y=100))
    out = sheet.pin_world_positions({"p1": {"source": "gps",
                                            "latitude": 39.9, "longitude": 116.4,
                                            "horizontal_accuracy": 4.0}})
    assert out[0].corroborated is False
    assert "no geo_origin" in out[0].detail


def test_sheet_json_roundtrip():
    sheet = _sheet()
    sheet.pin_photo(PhotoPin(photo_id="p1",
                             sheet_rev=sheet.pdf_content_hash[:12],
                             sheet_x=10, sheet_y=20, note="B2 column"))
    data = export_pins_json(sheet)
    parsed = DrawingSheet.model_validate_json(data)
    assert parsed.pins[0].photo_id == "p1"
    assert drawing_hash(b"drawing-pdf-v1") == parsed.pdf_content_hash


# ---------------------------------------------------------------------- T08


def _intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(fx=2000.0, fy=2000.0, cx=1920, cy=1080,
                            image_width=3840, image_height=2160,
                            units=FocalUnits.PIXELS)


def _pose() -> CameraPose:
    # identity rotation, camera at origin looking +z (w=1)
    return CameraPose(tx=0.0, ty=0.0, tz=0.0, qx=0.0, qy=0.0, qz=0.0, qw=1.0)


def test_projection_math_identity_pose():
    record = ARFrameRecord(photo_id="ph1", session_id="s1",
                           intrinsics=_intrinsics(), pose=_pose())
    m = Measurement3D(x=0.0, y=0.0, z=4.0)      # 4 m ahead, centered
    u, v = project_world_to_pixel(record, m)
    assert u == pytest.approx(1920.0)            # cx
    assert v == pytest.approx(1080.0)            # cy


def test_projection_offset_point():
    record = ARFrameRecord(photo_id="ph1", session_id="s1",
                           intrinsics=_intrinsics(), pose=_pose())
    m = Measurement3D(x=1.0, y=-0.5, z=4.0)      # right and up 0.5
    u, v = project_world_to_pixel(record, m)
    assert u == pytest.approx(2000.0 * 1.0 / 4.0 + 1920)
    assert v == pytest.approx(2000.0 * (-0.5) / 4.0 + 1080)


def test_persisted_projection_reverified_by_third_party():
    record = ARFrameRecord(photo_id="ph1", session_id="s1",
                           intrinsics=_intrinsics(), pose=_pose())
    record.add_measurement(Measurement3D(x=1.0, y=0.0, z=4.0))
    record.add_measurement(Measurement3D(x=-0.5, y=0.25, z=2.0))
    # Windows-side re-verification recomputes (u, v) and compares
    assert verify_record_projection(record) == []


def test_tampered_persisted_projection_detected():
    record = ARFrameRecord(photo_id="ph1", session_id="s1",
                           intrinsics=_intrinsics(), pose=_pose())
    record.add_measurement(Measurement3D(x=0.0, y=0.0, z=4.0))
    record.measurements[0].persisted_u += 30.0    # tamper after persist
    problems = verify_record_projection(record)
    assert any("differs from recomputed" in p for p in problems)


def test_unverified_units_rejected_by_range_check():
    """W003 review fix: no more 'fx<300 => mm' heuristic. The unit is
    DECLARED; each unit has its own plausible range. An UNSET unit with
    fx=2.0 (out of every plausible range) is rejected with the demand to
    declare; a DECLARED mm value of the same magnitude is fine."""
    # UNSET + out-of-range => rejected, demands declaration
    with pytest.raises(Exception, match="declare units"):
        CameraIntrinsics(fx=2.0, fy=2.0, cx=1920, cy=1080,
                        image_width=3840, image_height=2160)
    # same magnitude, correctly DECLARED as mm_35mm_eq => accepted
    mm = CameraIntrinsics(fx=26.0, fy=26.0, cx=1920, cy=1080,
                          image_width=3840, image_height=2160,
                          units=FocalUnits.MM_35MM_EQ)
    assert mm.units == FocalUnits.MM_35MM_EQ
    # declared pixels but out of pixel range => rejected
    with pytest.raises(Exception, match="outside the plausible range"):
        CameraIntrinsics(fx=2.0, fy=2.0, cx=1920, cy=1080,
                         image_width=3840, image_height=2160,
                         units=FocalUnits.PIXELS)
    # pixels declared + principal point outside image => rejected
    with pytest.raises(Exception, match="principal point"):
        CameraIntrinsics(fx=2000.0, fy=2000.0, cx=99999, cy=1080,
                         image_width=3840, image_height=2160,
                         units=FocalUnits.PIXELS)


def test_point_behind_camera_rejected():
    record = ARFrameRecord(photo_id="ph1", session_id="s1",
                           intrinsics=_intrinsics(), pose=_pose())
    with pytest.raises(ARExperimentError, match="BEHIND"):
        project_world_to_pixel(record, Measurement3D(x=0, y=0, z=-4.0))


def test_degraded_tracking_visible_in_report():
    record = ARFrameRecord(photo_id="ph1", session_id="s1",
                           intrinsics=_intrinsics(), pose=_pose())
    record.add_measurement(Measurement3D(x=0, y=0, z=4.0))
    record.add_measurement(Measurement3D(x=0, y=0, z=4.0,
                                         tracking=TrackingState.LIMITED))
    report = degrade_report(record)
    assert report["total_points"] == 2
    assert report["degraded_points"] == 1
    assert "drift risk" in report["note"]


def test_cross_session_merge_forbidden():
    a = ARFrameRecord(photo_id="ph1", session_id="session-AAA",
                      intrinsics=_intrinsics(), pose=_pose())
    b = ARFrameRecord(photo_id="ph2", session_id="session-BBB",
                      intrinsics=_intrinsics(), pose=_pose())
    b.add_measurement(Measurement3D(x=0, y=0, z=4.0))
    with pytest.raises(ARExperimentError, match="session-local"):
        merge_cross_session(a, b)


def test_same_session_merge_allowed():
    a = ARFrameRecord(photo_id="ph1", session_id="session-AAA",
                      intrinsics=_intrinsics(), pose=_pose())
    b = ARFrameRecord(photo_id="ph2", session_id="session-AAA",
                      intrinsics=_intrinsics(), pose=_pose())
    b.add_measurement(Measurement3D(x=1, y=1, z=4.0))
    merged = merge_cross_session(a, b)
    assert len(merged.measurements) == 1
    assert verify_record_projection(merged) == []


# ---------------------------------------------------------------------- T12


def test_upload_resume_after_partial_out_of_order():
    data = bytes(range(256)) * 4096              # 1 MiB, high entropy
    manifest, chunks = make_batch(data, chunk_size=256 * 1024)
    server = UploadServerModel()
    server.register_batch(manifest)

    # send chunk 2 and 0 (out of order), then 'network drops'
    server.put_chunk(manifest.batch_id, 2, chunks[2])
    server.put_chunk(manifest.batch_id, 0, chunks[0])

    st = server.status(manifest.batch_id)
    assert st["complete"] is False
    pending = ClientResume.pending(st, [c.index for c in manifest.items])
    assert pending == [1, 3]

    # resume: send the rest in reverse order (protocol is order-free)
    for i in reversed(pending):
        server.put_chunk(manifest.batch_id, i, chunks[i])
    assert server.status(manifest.batch_id)["complete"] is True

    result = server.complete(manifest.batch_id)
    assert result["total_sha256"] == manifest.total_sha256
    assert result["bytes"] == len(data)


def test_corrupt_chunk_nacked_slot_reusable():
    data = b"A" * 500_000
    manifest, chunks = make_batch(data, chunk_size=256 * 1024)
    server = UploadServerModel()
    server.register_batch(manifest)

    corrupt = bytearray(chunks[0])
    corrupt[0] ^= 0xFF
    with pytest.raises(UploadError, match="hash mismatch"):
        server.put_chunk(manifest.batch_id, 0, bytes(corrupt))
    # slot NOT occupied: correct retry succeeds
    server.put_chunk(manifest.batch_id, 0, chunks[0])
    assert 0 in server.status(manifest.batch_id)["received"]


def test_duplicate_chunks_idempotent():
    data = b"B" * 700_000
    manifest, chunks = make_batch(data, chunk_size=256 * 1024)
    server = UploadServerModel()
    server.register_batch(manifest)
    r1 = server.put_chunk(manifest.batch_id, 0, chunks[0])
    r2 = server.put_chunk(manifest.batch_id, 0, chunks[0])
    assert r1["status"] == "acked"
    assert r2["status"] == "duplicate_acked"


def test_batch_reuse_with_different_content_conflicts():
    m1, _ = make_batch(b"first-content")
    m2, _ = make_batch(b"second-content")
    m2 = m2.model_copy(update={"batch_id": m1.batch_id})   # force same id
    server = UploadServerModel()
    server.register_batch(m1)
    with pytest.raises(UploadError, match="conflict"):
        server.register_batch(m2)
    # idempotent re-registration of the SAME content is fine
    again = server.register_batch(m1)
    assert again["already_existed"] is True


def test_complete_with_missing_chunks_rejected():
    data = b"C" * 600_000
    manifest, chunks = make_batch(data, chunk_size=256 * 1024)
    server = UploadServerModel()
    server.register_batch(manifest)
    server.put_chunk(manifest.batch_id, 0, chunks[0])
    with pytest.raises(UploadError, match="missing"):
        server.complete(manifest.batch_id)


def test_whole_batch_hash_independent_check():
    # HIGH-ENTROPY data: every chunk is distinct, so swapping in a valid
    # chunk from another position changes the recomposed bytes (with
    # all-D padding the swap would be a no-op and prove nothing)
    import random
    rng = random.Random(20261120)
    data = bytes(rng.getrandbits(8) for _ in range(600_000))
    manifest, chunks = make_batch(data, chunk_size=256 * 1024)
    assert chunks[0] != chunks[1], "test data must have distinct chunks"
    # attacker swaps one VALID chunk for another VALID chunk and forges
    # the plan entry: per-chunk hashes pass, whole-batch must fail
    manifest.items[1].sha256 = manifest.items[0].sha256
    swapped = [chunks[0], chunks[0], chunks[2]]
    server = UploadServerModel()
    server.register_batch(manifest)
    for i, c in enumerate(swapped):
        server.put_chunk(manifest.batch_id, i, c)
    with pytest.raises(UploadError, match="whole-batch hash mismatch"):
        server.complete(manifest.batch_id)


# ---------------------------------------------------------------------- T14


def _wc_manifest() -> dict:
    return {
        "record_id": "rec-001",
        "photo_hash": hashlib.sha256(b"photo-bytes").hexdigest(),
        "capture_timestamp": "2026-11-20T10:00:00+00:00",
        "device_reported_time": "2026-11-20T10:00:01+00:00",
        "device_id": "iphone-pro-001",
        "location": None,
    }


def test_bridge_maps_fields_to_c2pa_actions():
    bridge = bridge_manifest(_wc_manifest())
    assert bridge.actions["c2pa.hash.data"] == _wc_manifest()["photo_hash"]
    assert bridge.actions["stds.exif.dateTimeOriginal"] == "2026-11-20T10:00:00+00:00"
    assert bridge.actions["stds.device.deviceID"] == "iphone-pro-001"
    assert "stds.exif.gps" not in bridge.actions   # None location omitted


def test_bridge_consistency_and_serialization_roundtrip():
    m = _wc_manifest()
    bridge = bridge_manifest(m)
    assert verify_bridge_consistency(m, bridge) == []
    parsed = __import__("techlab.c2pa_bridge", fromlist=["C2PABridgeSpec"]).C2PABridgeSpec.model_validate_json(
        serialize_bridge(bridge))
    assert parsed.actions["c2pa.hash.data"] == m["photo_hash"]


def test_bridge_without_hash_rejected():
    m = _wc_manifest()
    del m["photo_hash"]
    with pytest.raises(BridgeError, match="without the photo hash"):
        bridge_manifest(m)


def test_tampered_bridge_detected():
    m = _wc_manifest()
    bridge = bridge_manifest(m)
    bridge.actions["c2pa.hash.data"] = "ff" * 32     # post-bridge tamper
    problems = verify_bridge_consistency(m, bridge)
    assert any("differs" in p for p in problems)
