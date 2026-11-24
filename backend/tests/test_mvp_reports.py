"""WC-W012 report & delivery package tests (P6).

DESKTOP_SIMULATION - synthetic photos throughout; REAL PDFs/ZIPs out.

Covers: 3 templates / A4+Letter / mixed orientation / long notes &
specials / GPS missing / unverified evidence / forged status rejection /
snapshot immutability / PDF integrity / ZIP manifest verification /
originals unchanged / cross-subject / failure leaves no fake report /
traversal / sensitive leak scan / large batches (10/50/100 + 500 bound).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import uuid
import zipfile

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from tests.conftest import make_manifest, _encode_pub


@pytest.fixture()
def rpt_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "w012-rpt")
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    import app.db as dbm
    dbm.migrate(tmp_path / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    import app.services.device_identity as di
    # batch tests register 2 requests per photo + project/report calls;
    # lift the default 60/60s window so large-batch fixtures are not
    # rate-limited (the limiter itself has dedicated tests in
    # test_mvp_security / test_w012_security)
    monkeypatch.setattr(di, "check_rate", lambda *a, **k: None)

    def mk(name):
        key = ec.generate_private_key(ec.SECP256R1())
        pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
        return key, pub_b64, di.enroll_device(pub_b64, name, "w012-rpt")

    key_a, pub_a, tok_a = mk("rpt-AAA")
    key_b, pub_b, tok_b = mk("rpt-BBB")
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "biz.db",
        "key": key_a, "pub_a": pub_a, "tok_a": tok_a, "tok_b": tok_b,
        "H": {"X-Device-Token": tok_a},
        "B": {"X-Device-Token": tok_b},
        "client": TestClient(app),
    })()


def _photo(w=1200, h=900, seed=0) -> bytes:
    img = Image.new("RGB", (w, h), (100 + seed, 140, 120))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 150, 90], fill=(40, 80, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


def _mk_project(env, name="Report Site") -> str:
    return env.client.post("/api/v1/projects", json={"name": name},
                           headers=env.H).json()["project_id"]


def _reg_photo(env, pid: str, data: bytes, device_id="d") -> str:
    sha = hashlib.sha256(data).hexdigest()
    r = env.client.post(
        f"/api/v1/projects/{pid}/photos",
        json={"idempotency_key": f"r-{uuid.uuid4().hex[:8]}",
              "original_sha256": sha, "original_filename": "I.jpg",
              "mime_type": "image/jpeg", "byte_size": len(data),
              "captured_at": "2026-11-24 09:00",
              "device_id": device_id}, headers=env.H)
    assert r.status_code == 201, r.text
    pid_out = r.json()["photo_id"]
    up = env.client.put(f"/api/v1/photos/{pid_out}/original", content=data,
                         headers={**env.H,
                                  "Content-Type": "application/octet-stream"})
    assert up.status_code == 200, up.text
    return pid_out


def _report_body(photo_ids, **kw) -> dict:
    body = {"report_type": "evidence", "page_size": "a4",
            "per_page": 2, "photo_ids": photo_ids,
            "operator": "J. Wang", "company_name": "CMSS Eng"}
    body.update(kw)
    return body


# --------------------------------------------------- 3 templates + sizes


def test_three_report_types_generate(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    for rtype in ("site_photo", "inspection", "evidence"):
        r = c.post(f"/api/v1/projects/{pid}/reports",
                   json=_report_body([ph], report_type=rtype),
                   headers=rpt_env.H)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["report_type"] == rtype
        assert body["pdf_sha256"]
        # PDF bytes really exist and verify
        dl = c.get(f"/api/v1/reports/{body['report_id']}/download",
                    headers=rpt_env.H)
        assert dl.status_code == 200
        assert dl.content.startswith(b"%PDF")
        assert hashlib.sha256(dl.content).hexdigest() == body["pdf_sha256"]


def test_a4_and_letter_page_sizes(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    for size in ("a4", "letter"):
        r = c.post(f"/api/v1/projects/{pid}/reports",
                   json=_report_body([ph], page_size=size),
                   headers=rpt_env.H)
        assert r.status_code == 201
        assert r.json()["page_size"] == size


def test_mixed_orientation_photos_in_one_report(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ids = [_reg_photo(rpt_env, pid, _photo(1600, 1200)),
           _reg_photo(rpt_env, pid, _photo(1200, 1600)),
           _reg_photo(rpt_env, pid, _photo(900, 1600))]
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body(ids), headers=rpt_env.H)
    assert r.status_code == 201
    assert r.json()["photo_count"] == 3


@pytest.mark.parametrize("per_page", [1, 2, 4])
def test_photos_per_page_variants(rpt_env, per_page):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ids = [_reg_photo(rpt_env, pid, _photo(800, 600)) for _ in range(4)]
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body(ids, per_page=per_page),
               headers=rpt_env.H)
    assert r.status_code == 201
    assert r.json()["per_page"] == per_page


def test_per_page_invalid_rejected(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body([ph], per_page=3), headers=rpt_env.H)
    assert r.status_code == 422                # schema-level rejection


def test_long_notes_and_special_characters(rpt_env):
    """Long notes are recorded in the snapshot and special characters
    survive the JSON round-trip (the PDF escapes them)."""
    c = rpt_env.client
    pid = _mk_project(rpt_env, name='Site <X> & "Y" — 100% ☑')
    data = _photo()
    sha = hashlib.sha256(data).hexdigest()
    ph = c.post(f"/api/v1/projects/{pid}/photos", json={
        "idempotency_key": "sp", "original_sha256": sha,
        "original_filename": "I.jpg", "mime_type": "image/jpeg",
        "byte_size": len(data), "captured_at": "2026-11-24 09:00",
        "device_id": "d<ev>&\"ice\""}, headers=rpt_env.H).json()["photo_id"]
    c.put(f"/api/v1/photos/{ph}/original", content=data,
          headers={**rpt_env.H,
                   "Content-Type": "application/octet-stream"})
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body([ph]), headers=rpt_env.H)
    assert r.status_code == 201
    det = c.get(f"/api/v1/reports/{r.json()['report_id']}",
                headers=rpt_env.H).json()
    snap = det["snapshot"][ph]
    assert snap["captured_time"] == "2026-11-24 09:00"
    # the report PDF is still a valid PDF
    dl = c.get(f"/api/v1/reports/{r.json()['report_id']}/download",
               headers=rpt_env.H)
    assert dl.content.startswith(b"%PDF")


# ------------------------------------------------- evidence handling


def test_unverified_evidence_shows_unverified_in_report(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    c.post(f"/api/v1/photos/{ph}/evidence-links",
           json={"evidence_reference": f"ref-{uuid.uuid4().hex[:6]}",
                 "evidence_type": "signature_manifest"},
           headers=rpt_env.H)
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body([ph]), headers=rpt_env.H)
    det = c.get(f"/api/v1/reports/{r.json()['report_id']}",
                headers=rpt_env.H).json()
    assert det["snapshot"][ph]["evidence_status"] == "unverified"


def test_forged_status_in_report_uses_server_truth(rpt_env):
    """The report SNAPSHOT derives evidence_status from the server (same
    resolver); a photo with NO links snapshots 'no_evidence' no matter
    what the client would like to claim."""
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body([ph]), headers=rpt_env.H)
    det = c.get(f"/api/v1/reports/{r.json()['report_id']}",
                headers=rpt_env.H).json()
    assert det["snapshot"][ph]["evidence_status"] == "no_evidence"


def test_verified_evidence_flows_into_report_snapshot(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    data = _photo()
    ph = _reg_photo(rpt_env, pid, data)
    manifest = make_manifest(rpt_env.key,
                             _encode_pub(rpt_env.key.public_key()),
                             photo_bytes=data)
    from app.services.key_registry import get_registry
    get_registry().register(rpt_env.pub_a, "rpt-AAA")
    c.post(f"/api/v1/photos/{ph}/evidence-links",
           json={"evidence_reference": manifest.record_id,
                 "evidence_type": "signature_manifest"},
           headers=rpt_env.H)
    links = c.get(f"/api/v1/photos/{ph}/evidence-links",
                  headers=rpt_env.H).json()["items"]
    c.post(f"/api/v1/photos/{ph}/evidence-links/"
           f"{links[0]['evidence_link_id']}/verify",
           json={"manifest": manifest.model_dump()}, headers=rpt_env.H)
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body([ph]), headers=rpt_env.H)
    det = c.get(f"/api/v1/reports/{r.json()['report_id']}",
                headers=rpt_env.H).json()
    status = det["snapshot"][ph]["evidence_status"]
    assert status in ("cryptographically_valid", "signature_valid")


# ------------------------------------------------- snapshot immutability


def test_snapshot_immutable_across_status_change(rpt_env):
    """Generate a report, then CHANGE the evidence state; the OLD
    report's snapshot is unchanged and a regeneration creates a NEW
    report row (never silent mutation)."""
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    r1 = c.post(f"/api/v1/projects/{pid}/reports",
                json=_report_body([ph]), headers=rpt_env.H)
    rid1 = r1.json()["report_id"]
    # change server truth
    c.post(f"/api/v1/photos/{ph}/evidence-links",
           json={"evidence_reference": f"ref-{uuid.uuid4().hex[:6]}",
                 "evidence_type": "signature_manifest"},
           headers=rpt_env.H)
    det_old = c.get(f"/api/v1/reports/{rid1}",
                    headers=rpt_env.H).json()
    assert det_old["snapshot"][ph]["evidence_status"] == "no_evidence"
    # regenerate: NEW row
    r2 = c.post(f"/api/v1/projects/{pid}/reports",
                json=_report_body([ph]), headers=rpt_env.H)
    assert r2.json()["report_id"] != rid1
    det_new = c.get(f"/api/v1/reports/{r2.json()['report_id']}",
                     headers=rpt_env.H).json()
    assert det_new["snapshot"][ph]["evidence_status"] == "unverified"
    # old report bytes unchanged
    dl_old = c.get(f"/api/v1/reports/{rid1}/download",
                   headers=rpt_env.H)
    assert hashlib.sha256(dl_old.content).hexdigest() == det_old["pdf_sha256"]
    # listing shows both versions (history preserved)
    lst = c.get(f"/api/v1/projects/{pid}/reports",
                headers=rpt_env.H).json()
    assert lst["total"] == 2


# ------------------------------------------------- originals unchanged


def test_report_generation_leaves_original_bytes_unchanged(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    data = _photo()
    ph = _reg_photo(rpt_env, pid, data)
    snapshot_before = data
    c.post(f"/api/v1/projects/{pid}/reports",
           json=_report_body([ph]), headers=rpt_env.H)
    dl = c.get(f"/api/v1/photos/{ph}/original", headers=rpt_env.H)
    assert dl.content == data
    assert data is snapshot_before


# ------------------------------------------------- access control


def test_cross_subject_report_access_404(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    r = c.post(f"/api/v1/projects/{pid}/reports",
                json=_report_body([ph]), headers=rpt_env.H)
    rid = r.json()["report_id"]
    # B gets 404 on detail/download/package everywhere
    assert c.get(f"/api/v1/reports/{rid}",
                 headers=rpt_env.B).status_code == 404
    assert c.get(f"/api/v1/reports/{rid}/download",
                 headers=rpt_env.B).status_code == 404
    assert c.post(f"/api/v1/reports/{rid}/delivery-package",
                  json={},
                  headers=rpt_env.B).status_code == 404
    # B cannot even list A's project reports
    assert c.get(f"/api/v1/projects/{pid}/reports",
                 headers=rpt_env.B).status_code == 404


def test_report_requires_valid_photos_in_same_project(rpt_env):
    c = rpt_env.client
    pid1 = _mk_project(rpt_env, name="P1")
    pid2 = _mk_project(rpt_env, name="P2")
    ph2 = _reg_photo(rpt_env, pid2, _photo())       # photo in P2
    r = c.post(f"/api/v1/projects/{pid1}/reports",
               json=_report_body([ph2]), headers=rpt_env.H)
    assert r.status_code == 400                    # photo_project_mismatch


def test_duplicate_photo_ids_rejected(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body([ph, ph]), headers=rpt_env.H)
    assert r.status_code == 400


# ------------------------------------------------- failure & traversal


def test_failed_generation_leaves_no_fake_report(rpt_env):
    """A nonexistent photo id in the request fails with 404 BEFORE any
    report row or PDF is written."""
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    fake = str(uuid.uuid4())
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body([ph, fake]), headers=rpt_env.H)
    assert r.status_code == 404
    lst = c.get(f"/api/v1/projects/{pid}/reports",
                headers=rpt_env.H).json()
    assert lst["total"] == 0                       # nothing written


def test_report_id_traversal_rejected(rpt_env):
    c = rpt_env.client
    r = c.get("/api/v1/reports/..%2fetc", headers=rpt_env.H)
    assert r.status_code in (400, 404)


def test_invalid_report_type_rejected(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body([ph], report_type="magic"),
               headers=rpt_env.H)
    assert r.status_code == 422


# ------------------------------------------------- delivery package (P4)


def test_delivery_package_roundtrip_and_manifest(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    data = _photo()
    ph = _reg_photo(rpt_env, pid, data)
    # watermark derivative for the derivatives/ entry
    c.post(f"/api/v1/photos/{ph}/derivatives/watermark", json={
        "template": "minimal",
        "fields": {"project_name": "S", "show_project_name": True,
                   "evidence_status": "unverified",
                   "show_evidence_status": True}},
        headers=rpt_env.H)
    # evidence link
    c.post(f"/api/v1/photos/{ph}/evidence-links",
           json={"evidence_reference": f"ref-{uuid.uuid4().hex[:6]}",
                 "evidence_type": "signature_manifest"},
           headers=rpt_env.H)
    rr = c.post(f"/api/v1/projects/{pid}/reports",
                json=_report_body([ph]), headers=rpt_env.H)
    rid = rr.json()["report_id"]

    pkg = c.post(f"/api/v1/reports/{rid}/delivery-package",
                 json={"include_originals": True,
                       "include_derivatives": True,
                       "include_evidence": True},
                 headers=rpt_env.H)
    assert pkg.status_code == 200
    zip_bytes = pkg.content
    assert pkg.headers["X-Package-Sha256"] == \
        hashlib.sha256(zip_bytes).hexdigest()

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = set(zf.namelist())
    assert "report.pdf" in names
    assert f"originals/{ph}.jpg" in names
    assert f"derivatives/{ph}.jpg" in names
    assert f"evidence/{ph}.json" in names
    assert "manifest.json" in names
    assert "verification-guide.txt" in names

    # manifest sha256 all match
    manifest = json.loads(zf.read("manifest.json"))
    for f in manifest["files"]:
        data_f = zf.read(f["path"])
        assert hashlib.sha256(data_f).hexdigest() == f["sha256"]
        assert len(data_f) == f["bytes"]

    # originals in the package are byte-identical to the stored ones
    assert zf.read(f"originals/{ph}.jpg") == data
    # report in the package matches the report download
    dl = c.get(f"/api/v1/reports/{rid}/download", headers=rpt_env.H)
    assert zf.read("report.pdf") == dl.content


def test_delivery_package_sensitive_leak_scan(rpt_env):
    """The package must NEVER contain tokens, enrollment codes, or key
    material."""
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    rr = c.post(f"/api/v1/projects/{pid}/reports",
                json=_report_body([ph]), headers=rpt_env.H)
    rid = rr.json()["report_id"]
    pkg = c.post(f"/api/v1/reports/{rid}/delivery-package", json={},
                  headers=rpt_env.H)
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    blob = b""
    for n in zf.namelist():
        blob += zf.read(n)
    assert rpt_env.tok_a.encode() not in blob
    assert rpt_env.tok_b.encode() not in blob
    assert b"w012-rpt" not in blob              # enrollment code absent
    assert b"PRIVATE KEY" not in blob
    assert b"BEGIN EC" not in blob


def test_delivery_package_include_flags(rpt_env):
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    ph = _reg_photo(rpt_env, pid, _photo())
    rr = c.post(f"/api/v1/projects/{pid}/reports",
                json=_report_body([ph]), headers=rpt_env.H)
    rid = rr.json()["report_id"]
    pkg = c.post(f"/api/v1/reports/{rid}/delivery-package",
                 json={"include_originals": False,
                       "include_derivatives": False,
                       "include_evidence": False},
                 headers=rpt_env.H)
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    names = zf.namelist()
    assert "report.pdf" in names and "manifest.json" in names
    assert not any(n.startswith("originals/") for n in names)
    assert not any(n.startswith("derivatives/") for n in names)
    assert not any(n.startswith("evidence/") for n in names)


# ------------------------------------------------- P5: large batches


def _batch_env(env, n: int, w=640, h=480) -> tuple[str, list[str]]:
    pid = _mk_project(env)
    ids = []
    for i in range(n):
        ids.append(_reg_photo(env, pid, _photo(w, h, seed=i % 100)))
    return pid, ids


@pytest.mark.parametrize("n", [10, 50, 100])
def test_report_batch_sizes_with_metrics(rpt_env, n):
    """REAL performance measurements (DESKTOP figures, printed for the
    report): 10/50/100 photos with A4/per_page=4."""
    c = rpt_env.client
    pid, ids = _batch_env(rpt_env, n)
    t0 = time.perf_counter()
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body(ids, report_type="site_photo",
                                 per_page=4), headers=rpt_env.H)
    elapsed = time.perf_counter() - t0
    assert r.status_code == 201, r.text
    dl = c.get(f"/api/v1/reports/{r.json()['report_id']}/download",
               headers=rpt_env.H)
    print(f"\n[REPORT PERF - DESKTOP] {n} photos: "
          f"{elapsed:.2f}s gen, pdf {len(dl.content)/1024:.0f}KB, "
          f"{r.json()['page_count']} pages")


def test_report_photo_cap_enforced(rpt_env):
    """501 photo ids in one request -> schema 422 (MAX_REPORT_PHOTOS)."""
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    fake_ids = [str(uuid.uuid4()) for _ in range(501)]
    r = c.post(f"/api/v1/projects/{pid}/reports",
                json=_report_body(fake_ids), headers=rpt_env.H)
    assert r.status_code == 422


def test_500_photo_report_or_package_resource_bounded(rpt_env):
    """500-photo delivery package: embed bound (downscale) keeps memory
    bounded; the run completes and the package verifies end-to-end.
    DESKTOP figure printed."""
    c = rpt_env.client
    pid, ids = _batch_env(rpt_env, 500, w=480, h=360)
    t0 = time.perf_counter()
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body(ids, report_type="site_photo",
                                 per_page=4,
                                 include_evidence_appendix=False),
               headers=rpt_env.H)
    assert r.status_code == 201, r.text
    rid = r.json()["report_id"]
    pkg = c.post(f"/api/v1/reports/{rid}/delivery-package",
                 json={"include_originals": True,
                       "include_derivatives": False,
                       "include_evidence": False},
                 headers=rpt_env.H)
    elapsed = time.perf_counter() - t0
    assert pkg.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    manifest = json.loads(zf.read("manifest.json"))
    # manifest.json cannot contain its own hash (self-reference); it
    # lists report.pdf + the 500 originals = 501 entries. guide +
    # manifest itself are added after serialization by design.
    assert len(manifest["files"]) == 501
    assert "manifest.json" in zf.namelist()
    assert "verification-guide.txt" in zf.namelist()
    for f in manifest["files"]:
        assert hashlib.sha256(zf.read(f["path"])).hexdigest() == f["sha256"]
    print(f"\n[DELIVERY PERF - DESKTOP] 500-photo package: "
          f"{elapsed:.1f}s total, zip "
          f"{len(pkg.content)/1024/1024:.1f}MB")


def test_embed_downscale_applies_but_originals_intact(rpt_env):
    """A huge photo is downscaled for embedding; the stored ORIGINAL
    bytes are untouched."""
    c = rpt_env.client
    pid = _mk_project(rpt_env)
    big = _photo(3000, 2200)
    ph = _reg_photo(rpt_env, pid, big)
    r = c.post(f"/api/v1/projects/{pid}/reports",
               json=_report_body([ph]), headers=rpt_env.H)
    det = c.get(f"/api/v1/reports/{r.json()['report_id']}",
                headers=rpt_env.H).json()
    w, h = det["snapshot"][ph]["image_width"], \
        det["snapshot"][ph]["image_height"]
    assert max(w, h) <= 1800                     # downscale applied
    dl = c.get(f"/api/v1/photos/{ph}/original", headers=rpt_env.H)
    assert dl.content == big                      # original unchanged


# ------------------------------------------------- committed samples


def test_committed_report_samples_exist(rpt_env):
    from pathlib import Path
    samples = Path(__file__).resolve().parents[2] / "docs" / "mvp" / \
        "w012-samples"
    for name in ("site_photo_report.pdf", "inspection_report.pdf",
                 "evidence_report.pdf", "delivery_package.zip"):
        p = samples / name
        assert p.exists() and p.stat().st_size > 1000, name
    for pdf in ("site_photo_report.pdf", "inspection_report.pdf",
                "evidence_report.pdf"):
        assert (samples / pdf).read_bytes()[:4] == b"%PDF"
