#!/usr/bin/env python3
"""Generate W013-R1 sample Inspection Report PDFs with issues + snapshots.

Outputs (all SYNTHETIC inputs, REAL PDFs):
  docs/mvp/w013-samples/inspection_report_with_issues.pdf
  docs/mvp/w013-samples/evidence_report_full.pdf
"""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from PIL import Image, ImageDraw

from app.services.report_pdf import (
    PageSize, PhotoEntry, ReportMeta, ReportType, generate_report_pdf)

SAMPLES = REPO / "docs" / "mvp" / "w013-samples"


def _photo(w=1200, h=900, seed=0) -> bytes:
    img = Image.new("RGB", (w, h), (100 + seed, 140, 120))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 200, 150], fill=(40, 80, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _entries(n=4) -> list[PhotoEntry]:
    out = []
    for i in range(n):
        data = _photo(seed=i * 20)
        out.append(PhotoEntry(
            photo_id=f"IMG_{i:03d}",
            original_sha256=hashlib.sha256(data).hexdigest(),
            derivative_sha256=None, image_bytes=data,
            image_width=1200, image_height=900,
            captured_time=f"2026-11-24 09:{i:02d}",
            time_source="device",
            gps_latitude=32.0603, gps_longitude=118.7969,
            gps_source="gps" if i % 2 == 0 else None,
            gps_accuracy=4.0,
            notes=f"Pier {i} west face inspection" if i % 2 == 0 else None,
            evidence_status="unverified" if i % 3 else
                "cryptographically_valid",
            evidence_reference=f"rec-{i:03d}" if i % 3 == 0 else None,
            evidence_verified_at=None))
    return out


ISSUE_SNAPSHOT = [
    {
        "issue_id": "iss-001",
        "title": "Hairline crack at pier 2 west face",
        "description": "Vertical hairline crack approximately 30cm long, "
            "starting 1m below the deck joint.",
        "severity": "high", "status": "in_progress",
        "location_description": "Km 34+200, Pier 2, West face",
        "assigned_to": "J. Wang",
        "created_at": "2026-11-24 08:00",
        "updated_at": "2026-11-24 10:30",
        "resolved_at": None,
        "history": [
            {"action": "created", "from_value": None,
             "to_value": "high", "detail": "Hairline crack...",
             "created_at": "2026-11-24 08:00"},
            {"action": "status_changed", "from_value": "open",
             "to_value": "in_progress", "detail": "",
             "created_at": "2026-11-24 09:00"},
            {"action": "photo_linked", "from_value": None,
             "to_value": "before", "detail": "IMG_000",
             "created_at": "2026-11-24 09:15"},
        ],
        "photos": [
            {"photo_id": "IMG_000", "role": "before",
             "original_sha256": hashlib.sha256(
                 _photo(seed=0)).hexdigest()},
        ],
    },
    {
        "issue_id": "iss-002",
        "title": "Concrete spalling on bridge girder B-3",
        "description": "Surface spalling with exposed rebar, "
            "approximately 15cm x 10cm area.",
        "severity": "critical", "status": "resolved",
        "location_description": "Km 34+300, Girder B-3, bottom flange",
        "assigned_to": "L. Chen",
        "created_at": "2026-11-20 14:00",
        "updated_at": "2026-11-23 16:00",
        "resolved_at": "2026-11-23 16:00",
        "history": [
            {"action": "created", "from_value": None,
             "to_value": "critical", "detail": "Spalling girder...",
             "created_at": "2026-11-20 14:00"},
            {"action": "status_changed", "from_value": "open",
             "to_value": "in_progress", "detail": "",
             "created_at": "2026-11-21 08:00"},
            {"action": "photo_linked", "from_value": None,
             "to_value": "before", "detail": "IMG_002",
             "created_at": "2026-11-21 08:30"},
            {"action": "photo_linked", "from_value": None,
             "to_value": "after", "detail": "IMG_003",
             "created_at": "2026-11-23 15:50"},
            {"action": "status_changed", "from_value": "in_progress",
             "to_value": "resolved", "detail": "",
             "created_at": "2026-11-23 16:00"},
        ],
        "photos": [
            {"photo_id": "IMG_002", "role": "before",
             "original_sha256": hashlib.sha256(
                 _photo(seed=40)).hexdigest()},
            {"photo_id": "IMG_003", "role": "after",
             "original_sha256": hashlib.sha256(
                 _photo(seed=60)).hexdigest()},
        ],
    },
]


def main() -> int:
    SAMPLES.mkdir(parents=True, exist_ok=True)

    # ---- 1: Inspection Report with issues + before/after photos ----
    meta = ReportMeta(
        report_id="r1-insp", report_number="WC-20261124-I101",
        project_name="Highway N-12 Widening - Section 3",
        site_address="Km 34+200, Jiangsu",
        generated_at="2026-11-24 14:00",
        operator="J. Wang (Site Engineer)",
        company_name="CMSS Engineering Co., Ltd.",
        include_evidence_appendix=False,
        issue_snapshot=ISSUE_SNAPSHOT)
    result = generate_report_pdf(ReportType.INSPECTION, meta,
                                  _entries(4), PageSize.A4, per_page=2,
                                  include_appendix=False)
    out = SAMPLES / "inspection_report_with_issues.pdf"
    out.write_bytes(result["pdf_bytes"])
    print(f"inspection_report_with_issues.pdf: {result['page_count']} pages, "
          f"{len(result['pdf_bytes'])} bytes")

    # ---- 2: Evidence Report with issues + appendix ----
    meta2 = ReportMeta(
        report_id="r1-ev", report_number="WC-20261124-E101",
        project_name="Highway N-12 Widening - Section 3",
        site_address="Km 34+200, Jiangsu",
        generated_at="2026-11-24 14:00",
        operator="J. Wang", company_name="CMSS Engineering",
        include_evidence_appendix=True,
        issue_snapshot=ISSUE_SNAPSHOT)
    result2 = generate_report_pdf(ReportType.EVIDENCE, meta2,
                                   _entries(4), PageSize.A4, per_page=2,
                                   include_appendix=True)
    out2 = SAMPLES / "evidence_report_full.pdf"
    out2.write_bytes(result2["pdf_bytes"])
    print(f"evidence_report_full.pdf: {result2['page_count']} pages, "
          f"{len(result2['pdf_bytes'])} bytes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
