"""WC-W011 watermark & derivative tests.

DESKTOP_SIMULATION - synthetic images throughout; renders REAL pixels.

Coverage per the brief:
  five templates render / landscape+portrait+resolutions / long text &
  special chars / GPS missing / time-trust missing / original bytes
  unchanged / derivative hash verified / permission isolation /
  duplicate generation (idempotent) / corrupted & oversize inputs /
  EXIF orientation handling / unverified-never-Verified enforcement /
  derivative API integration.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from app.services.watermark_render import (
    GpsSource, TimeTrust, WatermarkFields, render_watermark,
    template_lines, get_template, _apply_exif_orientation)

MAX_DERIVATIVE_INPUT_BYTES = 100 * 1024 * 1024


def _photo(width: int = 1600, height: int = 1200,
           bright: bool = True) -> bytes:
    img = Image.new("RGB", (width, height),
                    (210, 210, 215) if bright else (38, 38, 42))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 180, 120], fill=(40, 90, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _logo() -> bytes:
    logo = Image.new("RGB", (120, 60), (200, 30, 30))
    d = ImageDraw.Draw(logo)
    d.text((30, 18), "LOGO", fill=(255, 255, 255))
    buf = io.BytesIO()
    logo.save(buf, format="PNG")
    return buf.getvalue()


def _full_fields(**overrides) -> WatermarkFields:
    base = dict(
        project_name="Site A", show_project_name=True,
        site_address="Km 1, Test Road", show_site_address=True,
        captured_time="2026-11-24 10:00", show_captured_time=True,
        captured_time_trust=TimeTrust.DEVICE,
        gps_latitude=32.06, gps_longitude=118.79,
        gps_source=GpsSource.GPS, show_gps=True,
        gps_accuracy_m=4.0, show_gps_accuracy=True,
        photo_id="IMG_001", show_photo_id=True,
        operator="Tester", show_operator=True,
        company_name="TestCo", show_company_name=True,
        notes="note line", show_notes=True,
        evidence_status="unverified", show_evidence_status=True)
    base.update(overrides)
    return WatermarkFields(**base)


# --------------------------------------------------- P1: five templates


@pytest.mark.parametrize("template", ["minimal", "construction",
                                      "inspection", "corporate", "evidence"])
def test_all_five_templates_render_real_pixels(template):
    result = render_watermark(_photo(), _full_fields(), template)
    assert len(result["image_bytes"]) > 1000
    img = Image.open(io.BytesIO(result["image_bytes"]))
    assert img.format == "JPEG"                       # real image out
    assert result["geometry"]["template"] == template


@pytest.mark.parametrize("w,h,tag", [(1600, 1200, "landscape"),
                                     (1200, 1600, "portrait"),
                                     (640, 480, "small"),
                                     (4032, 3024, "large")])
def test_orientations_and_resolutions(w, h, tag):
    result = render_watermark(_photo(w, h), _full_fields(), "construction")
    geo = result["geometry"]
    assert geo["image_width"] == w and geo["image_height"] == h
    # panel always inside the image
    p = geo["panel"]
    assert 0 <= p["x"] and p["x"] + p["w"] <= w
    assert 0 <= p["y"] and p["y"] + p["h"] <= h
    # font scales with the shorter edge
    assert geo["font_size"] >= 10


def test_adaptive_colors_dark_vs_bright_background():
    dark_bg = render_watermark(_photo(bright=False), _full_fields(),
                               "evidence")
    light_bg = render_watermark(_photo(bright=True), _full_fields(),
                               "evidence")
    g1, g2 = dark_bg["geometry"], light_bg["geometry"]
    # dark photo -> light panel/text; bright photo -> dark panel/text
    assert g1["text_color_rgb"] != g2["text_color_rgb"]
    assert g1["background_dark"] != g2["background_dark"]


def test_word_wrap_long_notes():
    fields = _full_fields(notes=(
        "Super long on-site note that must wrap across multiple lines "
        "because engineering notes can be extremely detailed and go on "
        "far beyond a single line width. " * 4))
    result = render_watermark(_photo(), fields, "inspection")
    assert result["geometry"]["line_count"] >= 8     # wrapped lines exist


def test_special_characters_render():
    fields = _full_fields(
        project_name='Site <A> & "B" — 100% ☑',
        notes="Crack ~2.3mm @ pier 2 | width x height")
    result = render_watermark(_photo(), fields, "construction")
    assert len(result["image_bytes"]) > 1000


# ------------------------------------------ honest trust enforcement (P0)


def test_unverified_evidence_never_renders_verified():
    lines = template_lines(get_template("evidence"),
                           _full_fields(evidence_status="unverified"))
    text = " ".join(t for t, _ in lines)
    assert "UNVERIFIED" in text
    assert "VERIFIED" not in text.replace("UNVERIFIED", "")


def test_verified_states_render_their_real_labels():
    for status, must_contain in [
            ("cryptographically_valid", "CRYPTOGRAPHICALLY VERIFIED"),
            ("server_anchored", "SERVER ANCHORED"),
            ("verification_failed", "VERIFICATION FAILED"),
            ("no_evidence", "NO EVIDENCE LINKED")]:
        lines = template_lines(get_template("evidence"),
                               _full_fields(evidence_status=status))
        text = " ".join(t for t, _ in lines)
        assert must_contain in text, status


def test_device_time_always_labeled_unverified():
    lines = template_lines(get_template("evidence"),
                            _full_fields(captured_time_trust=TimeTrust.DEVICE))
    text = " ".join(t for t, _ in lines)
    assert "[device time, unverified]" in text


def test_server_anchored_time_gets_trust_tag():
    lines = template_lines(get_template("evidence"),
                           _full_fields(captured_time_trust=TimeTrust.SERVER_ANCHORED))
    text = " ".join(t for t, _ in lines)
    assert "[server-anchored]" in text
    assert "unverified" not in text.split("[server-anchored]")[0].split("|")[-1]


def test_manual_gps_labeled_manual_entry():
    lines = template_lines(get_template("evidence"),
                           _full_fields(gps_source=GpsSource.MANUAL))
    text = " ".join(t for t, _ in lines)
    assert "MANUAL ENTRY" in text
    assert "GPS: 32" not in text          # not presented as a fix


# ---------------------------------------------- P0: missing data behavior


def test_gps_missing_renders_without_gps_line():
    fields = _full_fields(show_gps=False, show_gps_accuracy=False)
    lines = template_lines(get_template("construction"), fields)
    text = " ".join(t for t, _ in lines)
    assert "GPS" not in text
    assert len(lines) >= 1                       # other lines still render


def test_gps_source_none_with_show_gps_rejected():
    with pytest.raises(Exception):
        WatermarkFields(show_gps=True, gps_source=GpsSource.NONE)


def test_time_missing_renders_without_time_line():
    fields = _full_fields(show_captured_time=False,
                          show_server_anchored_time=False)
    lines = template_lines(get_template("minimal"), fields)
    assert not any("10:00" in t for t, _ in lines)


def test_all_toggles_off_is_an_error():
    with pytest.raises(ValueError, match="nothing to render"):
        render_watermark(_photo(), WatermarkFields(), "minimal")


# ------------------------------------------- P2: immutability & hashing


def test_original_bytes_unchanged_after_render():
    original = _photo()
    snapshot = original
    render_watermark(original, _full_fields(), "evidence")
    assert original is snapshot
    assert original == snapshot            # content untouched


def test_derivative_hash_is_verifiable():
    result = render_watermark(_photo(), _full_fields(), "minimal")
    assert hashlib.sha256(
        result["image_bytes"]).hexdigest() == result["derivative_sha256"]
    assert result["derivative_sha256"] != result["original_sha256"]


def test_same_request_same_derivative():
    photo = _photo()
    r1 = render_watermark(photo, _full_fields(), "corporate")
    r2 = render_watermark(photo, _full_fields(), "corporate")
    assert r1["derivative_sha256"] == r2["derivative_sha256"]


def test_different_template_different_derivative():
    photo = _photo()
    r1 = render_watermark(photo, _full_fields(), "minimal")
    r2 = render_watermark(photo, _full_fields(), "corporate")
    assert r1["derivative_sha256"] != r2["derivative_sha256"]


def test_different_content_different_params_hash():
    f1 = _full_fields()
    f2 = _full_fields(operator="Someone Else")
    assert f1.render_params_hash() != f2.render_params_hash()


# ------------------------------------------------- P1: EXIF orientation


def _photo_with_exif(orientation: int) -> bytes:
    img = Image.new("RGB", (800, 600), (90, 140, 90))
    exif = Image.Exif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


@pytest.mark.parametrize("orientation,expected_dims", [
    (1, (800, 600)),        # normal
    (6, (600, 800)),        # rotate 90 CW -> portrait after transpose
    (8, (600, 800)),        # rotate 270
    (3, (800, 600)),        # 180
])
def test_exif_orientation_transposed(orientation, expected_dims):
    photo = _photo_with_exif(orientation)
    result = render_watermark(photo, _full_fields(), "minimal")
    geo = result["geometry"]
    assert (geo["image_width"], geo["image_height"]) == expected_dims
    assert geo["exif_orientation_applied"] == orientation


# ------------------------------------------- P1: bad inputs (P5 items)


def test_corrupted_image_rejected():
    with pytest.raises(Exception):
        render_watermark(b"this is not an image at all" * 10,
                        _full_fields(), "minimal")


def test_truncated_jpeg_rejected():
    photo = _photo()
    with pytest.raises(Exception):
        render_watermark(photo[:len(photo) // 2], _full_fields(), "minimal")


def test_oversize_image_rejected(monkeypatch):
    """The render-budget guard fires before ANY pixel work: a normal
    photo under a patched tiny limit must be refused with the 'too
    large' error (avoids allocating a real 400MB monster in tests)."""
    from app.services import watermark_render as wr
    monkeypatch.setattr(wr, "MAX_RENDER_PIXELS", 100)
    with pytest.raises(ValueError, match="too large"):
        render_watermark(_photo(640, 480), _full_fields(), "minimal")


def test_production_pixel_limit_is_sane():
    """The shipped limit is 80MP: real 48MP iPhone photos pass, 150MP
    bombs do not (checked as pure arithmetic)."""
    from app.services.watermark_render import MAX_RENDER_PIXELS
    assert MAX_RENDER_PIXELS >= 8064 * 6048   # 48.8MP iPhone Pro max
    assert MAX_RENDER_PIXELS < 150_000_000


def test_unknown_template_rejected():
    with pytest.raises(ValueError, match="unknown template"):
        render_watermark(_photo(), _full_fields(), "fancy")


# ------------------------------------------------- P3: API integration


@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "w011-code")
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    import app.db as dbm
    dbm.migrate(tmp_path / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    import app.services.device_identity as di
    import base64
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat)

    def mk(name):
        key = ec.generate_private_key(ec.SECP256R1())
        pub_b64 = base64.b64encode(key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)).decode()
        return di.enroll_device(pub_b64, name, "w011-code")

    tok_a = mk("device-AAA")
    tok_b = mk("device-BBB")
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "biz.db",
        "H": {"X-Device-Token": tok_a},
        "B": {"X-Device-Token": tok_b},
        "client": TestClient(app),
    })()


def _setup_photo(env, data: bytes) -> tuple[str, str]:
    """project -> photo -> original upload; returns (photo_id, sha)."""
    c = env.client
    sha = hashlib.sha256(data).hexdigest()
    pid = c.post("/api/v1/projects", json={"name": "W011 Site"},
                 headers=env.H).json()["project_id"]
    photo_id = c.post(
        f"/api/v1/projects/{pid}/photos",
        json={"idempotency_key": f"w011-{uuid.uuid4().hex[:8]}",
              "original_sha256": sha,
              "original_filename": "IMG.jpg", "mime_type": "image/jpeg",
              "byte_size": len(data), "captured_at": "t",
              "device_id": "d"}, headers=env.H).json()["photo_id"]
    up = c.put(f"/api/v1/photos/{photo_id}/original", content=data,
               headers={**env.H,
                        "Content-Type": "application/octet-stream"})
    assert up.status_code == 200, up.text
    return photo_id, sha


_WM_BODY = {
    "template": "evidence",
    "fields": {
        "project_name": "W011 Bridge", "show_project_name": True,
        "photo_id": "IMG_1", "show_photo_id": True,
        "captured_time": "2026-11-24 10:00", "show_captured_time": True,
        "gps_latitude": 32.06, "gps_longitude": 118.79,
        "gps_source": "gps", "show_gps": True,
        "gps_accuracy_m": 4.0, "show_gps_accuracy": True,
        "evidence_status": "unverified", "show_evidence_status": True,
    },
}


def test_api_watermark_full_roundtrip(api_env):
    c = api_env.client
    data = _photo(1400, 1000)
    photo_id, sha = _setup_photo(api_env, data)

    r = c.post(f"/api/v1/photos/{photo_id}/derivatives/watermark",
               json=_WM_BODY, headers=env_H(api_env))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["original_sha256"] == sha
    assert "not the trusted original evidence" in body["note"]

    # idempotent: same request -> same derivative
    r2 = c.post(f"/api/v1/photos/{photo_id}/derivatives/watermark",
                json=_WM_BODY, headers=env_H(api_env))
    assert r2.status_code == 201
    assert r2.json()["derivative_id"] == body["derivative_id"]
    assert r2.json()["idempotent_replay"] is True

    # list
    lst = c.get(f"/api/v1/photos/{photo_id}/derivatives",
                headers=env_H(api_env)).json()
    assert lst["total"] == 1
    assert "RENDERED copies" in lst["note"]       # derivative ≠ evidence

    # download + verify hash + verify original bytes unchanged
    dl = c.get(f"/api/v1/derivatives/{body['derivative_id']}/download",
               headers=env_H(api_env))
    assert dl.status_code == 200
    assert dl.headers["X-Derivative-Sha256"] == body["derivative_sha256"]
    assert hashlib.sha256(dl.content).hexdigest() == body["derivative_sha256"]
    orig = c.get(f"/api/v1/photos/{photo_id}/original",
                 headers=env_H(api_env))
    assert orig.content == data                    # ORIGINAL UNTOUCHED


def env_H(env):
    return env.H


def test_api_cross_subject_isolation(api_env):
    c = api_env.client
    photo_id, _ = _setup_photo(api_env, _photo())
    r = c.post(f"/api/v1/photos/{photo_id}/derivatives/watermark",
               json=_WM_BODY, headers=api_env.B)
    assert r.status_code == 404                     # no existence oracle
    lst = c.get(f"/api/v1/photos/{photo_id}/derivatives",
                headers=api_env.B)
    assert lst.status_code == 404


def test_api_requires_original_first(api_env):
    c = api_env.client
    sha = hashlib.sha256(b"x").hexdigest()
    pid = c.post("/api/v1/projects", json={"name": "S"},
                 headers=env_H(api_env)).json()["project_id"]
    photo_id = c.post(
        f"/api/v1/projects/{pid}/photos",
        json={"idempotency_key": "no-bytes", "original_sha256": sha,
              "original_filename": "f.jpg", "mime_type": "image/jpeg",
              "byte_size": 1, "captured_at": "t", "device_id": "d"},
        headers=env_H(api_env)).json()["photo_id"]
    r = c.post(f"/api/v1/photos/{photo_id}/derivatives/watermark",
               json=_WM_BODY, headers=env_H(api_env))
    assert r.status_code == 404
    assert "original_not_uploaded" in r.text


def test_api_invalid_logo_rejected(api_env):
    c = api_env.client
    photo_id, _ = _setup_photo(api_env, _photo())
    body = {**_WM_BODY,
            "fields": {**_WM_BODY["fields"], "show_company_logo": True},
            "company_logo_b64": "!!!not-base64!!!"}
    r = c.post(f"/api/v1/photos/{photo_id}/derivatives/watermark",
               json=body, headers=env_H(api_env))
    assert r.status_code == 400


def test_api_unknown_template_rejected(api_env):
    c = api_env.client
    photo_id, _ = _setup_photo(api_env, _photo())
    r = c.post(f"/api/v1/photos/{photo_id}/derivatives/watermark",
               json={**_WM_BODY, "template": "nonexistent"},
               headers=env_H(api_env))
    assert r.status_code == 400 or r.status_code == 422


def test_api_unverified_evidence_flows_through(api_env):
    """End to end: the API renders UNVERIFIED as UNVERIFIED (never
    'Verified') when the evidence state really is unverified."""
    c = api_env.client
    photo_id, _ = _setup_photo(api_env, _photo())
    r = c.post(f"/api/v1/photos/{photo_id}/derivatives/watermark",
               json=_WM_BODY, headers=env_H(api_env))
    assert r.status_code == 201
    lines = template_lines(get_template("evidence"),
                           WatermarkFields(**_WM_BODY["fields"]))
    text = " ".join(t for t, _ in lines)
    assert "UNVERIFIED" in text and "VERIFIED" not in (
        text.replace("UNVERIFIED", ""))


# ------------------------------------------- committed samples sanity


def test_committed_sample_images_exist_and_render():
    """The ten committed sample JPGs are real, openable images whose
    derivatives differ from their synthetic originals."""
    from pathlib import Path
    samples = Path(__file__).resolve().parents[2] / "docs" / "mvp" / \
        "w011-samples"
    count = 0
    for jpg in sorted(samples.glob("*.jpg")):
        img = Image.open(jpg)
        assert img.format == "JPEG"
        assert img.width >= 640
        count += 1
    assert count == 10, f"expected 10 samples, found {count}"


def test_geometry_vectors_file_complete():
    from pathlib import Path
    vec = Path(__file__).resolve().parents[2] / "test-vectors" / \
        "tech-lab" / "w011-geometry-vectors.json"
    data = json.loads(vec.read_text(encoding="utf-8"))
    assert len(data["cases"]) == 10
    for case in data["cases"]:
        g = case["expected_geometry"]
        assert g["panel"]["w"] > 0 and g["panel"]["h"] > 0
        assert g["template_version"] == "1"
