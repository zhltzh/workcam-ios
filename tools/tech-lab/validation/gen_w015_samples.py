#!/usr/bin/env python3
"""Generate B02-W015 sample inspection reports (REAL runs, not handcrafted).

Outputs (synthetic desktop inputs, REAL PDFs/ZIPs produced by the live
backend stack):
  docs/mvp/w015-samples/inspection_full_a4.pdf            via full API chain
  docs/mvp/w015-samples/inspection_full_letter.pdf        via full API chain
  docs/mvp/w015-samples/inspection_open_items_a4.pdf      via full API chain
  docs/mvp/w015-samples/evidence_report_with_issues_a4.pdf via full API chain
  docs/mvp/w015-samples/site_photo_letter.pdf              via full API chain
  docs/mvp/w015-samples/inspection_grid4_a4.pdf            direct engine
  docs/mvp/w015-samples/inspection_register8_a4.pdf        direct engine
  docs/mvp/w015-samples/delivery_package_full.zip         via full API chain

Run:  python tools/tech-lab/validation/gen_w015_samples.py
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

SAMPLES = REPO / "docs" / "mvp" / "w015-samples"


def _photo(w=1200, h=900, seed=0) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), (100 + seed, 140, 120))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 240, 160], fill=(40, 80, 160))
    d.ellipse([300, 200, 420, 320], fill=(180, 60, 40))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _api_client():
    """Boot the full FastAPI stack on a throwaway SQLite DB."""
    tmpdir = tempfile.mkdtemp(prefix="w015-samples-")
    os.environ["WORKCAM_BUSINESS_DB"] = str(Path(tmpdir) / "biz.db")
    os.environ["WORKCAM_ENROLLMENT_CODE"] = "w015-samples"
    os.environ.pop("WORKCAM_UPLOAD_EXPERIMENTAL", None)
    os.environ.pop("WORKCAM_UPLOAD_AUTH", None)
    import app.db as dbm
    dbm.migrate(Path(tmpdir) / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    import app.services.device_identity as di
    di.check_rate = lambda *a, **k: None
    from cryptography.hazmat.primitives.asymmetric import ec
    from tests.conftest import _encode_pub
    from fastapi.testclient import TestClient
    from app.main import app as fastapp

    key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
    tok = di.enroll_device(pub_b64, "w015-sampler", "w015-samples")
    return TestClient(fastapp), {"X-Device-Token": tok}, tmpdir


def _setup_site(c, H, n_photos=4):
    pid = c.post("/api/v1/projects", json={
        "name": "Highway N-12 Widening - Section 3",
        "site_address": "Km 34+200, Jiangsu"},
        headers=H).json()["project_id"]
    photo_ids = []
    for i in range(n_photos):
        data = _photo(seed=i * 25)
        ph = c.post(
            f"/api/v1/projects/{pid}/photos",
            json={"idempotency_key": f"s-{uuid.uuid4().hex[:8]}",
                  "original_sha256": hashlib.sha256(data).hexdigest(),
                  "original_filename": f"IMG_{i}.jpg",
                  "mime_type": "image/jpeg", "byte_size": len(data),
                  "captured_at": f"2026-11-25 09:{i:02d}",
                  "device_id": "iPhone15,3"}, headers=H).json()["photo_id"]
        c.put(f"/api/v1/photos/{ph}/original", content=data,
               headers={**H, "Content-Type": "application/octet-stream"})
        photo_ids.append(ph)
    return pid, photo_ids


def _issue(c, H, pid, **kw) -> str:
    body = {"title": "Issue", "severity": "low"}
    body.update(kw)
    return c.post(f"/api/v1/projects/{pid}/issues", json=body,
                   headers=H).json()["issue_id"]


def _advance(c, H, iid, states, start_rev=1):
    rev = start_rev
    for st in states:
        c.patch(f"/api/v1/issues/{iid}",
                 json={"status": st, "expected_revision": rev},
                 headers=H)
        rev += 1


def _save(rr, name, c, H):
    dl = c.get(f"/api/v1/reports/{rr['report_id']}/download", headers=H)
    out = SAMPLES / name
    out.write_bytes(dl.content)
    print(f"{name}: {rr['page_count']} pages, "
          f"{rr['issue_count']} issues, {len(dl.content)} bytes")
    return rr


def main() -> int:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    c, H, tmpdir = _api_client()

    # ==== site with 4 photos + 3 issues in mixed states ====
    pid, photos = _setup_site(c, H, 4)
    i1 = _issue(c, H, pid,
                title="Hairline crack at pier 2 west face",
                description="Vertical hairline crack approx 30cm long, "
                           "starting 1m below the deck joint.",
                severity="high",
                location_description="Km 34+200, Pier 2, West face",
                assigned_to="J. Wang",
                remediation_note="Epoxy inject per spec SS-7; monitor "
                                 "crack gauge for 7 days; re-inspect.")
    i2 = _issue(c, H, pid,
                title="Concrete spalling on girder B-3",
                description="Surface spalling with exposed rebar, "
                            "approx 15cm x 10cm area.",
                severity="critical",
                location_description="Km 34+300, Girder B-3",
                assigned_to="L. Chen",
                remediation_note="Chip to sound concrete, patch with "
                                 "repair mortar R2, cure 48h.")
    i3 = _issue(c, H, pid,
                title="Drainage scupper blocked at deck 5",
                severity="medium",
                location_description="Km 34+400, deck drain 5",
                assigned_to="M. Ruiz")
    c.post(f"/api/v1/issues/{i1}/photos",
           json={"photo_id": photos[0], "role": "before"}, headers=H)
    c.post(f"/api/v1/issues/{i2}/photos",
           json={"photo_id": photos[1], "role": "before"}, headers=H)
    c.post(f"/api/v1/issues/{i2}/photos",
           json={"photo_id": photos[2], "role": "after"}, headers=H)
    _advance(c, H, i1, ["in_progress"])
    _advance(c, H, i2, ["in_progress", "resolved"])

    meta_block = {
        "operator": "J. Wang (Site Engineer)",
        "company_name": "CMSS Engineering Co., Ltd.",
        "inspector_name": "L. Chen",
        "inspector_title": "Site QC Engineer",
        "project_metadata": {
            "Client": "Provincial Highway Administration",
            "Contract No.": "NH-2026-0342",
            "Section": "Km 34+000 to 35+500",
            "Weather at inspection": "Clear, 14 C",
        },
    }
    lidar_refs = [
        {"record_id": "B02-W016-raycast-normal-2m-R1",
         "description": "Crack zone standoff offset reference",
         "related_issue_id": i1},
        {"record_id": "B02-W016-scene_depth-glass-1m-R2",
         "description": "Glass parapet panel distance test row"},
    ]

    body = {
        "report_type": "inspection", "page_size": "a4", "per_page": 2,
        "photo_ids": photos, "issue_ids": [i1, i2, i3],
        "lidar_measurements": lidar_refs,
        **meta_block,
    }
    rr1 = c.post(f"/api/v1/projects/{pid}/reports", json=body,
                  headers=H).json()
    _save(rr1, "inspection_full_a4.pdf", c, H)

    rr2 = c.post(f"/api/v1/projects/{pid}/reports", json={
        **body, "page_size": "letter"}, headers=H).json()
    _save(rr2, "inspection_full_letter.pdf", c, H)

    # delivery package for the full A4 report
    pkg = c.post(f"/api/v1/reports/{rr1['report_id']}/delivery-package",
                  json={"include_originals": True,
                        "include_derivatives": False,
                        "include_evidence": True}, headers=H)
    (SAMPLES / "delivery_package_full.zip").write_bytes(pkg.content)
    print(f"delivery_package_full.zip: {len(pkg.content)} bytes, "
          f"{pkg.headers['X-File-Count']} files")

    # ==== evidence template with appendix + issues ====
    rr3 = c.post(f"/api/v1/projects/{pid}/reports", json={
        "report_type": "evidence", "page_size": "a4", "per_page": 2,
        "photo_ids": photos, "issue_ids": [i1, i2, i3],
        "include_evidence_appendix": True,
        "lidar_measurements": lidar_refs,
        **meta_block}, headers=H).json()
    _save(rr3, "evidence_report_with_issues_a4.pdf", c, H)

    # ==== open-items register: everything still open / in-progress ====
    pid2, photos2 = _setup_site(c, H, 2)
    for s, t in (("low", "Formwork misaligned at abutment A1"),
                 ("medium", "Rebar cover below spec on pier cap"),
                 ("high", "Unshored excavation near footing"),
                 ("critical", "Temporary railing missing above 6m drop")):
        _issue(c, H, pid2, title=t, severity=s)
    rr4 = c.post(f"/api/v1/projects/{pid2}/reports", json={
        "report_type": "inspection", "page_size": "a4", "per_page": 2,
        "photo_ids": photos2,
        "issue_ids": [r["issue_id"] for r in c.get(
            f"/api/v1/projects/{pid2}/issues", headers=H
        ).json()["items"]],
        **meta_block}, headers=H).json()
    _save(rr4, "inspection_open_items_a4.pdf", c, H)

    # ==== site photo report, US Letter, no issues ====
    rr5 = c.post(f"/api/v1/projects/{pid}/reports", json={
        "report_type": "site_photo", "page_size": "letter",
        "per_page": 4, "photo_ids": photos, **meta_block},
        headers=H).json()
    _save(rr5, "site_photo_letter.pdf", c, H)

    # ==== direct engine: dense grid + register-only variants ====
    from app.services.report_pdf import (
        PageSize, PhotoEntry, ReportMeta, ReportType,
        generate_report_pdf)

    def _entries(n):
        out = []
        for i in range(n):
            data = _photo(seed=i * 20)
            out.append(PhotoEntry(
                photo_id=f"IMG_{i:03d}",
                original_sha256=hashlib.sha256(data).hexdigest(),
                derivative_sha256=None, image_bytes=data,
                image_width=1200, image_height=900,
                captured_time=f"2026-11-25 09:{i:02d}",
                time_source="device",
                gps_latitude=32.0603, gps_longitude=118.7969,
                gps_source="gps", gps_accuracy=4.0,
                notes=None,
                evidence_status="cryptographically_valid" if i % 2 else
                "unverified",
                evidence_reference=f"rec-{i:03d}" if i % 2 else None,
                evidence_verified_at=None))
        return out

    issues8 = []
    for n, (sev, st) in enumerate([
            ("low", "open"), ("medium", "open"), ("high", "open"),
            ("critical", "open"), ("medium", "in_progress"),
            ("high", "in_progress"), ("critical", "resolved"),
            ("low", "resolved")]):
        issues8.append({
            "issue_id": f"eng-iss-{n}",
            "title": f"Register sample issue {n + 1}",
            "description": "Synthetic register row for layout sampling.",
            "severity": sev, "status": st,
            "location_description": f"Km 34+{n * 10}",
            "assigned_to": "Crew A" if n % 2 else "Crew B",
            "remediation_note": "Rework per approved method statement." if n % 3 == 0 else "",
            "created_at": "2026-11-20 08:00",
            "updated_at": "2026-11-25 10:00",
            "resolved_at": "2026-11-24 17:00" if st == "resolved" else None,
            "history": [{"action": "created", "created_at": "2026-11-20 08:00"}],
            "photos": [],
        })

    meta6 = ReportMeta(
        report_id="s6", report_number="WC-20261125-S06",
        project_name="Highway N-12 Widening - Section 3",
        site_address="Km 34+200, Jiangsu",
        generated_at="2026-11-25 15:00",
        operator="J. Wang", company_name="CMSS Engineering",
        inspector_name="L. Chen", inspector_title="Site QC Engineer",
        project_metadata={"Client": "Provincial Highway Administration"})
    r6 = generate_report_pdf(ReportType.INSPECTION, meta6, _entries(4),
                              PageSize.A4, per_page=4)
    (SAMPLES / "inspection_grid4_a4.pdf").write_bytes(r6["pdf_bytes"])
    print(f"inspection_grid4_a4.pdf: {r6['page_count']} pages")

    meta7 = ReportMeta(
        report_id="s7", report_number="WC-20261125-S07",
        project_name="Highway N-12 Widening - Section 3",
        site_address="Km 34+200, Jiangsu",
        generated_at="2026-11-25 16:00",
        operator="J. Wang", company_name="CMSS Engineering",
        inspector_name="L. Chen", inspector_title="Site QC Engineer",
        project_metadata={"Client": "Provincial Highway Administration",
                          "Contract No.": "NH-2026-0342"},
        issue_snapshot=issues8,
        lidar_measurements=[
            {"record_id": "B02-W016-raycast-normal-1m-R3",
             "description": "Deck underside clearance spot row"},
            {"record_id": "B02-W016-smoothed_depth-low_light-3m-R1",
             "description": "Night lighting condition repeatability row"},
        ])
    r7 = generate_report_pdf(ReportType.INSPECTION, meta7, _entries(2),
                              PageSize.A4, per_page=2)
    (SAMPLES / "inspection_register8_a4.pdf").write_bytes(r7["pdf_bytes"])
    print(f"inspection_register8_a4.pdf: {r7['page_count']} pages")

    print(f"\nsamples written to {SAMPLES}")
    print(f"api scratch dir (not committed): {tmpdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
