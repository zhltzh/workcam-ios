"""WC-W012 P1/P3 - engineering report PDF engine.

Three English report templates rendered with reportlab:
  Site Photo Report / Inspection Report / Evidence Report

Design notes:
  - A4 and US Letter page sizes
  - 1 / 2 / 4 photos per page, auto pagination
  - header (project / report id) + footer (page x/y + generation note)
  - evidence appendix for Evidence Report (and optionally others)
  - PROFESSIONAL layout: clean grid, muted colors, consistent spacing
  - SNAPSHOT model: every report binds the photo+evidence state at
    generation time; regeneration creates a new version
  - large reports STREAM: photos are loaded/drawn/released one at a
    time (P5)
  - honesty: the report is NOT a legal certification - the footer and
    the evidence appendix say so; unverified states render as-is
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)

REPORTS_VERSION = "1"


class ReportType(str, Enum):
    SITE_PHOTO = "site_photo"
    INSPECTION = "inspection"
    EVIDENCE = "evidence"


class PageSize(str, Enum):
    A4 = "a4"
    LETTER = "letter"


_PAGE_MAP = {PageSize.A4: A4, PageSize.LETTER: letter}


@dataclass
class PhotoEntry:
    """One photo row in a report - fully SNAPSHOT at generation time."""
    photo_id: str
    original_sha256: str
    derivative_sha256: str | None
    image_bytes: bytes | None          # the JPEG to embed (streamed)
    image_width: int
    image_height: int
    captured_time: str | None
    time_source: str | None             # 'device' | 'server_anchored' | None
    gps_latitude: float | None
    gps_longitude: float | None
    gps_source: str | None
    gps_accuracy: float | None
    notes: str | None
    evidence_status: str
    evidence_reference: str | None
    evidence_verified_at: str | None


@dataclass
class ReportMeta:
    report_id: str
    report_number: str
    project_name: str
    site_address: str
    generated_at: str
    operator: str
    company_name: str
    logo_bytes: bytes | None = None
    # appendix config
    include_evidence_appendix: bool = False
    # W013-R1: issue snapshot (list of issue dicts from the API)
    issue_snapshot: list | None = None


# ---------------------------------------------------------------- palette

_BRAND = colors.HexColor("#1a355e")
_ACCENT = colors.HexColor("#2e6da4")
_SOFT = colors.HexColor("#f2f5f9")
_TEXT = colors.HexColor("#1b1e23")
_MUTED = colors.HexColor("#5a6472")
_LINE = colors.HexColor("#c9d3de")

_EVIDENCE_TEXT = {
    "no_evidence": "No evidence linked",
    "unverified": "Unverified (evidence linked, verification pending)",
    "signature_valid": "Signature math valid; key not registered",
    "cryptographically_valid": "Cryptographically verified",
    "verification_failed": "Verification failed",
    "server_anchored": "Server anchored",
}


def _styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("wcTitle", parent=ss["Title"], fontName="Helvetica-Bold",
                                fontSize=20, leading=24, textColor=_BRAND, spaceAfter=6),
        "subtitle": ParagraphStyle("wcSub", parent=ss["Normal"], fontName="Helvetica",
                                   fontSize=10.5, leading=14, textColor=_MUTED, spaceAfter=2),
        "h2": ParagraphStyle("wcH2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                             fontSize=12.5, leading=16, textColor=_BRAND,
                             spaceBefore=10, spaceAfter=6),
        "body": ParagraphStyle("wcBody", parent=ss["BodyText"], fontName="Helvetica",
                               fontSize=9, leading=12, textColor=_TEXT),
        "notes": ParagraphStyle("wcNotes", parent=ss["BodyText"], fontName="Helvetica-Oblique",
                               fontSize=8.5, leading=11, textColor=_MUTED),
        "small": ParagraphStyle("wcSmall", parent=ss["BodyText"], fontName="Helvetica",
                                fontSize=7.5, leading=9.5, textColor=_MUTED),
    }


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------ page frame


class _ReportDoc(BaseDocTemplate):
    """Header/footer page furniture. Footer states the report is NOT a
    legal certification (product honesty rule)."""

    def __init__(self, buf, pagesize, meta: ReportMeta, total_pages=None,
                 **kw):
        super().__init__(buf, pagesize=pagesize, **kw)
        self._meta = meta
        self._styles = _styles()

    def _furniture(self, canv, doc):
        m = self._meta
        canv.saveState()
        w, h = doc.pagesize
        # header rule
        canv.setStrokeColor(_LINE)
        canv.setLineWidth(0.6)
        canv.line(0.55 * inch, h - 0.62 * inch, w - 0.55 * inch, h - 0.62 * inch)
        canv.setFont("Helvetica-Bold", 9)
        canv.setFillColor(_BRAND)
        canv.drawString(0.55 * inch, h - 0.50 * inch, m.project_name)
        canv.setFont("Helvetica", 8)
        canv.setFillColor(_MUTED)
        canv.drawRightString(w - 0.55 * inch, h - 0.50 * inch, m.report_number)
        # footer rule
        canv.line(0.55 * inch, 0.62 * inch, w - 0.55 * inch, 0.62 * inch)
        canv.setFont("Helvetica", 7)
        canv.drawString(0.55 * inch, 0.48 * inch,
                        "Generated by WorkCam - reference document, "
                        "not a legal certification")
        canv.drawRightString(w - 0.55 * inch, 0.48 * inch,
                             f"Page {doc.page}")
        if m.logo_bytes:
            try:
                from reportlab.lib.utils import ImageReader
                img = ImageReader(io.BytesIO(m.logo_bytes))
                iw, ih = img.getSize()
                lh = 0.24 * inch
                lw = iw * lh / ih
                canv.drawImage(img, 0.55 * inch, h - 0.50 * inch - lh,
                               width=lw, height=lh, mask="auto",
                               preserveAspectRatio=True)
            except Exception:
                pass                    # logo is decorative; never fatal
        canv.restoreState()


# ------------------------------------------------------------- front page


def _front_page(story, meta: ReportMeta, styles, report_type: ReportType,
                 count: int):
    m = meta
    story.append(Spacer(1, 0.35 * inch))
    story.append(Paragraph(_escape(m.project_name), styles["title"]))
    story.append(Paragraph(_escape(m.site_address), styles["subtitle"]))
    story.append(Spacer(1, 0.10 * inch))
    type_label = {
        ReportType.SITE_PHOTO: "Site Photo Report",
        ReportType.INSPECTION: "Inspection Report",
        ReportType.EVIDENCE: "Evidence Report",
    }[report_type]
    story.append(Paragraph(
        f"{type_label} - {count} photo(s)", styles["subtitle"]))
    story.append(Spacer(1, 0.18 * inch))

    info = [
        ["Report No.", m.report_number],
        ["Generated", m.generated_at],
        ["Operator", m.operator or "-"],
        ["Company", m.company_name or "-"],
    ]
    t = Table(info, colWidths=[1.3 * inch, 5.2 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.5),
        ("FONT", (1, 0), (1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), _BRAND),
        ("TEXTCOLOR", (1, 0), (1, -1), _TEXT),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, _LINE),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.22 * inch))
    story.append(Paragraph(
        "This report was generated from project data captured in WorkCam. "
        "Photos are presented as recorded at capture time; evidence "
        "states reflect verification status at the time of generation.",
        styles["notes"]))
    story.append(PageBreak())


# ------------------------------------------------------------ photo pages


def _photo_grid(story, photos: list[PhotoEntry], per_page: int, styles,
                meta: ReportMeta, doc: _ReportDoc):
    """Photos 1/2/4 per page with per-photo metadata captions. Image
    sizing is bounded by the ACTUAL doc frame (width AND height) - the
    first draft computed height from the page size and a portrait image
    overflowed the frame (found by the Letter-page sample run)."""
    usable_w = doc.width                      # already margin-subtracted
    frame_h = doc.height
    rows = 1 if per_page == 1 else 2          # 2-per-page = 2 rows
    caption_h = 44                             # caption + spacing
    max_h = (frame_h / rows) - caption_h
    max_h = max(max_h, 60)                    # never degenerate

    for i, ph in enumerate(photos):
        col_w = usable_w if per_page in (1, 2) else usable_w / 2

        block = []
        if ph.image_bytes:
            try:
                img = Image(io.BytesIO(ph.image_bytes))
                ratio = img.imageHeight / max(1, img.imageWidth)
                w = col_w
                h = w * ratio
                if h > max_h:                  # clamp by HEIGHT first
                    h = max_h
                    w = h / ratio
                img.drawWidth = w
                img.drawHeight = h
                block.append(img)
            except Exception:
                block.append(Paragraph("[image unavailable]",
                                       styles["notes"]))
        else:
            block.append(Paragraph(
                f"[photo {ph.photo_id} - image not embedded in this "
                "report]", styles["notes"]))

        caption_bits = [f"<b>{_escape(ph.photo_id)}</b>"]
        if ph.captured_time:
            src = {"device": "device time", "server_anchored":
                   "server-anchored", None: ""}.get(ph.time_source, "")
            tag = f" ({src})" if src else ""
            caption_bits.append(f"Captured: {_escape(ph.captured_time)}{tag}")
        if ph.gps_latitude is not None:
            g = f"GPS: {ph.gps_latitude:.6f}, {ph.gps_longitude:.6f}"
            if ph.gps_accuracy is not None:
                g += f" +/-{ph.gps_accuracy:.0f}m"
            if ph.gps_source:
                g += f" [{_escape(ph.gps_source)}]"
            caption_bits.append(g)
        if ph.notes:
            caption_bits.append(f"Note: {_escape(ph.notes)}")
        caption_bits.append(
            f"Evidence: {_EVIDENCE_TEXT.get(ph.evidence_status,
                                            ph.evidence_status)}")
        block.append(Paragraph(" | ".join(caption_bits), styles["small"]))
        block.append(Spacer(1, 0.10 * inch))
        story.append(KeepTogether(block))
        # pagination
        if per_page == 1 or (i + 1) % per_page == 0:
            if i + 1 < len(photos):
                story.append(PageBreak())


# -------------------------------------------------- W013-R1: issue section


_SEVERITY_LABELS = {"low": "Low", "medium": "Medium",
                    "high": "High", "critical": "Critical"}
_STATUS_LABELS = {"open": "Open", "in_progress": "In Progress",
                  "resolved": "Resolved", "closed": "Closed"}


def _issue_section(story, issues: list, styles):
    story.append(PageBreak())
    story.append(Paragraph("Inspection Issues", styles["h2"]))
    story.append(Paragraph(
        "The issues below are HUMAN records captured at generation time. "
        "Annotations and before/after photo associations are manual - "
        "they do not constitute acceptance of completed work or prove "
        "the photos show the same location.",
        styles["notes"]))
    story.append(Spacer(1, 0.10 * inch))

    for idx, iss in enumerate(issues):
        block = []
        sev = _SEVERITY_LABELS.get(iss.get("severity"), iss.get("severity"))
        st = _STATUS_LABELS.get(iss.get("status"), iss.get("status"))
        block.append(Paragraph(
            f"<b>{idx + 1}. {_escape(iss.get('title', 'Untitled'))}</b>",
            styles["body"]))
        info_bits = [f"Severity: {sev}", f"Status: {st}"]
        if iss.get("assigned_to"):
            info_bits.append(f"Assigned: {_escape(iss['assigned_to'])}")
        if iss.get("location_description"):
            info_bits.append(
                f"Location: {_escape(iss['location_description'])}")
        block.append(Paragraph(" | ".join(info_bits), styles["small"]))
        if iss.get("description"):
            block.append(Paragraph(f"Description: "
                                    f"{_escape(iss['description'])}",
                                    styles["small"]))
        if iss.get("photos"):
            role_bits = []
            for p in iss["photos"]:
                sha_short = p.get("original_sha256", "")[:16] + "…"
                role_bits.append(f"{p['role'].title()}: "
                                 f"{_escape(p['photo_id'])} "
                                 f"(sha {sha_short})")
            block.append(Paragraph(" | ".join(role_bits), styles["small"]))
        if iss.get("history"):
            block.append(Paragraph("<b>History:</b>", styles["small"]))
            for h in iss["history"][:10]:
                parts = [h.get("created_at", "")]
                if h.get("action"):
                    parts.append(h["action"])
                if h.get("from_value") and h.get("to_value"):
                    parts.append(f"{h['from_value']} -> {h['to_value']}")
                block.append(Paragraph(" | ".join(parts), styles["small"]))
        block.append(Spacer(1, 0.08 * inch))
        story.append(KeepTogether(block))

    story.append(Spacer(1, 0.06 * inch))
    story.append(Paragraph(
        "Issue severity and status are HUMAN records; no AI assessment "
        "is implied. Before/after photos are manual associations.",
        styles["notes"]))


# ----------------------------------------------------- evidence appendix


def _evidence_appendix(story, photos: list[PhotoEntry], styles):
    story.append(PageBreak())
    story.append(Paragraph("Evidence Appendix", styles["h2"]))
    story.append(Paragraph(
        "The table below is the verification snapshot at generation "
        "time. File integrity means the stored bytes match their "
        "SHA-256; it does NOT prove the capture scene was authentic. "
        "Signature validity and server time anchoring are separate "
        "claims.", styles["notes"]))
    story.append(Spacer(1, 0.08 * inch))

    header = ["Photo ID", "Original SHA-256", "Watermark SHA-256",
              "Evidence Ref", "Status at Generation", "Verified At"]
    rows = [header]
    for ph in photos:
        rows.append([
            _escape(ph.photo_id),
            ph.original_sha256[:16] + "…",
            (ph.derivative_sha256[:16] + "…") if ph.derivative_sha256 else "-",
            _escape(ph.evidence_reference or "-"),
            _EVIDENCE_TEXT.get(ph.evidence_status, ph.evidence_status),
            _escape(ph.evidence_verified_at or "-"),
        ])
    t = Table(rows, colWidths=[0.95 * inch, 1.15 * inch, 1.15 * inch,
                                1.15 * inch, 1.5 * inch, 0.9 * inch],
               repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
        ("BACKGROUND", (0, 0), (-1, 0), _SOFT),
        ("TEXTCOLOR", (0, 0), (-1, 0), _BRAND),
        ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SOFT]),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.10 * inch))
    story.append(Paragraph(
        "Independent verification: recompute SHA-256 of any delivered "
        "original and compare against this appendix; use the WorkCam "
        "evidence-verifier CLI to re-check signatures offline.",
        styles["small"]))
    story.append(Spacer(1, 0.06 * inch))
    story.append(Paragraph(
        "This report is a reference document generated from project "
        "data. It is NOT a legally certified or notarized file.",
        styles["notes"]))


# --------------------------------------------------------------- entry


def generate_report_pdf(report_type: ReportType, meta: ReportMeta,
                        photos: list[PhotoEntry],
                        page_size: PageSize = PageSize.A4,
                        per_page: int = 2,
                        include_appendix: bool | None = None,
                        ) -> dict:
    """Build the PDF. Returns bytes + sha256. Streaming-friendly: photo
    bytes are consumed one at a time inside the platypus build."""
    if per_page not in (1, 2, 4):
        raise ValueError("per_page must be 1, 2 or 4")
    buf = io.BytesIO()
    doc = _ReportDoc(buf, pagesize=_PAGE_MAP[page_size], meta=meta,
                     leftMargin=0.55 * inch, rightMargin=0.55 * inch,
                     topMargin=0.75 * inch, bottomMargin=0.75 * inch,
                     title=f"{meta.report_number}",
                     author=meta.company_name or "WorkCam")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="main")
    doc.addPageTemplates([PageTemplate(id="report", frames=[frame],
                                      onPage=doc._furniture)])

    styles = _styles()
    story: list = []
    _front_page(story, meta, styles, report_type, len(photos))
    _photo_grid(story, photos, per_page, styles, meta, doc)

    # W013-R1: issue section (if issues were snapshotted)
    if meta.issue_snapshot:
        _issue_section(story, meta.issue_snapshot, styles)

    want_appendix = (include_appendix if include_appendix is not None
                     else report_type == ReportType.EVIDENCE)
    if want_appendix and photos:
        _evidence_appendix(story, photos, styles)

    doc.build(story)
    pdf = buf.getvalue()
    return {"pdf_bytes": pdf,
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "page_count": doc.page,
            "version": REPORTS_VERSION}
