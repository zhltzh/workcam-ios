#!/usr/bin/env python3
"""Generate REAL sample report PDFs (one per template) + a delivery
package sample, committed for reviewer inspection.

Outputs:
  docs/mvp/w012-samples/site_photo_report.pdf
  docs/mvp/w012-samples/inspection_report.pdf
  docs/mvp/w012-samples/evidence_report.pdf
  docs/mvp/w012-samples/delivery_package.zip

All inputs are SYNTHETIC (program-generated photos) - no real
photography. The PDFs are REAL rendered documents.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from PIL import Image, ImageDraw

from app.services.report_pdf import (
    PageSize, PhotoEntry, ReportMeta, ReportType, generate_report_pdf)

SAMPLES = REPO / "docs" / "mvp" / "w012-samples"


def _logo() -> bytes:
    logo = Image.new("RGB", (240, 90), (26, 53, 94))
    d = ImageDraw.Draw(logo)
    d.rectangle([6, 6, 233, 83], outline=(255, 255, 255), width=3)
    d.text((60, 30), "CMSS ENG", fill=(255, 255, 255))
    buf = io.BytesIO()
    logo.save(buf, format="PNG")
    return buf.getvalue()


def _photo(idx: int, w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), (110 + idx * 8, 140, 120))
    d = ImageDraw.Draw(img)
    d.rectangle([40 + idx * 30, 40, 200 + idx * 30, 150],
               fill=(60, 90, 160))
    d.text((50, h - 40), f"SITE PHOTO {idx:02d}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _entries(n: int, statuses=None) -> list[PhotoEntry]:
    out = []
    for i in range(n):
        w, h = ((1600, 1200) if i % 2 == 0 else (1200, 1600))
        data = _photo(i, w, h)
        status = (statuses[i] if statuses and i < len(statuses)
                  else ("unverified" if i % 3 else "cryptographically_valid"))
        out.append(PhotoEntry(
            photo_id=f"IMG_{i:03d}",
            original_sha256=hashlib.sha256(data).hexdigest(),
            derivative_sha256=hashlib.sha256(data + b"d").hexdigest()
                if i % 2 == 0 else None,
            image_bytes=data, image_width=w, image_height=h,
            captured_time=f"2026-11-24 09:{i:02d}",
            time_source="device",
            gps_latitude=32.0603, gps_longitude=118.7969,
            gps_source="gps" if i % 4 != 3 else None,
            gps_accuracy=4.0,
            notes=(f"Hairline crack near pier {i} west face; monitor "
                   "per spec" if i % 2 == 0 else None),
            evidence_status=status,
            evidence_reference=f"rec-{i:03d}",
            evidence_verified_at=f"2026-11-24 10:{i:02d}"
                if status.startswith(("cryptographically", "server"))
                else None))
    return out


def main() -> int:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    logo = _logo()
    meta_common = dict(
        project_name="Highway N-12 Widening - Section 3",
        site_address="Km 34+200, Jiangsu Province",
        operator="J. Wang (Site Engineer)",
        company_name="CMSS Engineering Co., Ltd.",
        logo_bytes=logo)

    # ---- Site Photo Report (landscape mix, 4 per page) --------------
    meta = ReportMeta(report_id="sample-site", report_number="WC-20261124-S001",
                      generated_at="2026-11-24 14:00", **meta_common)
    r = generate_report_pdf(ReportType.SITE_PHOTO, meta,
                            _entries(8), PageSize.A4, per_page=4,
                            include_appendix=False)
    out = SAMPLES / "site_photo_report.pdf"
    out.write_bytes(r["pdf_bytes"])
    print(f"site_photo_report.pdf: {r['page_count']} pages, "
          f"{len(r['pdf_bytes'])} bytes")

    # ---- Inspection Report (1 per page, Letter) ---------------------
    meta = ReportMeta(report_id="sample-insp", report_number="WC-20261124-I001",
                      generated_at="2026-11-24 14:00", **meta_common)
    r = generate_report_pdf(ReportType.INSPECTION, meta,
                            _entries(4), PageSize.LETTER, per_page=1,
                            include_appendix=False)
    out = SAMPLES / "inspection_report.pdf"
    out.write_bytes(r["pdf_bytes"])
    print(f"inspection_report.pdf: {r['page_count']} pages, "
          f"{len(r['pdf_bytes'])} bytes")

    # ---- Evidence Report (2 per page, appendix ON, mixed states) ----
    meta = ReportMeta(report_id="sample-ev", report_number="WC-20261124-E001",
                      generated_at="2026-11-24 14:00", **meta_common,
                      include_evidence_appendix=True)
    statuses = ["cryptographically_valid", "unverified",
                "verification_failed", "signature_valid",
                "server_anchored", "no_evidence",
                "cryptographically_valid", "unverified"]
    r = generate_report_pdf(ReportType.EVIDENCE, meta,
                            _entries(8, statuses), PageSize.A4,
                            per_page=2, include_appendix=True)
    out = SAMPLES / "evidence_report.pdf"
    out.write_bytes(r["pdf_bytes"])
    print(f"evidence_report.pdf: {r['page_count']} pages, "
          f"{len(r['pdf_bytes'])} bytes")

    # ---- delivery package sample ------------------------------------
    import zipfile
    entries = _entries(4)
    ev = entries[:2]
    buf = io.BytesIO()
    manifest = {"report_number": "WC-20261124-E001", "files": []}

    def add(zf, name, data):
        zf.writestr(name, data)
        manifest["files"].append({"path": name,
                                  "sha256": hashlib.sha256(data).hexdigest(),
                                  "bytes": len(data)})

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        add(zf, "report.pdf", r["pdf_bytes"])
        for e in ev:
            add(zf, f"originals/{e.photo_id}.jpg", e.image_bytes)
            add(zf, f"derivatives/{e.photo_id}.jpg", e.image_bytes)
            add(zf, f"evidence/{e.photo_id}.json",
                json.dumps({"photo_id": e.photo_id,
                            "original_sha256": e.original_sha256,
                            "evidence_status": e.evidence_status},
                           indent=2).encode())
        add(zf, "manifest.json",
            json.dumps(manifest, indent=2).encode())
        add(zf, "verification-guide.txt",
            b"WORKCAM DELIVERY PACKAGE - VERIFICATION GUIDE\n"
            b"See manifest.json for per-file SHA-256.\n"
            b"This package is NOT a legal certification.\n")
    out = SAMPLES / "delivery_package.zip"
    out.write_bytes(buf.getvalue())
    print(f"delivery_package.zip: {buf.tell()} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
