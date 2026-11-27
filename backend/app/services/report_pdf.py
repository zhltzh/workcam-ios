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

REPORTS_VERSION = "2"


class ReportType(str, Enum):
    SITE_PHOTO = "site_photo"
    INSPECTION = "inspection"
    EVIDENCE = "evidence"
    # M03-D06: three additional professional templates
    MEASUREMENT = "measurement"
    BEFORE_AFTER = "before_after"
    PROJECT_SUMMARY = "project_summary"


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
    # B02-W015: inspector + project metadata block (MANUAL INPUT)
    inspector_name: str = ""
    inspector_title: str = ""
    project_metadata: dict = field(default_factory=dict)
    # B02-W015: LiDAR measurement record references. Each item is a
    # dict {record_id, description, related_issue_id?, data_status}.
    # data_status defaults to DEVICE_PENDING - real-device LiDAR data
    # has NOT been captured yet; every row renders as an EXPERIMENTAL
    # MEASUREMENT reference, never as a verified survey result.
    lidar_measurements: list | None = None


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

# B02-W015: four-tier source classification. EVERY content element in
# the report carries one of these labels so a reader can tell raw
# captures from human records, from experimental measurements, from
# generated images. Wording rule: nothing in this report is presented
# as a legal certification or attestation.
SOURCE_CLASSES = [
    ("RAW EVIDENCE",
     "Original capture bytes as uploaded; immutable in storage; "
     "SHA-256 recorded in this report."),
    ("MANUAL INPUT",
     "Human-entered record (severity, status, remediation notes, "
     "inspector details, sign-off). Not an automated assessment."),
    ("EXPERIMENTAL MEASUREMENT",
     "Sensor estimate under laboratory test conditions (LiDAR "
     "depth). Not survey-grade; device data status stated per row."),
    ("DERIVED IMAGE",
     "Image generated from an original (watermark, annotation or "
     "comparison render). Never a substitute for the original."),
]

_SEVERITY_ORDER = ["low", "medium", "high", "critical"]
_STATUS_ORDER = ["open", "in_progress", "resolved", "closed"]

_LIDAR_STATUS_TEXT = {
    "DEVICE_PENDING": "DEVICE_PENDING - real-device data not captured",
    "DEVICE_VERIFIED": "Device data verified in the B02-W016 experiment",
    "DESKTOP_SIMULATION": "Synthetic simulation data - not device results",
}


def _severity_breakdown(issues: list) -> dict:
    counts = {s: 0 for s in _SEVERITY_ORDER}
    for iss in issues:
        sev = iss.get("severity")
        if sev in counts:
            counts[sev] += 1
    return counts


def _status_summary(issues: list) -> dict:
    counts = {s: 0 for s in _STATUS_ORDER}
    for iss in issues:
        st = iss.get("status")
        if st in counts:
            counts[st] += 1
    return counts


def _ref_map(ids: list, prefix: str) -> dict:
    """Stable report-scoped reference numbers: PH-001 / ISS-001 ..."""
    return {i: f"{prefix}-{n + 1:03d}" for n, i in enumerate(ids)}


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


def _info_table(rows: list, styles) -> Table:
    t = Table(rows, colWidths=[1.3 * inch, 5.2 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.5),
        ("FONT", (1, 0), (1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), _BRAND),
        ("TEXTCOLOR", (1, 0), (1, -1), _TEXT),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, _LINE),
    ]))
    return t


def _source_legend(story, styles):
    """Four-tier source classification - the reader's key to which
    parts of the report are raw captures, human records, experimental
    measurements, or generated images."""
    story.append(Paragraph("Source Classification", styles["h2"]))
    story.append(Paragraph(
        "Every element in this report belongs to one of the following "
        "source classes. No element in this report constitutes a legal "
        "certification, attestation, or notarized statement.",
        styles["notes"]))
    rows = [["Class", "Meaning"]]
    for label, meaning in SOURCE_CLASSES:
        rows.append([label, meaning])
    t = Table(rows, colWidths=[1.7 * inch, 4.8 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7),
        ("FONT", (0, 1), (0, -1), "Helvetica-Bold", 7),
        ("FONT", (1, 1), (1, -1), "Helvetica", 7),
        ("BACKGROUND", (0, 0), (-1, 0), _SOFT),
        ("TEXTCOLOR", (0, 0), (-1, 0), _BRAND),
        ("TEXTCOLOR", (0, 1), (0, -1), _BRAND),
        ("TEXTCOLOR", (1, 1), (1, -1), _TEXT),
        ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SOFT]),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.12 * inch))


def _front_page(story, meta: ReportMeta, styles, report_type: ReportType,
                 count: int, page_size: PageSize, issue_count: int):
    m = meta
    story.append(Spacer(1, 0.30 * inch))
    story.append(Paragraph(_escape(m.project_name), styles["title"]))
    story.append(Paragraph(_escape(m.site_address), styles["subtitle"]))
    story.append(Spacer(1, 0.10 * inch))
    type_label = {
        ReportType.SITE_PHOTO: "Site Photo Report",
        ReportType.INSPECTION: "Inspection Report",
        ReportType.EVIDENCE: "Evidence Report",
        ReportType.MEASUREMENT: "Measurement Report",
        ReportType.BEFORE_AFTER: "Before / After Report",
        ReportType.PROJECT_SUMMARY: "Project Summary",
    }[report_type]
    story.append(Paragraph(
        f"{type_label} - {count} photo(s), {issue_count} issue(s)",
        styles["subtitle"]))
    story.append(Spacer(1, 0.14 * inch))

    info = [
        ["Report No.", m.report_number],
        ["Generated", m.generated_at],
        ["Page Size", {"a4": "A4 (210 x 297 mm)",
                       "letter": "US Letter (8.5 x 11 in)"
                       }[page_size.value]],
        ["Operator", m.operator or "-"],
        ["Company", m.company_name or "-"],
        ["Inspector",
         f"{m.inspector_name or '-'}"
         + (f" ({m.inspector_title})" if m.inspector_title else "")],
    ]
    if m.project_metadata:
        for k, v in list(m.project_metadata.items())[:10]:
            info.append([_escape(str(k))[:64], _escape(str(v))[:300]])
    story.append(_info_table(info, styles))
    story.append(Paragraph(
        "Inspector and project metadata above are MANUAL INPUT "
        "provided at report generation time.",
        styles["small"]))
    story.append(Spacer(1, 0.14 * inch))

    _source_legend(story, styles)

    story.append(Paragraph(
        "This report was generated from project data captured in WorkCam. "
        "Photos are presented as recorded at capture time; evidence "
        "states reflect verification status at the time of generation.",
        styles["notes"]))
    story.append(PageBreak())


# ------------------------------------------------------------ photo pages


def _photo_grid(story, photos: list[PhotoEntry], per_page: int, styles,
                meta: ReportMeta, doc: _ReportDoc, photo_ref: dict):
    """Photos 1/2/4 per page with per-photo metadata captions. Image
    sizing is bounded by the ACTUAL doc frame (width AND height) - the
    first draft computed height from the page size and a portrait image
    overflowed the frame (found by the Letter-page sample run).
    Captions carry the PH-nnn report reference and the RAW EVIDENCE
    source class (the embedded image is the immutable original)."""
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

        caption_bits = [
            f"<b>{photo_ref.get(ph.photo_id, ph.photo_id)}</b>",
            "[RAW EVIDENCE - original capture]"]
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


# -------------------------------------------------- W013-R1 + W015 issues

_SEVERITY_LABELS = {"low": "Low", "medium": "Medium",
                    "high": "High", "critical": "Critical"}
_STATUS_LABELS = {"open": "Open", "in_progress": "In Progress",
                  "resolved": "Resolved", "closed": "Closed"}

_ISSUE_FIG_MAX_H = 2.1 * inch       # before/after pair height bound


def _issue_photo_ref(p: dict, photo_ref: dict) -> str:
    """Reference label for an issue-linked photo: the PH-nnn number when
    the photo is embedded in this report, otherwise its raw id with an
    explicit not-embedded marker."""
    pid = p.get("photo_id", "")
    if pid in photo_ref:
        return photo_ref[pid]
    return f"{pid} (not embedded in this report)"


def _register_table(story, issues: list, styles, photo_ref: dict,
                    issue_ref: dict):
    """Issue register: one row per issue with report-scoped numbers."""
    rows = [["No.", "Title", "Severity", "Status", "Linked Photos"]]
    for iss in issues:
        refs = ", ".join(
            _issue_photo_ref(p, photo_ref) for p in iss.get("photos", []))
        rows.append([
            issue_ref.get(iss.get("issue_id", ""), "-"),
            _escape(iss.get("title", "Untitled"))[:80],
            _SEVERITY_LABELS.get(iss.get("severity"), iss.get("severity")
                                  or "-"),
            _STATUS_LABELS.get(iss.get("status"), iss.get("status") or "-"),
            _escape(refs) or "-",
        ])
    t = Table(rows, colWidths=[0.62 * inch, 2.5 * inch, 0.72 * inch,
                                0.85 * inch, 1.81 * inch], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
        ("BACKGROUND", (0, 0), (-1, 0), _SOFT),
        ("TEXTCOLOR", (0, 0), (-1, 0), _BRAND),
        ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SOFT]),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.10 * inch))


def _summary_table(story, issues: list, styles, kind: str):
    """One-row summary table: severity breakdown or remediation status
    counts, all MANUAL INPUT records."""
    if kind == "severity":
        counts = _severity_breakdown(issues)
        order, labels = _SEVERITY_ORDER, _SEVERITY_LABELS
        heading = "Severity Breakdown"
    else:
        counts = _status_summary(issues)
        order, labels = _STATUS_ORDER, _STATUS_LABELS
        heading = "Remediation Status Summary"
    story.append(Paragraph(heading, styles["h2"]))
    rows = [["Class", *[labels[s] for s in order], "Total"]]
    rows.append(["Count", *[str(counts[s]) for s in order],
                 str(sum(counts.values()))])
    widths = [0.7 * inch] + [1.0 * inch] * len(order) + [0.7 * inch]
    t = Table(rows, colWidths=widths)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), _SOFT),
        ("TEXTCOLOR", (0, 0), (-1, 0), _BRAND),
        ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
    ]))
    story.append(t)
    story.append(Paragraph(
        "Counts above summarize MANUAL INPUT records captured at "
        "generation time; no automated assessment is implied.",
        styles["small"]))
    story.append(Spacer(1, 0.10 * inch))


def _before_after_pair(iss: dict, styles, photo_ref: dict, usable_w: float):
    """Side-by-side before/after (or before/progress) comparison figure.
    Each embedded image is the RAW EVIDENCE original; the pairing is a
    MANUAL association made by a human, and this layout is a report
    rendering, not a stored derivative."""
    by_role = {}
    for p in iss.get("photos", []):
        if p.get("image_bytes"):
            by_role.setdefault(p.get("role"), p)
    if not by_role:
        return None
    roles = [r for r in ("before", "progress", "after")
             if r in by_role]
    if not roles:
        return None
    roles = roles[:2]                     # pair layout: 2 columns max
    cells, cap_cells = [], []
    half = usable_w / 2 - 0.08 * inch
    for role in roles:
        p = by_role[role]
        try:
            img = Image(io.BytesIO(p["image_bytes"]))
            ratio = img.imageHeight / max(1, img.imageWidth)
            h = _ISSUE_FIG_MAX_H
            w = min(half, h / ratio)
            h = w * ratio
            img.drawWidth = w
            img.drawHeight = h
            cells.append(img)
            sha_short = (p.get("original_sha256") or "")[:16] + "..."
            cap_cells.append(Paragraph(
                f"{role.upper()} - {_escape(_issue_photo_ref(p, photo_ref))}"
                f" [RAW EVIDENCE] sha {sha_short}", styles["small"]))
        except Exception:
            cells.append(Paragraph("[image unavailable]", styles["notes"]))
            cap_cells.append(Paragraph("", styles["small"]))
    while len(cells) < 2:
        cells.append(Paragraph("", styles["small"]))
        cap_cells.append(Paragraph("", styles["small"]))
    label_row = [Paragraph(
        f"<b>{roles[0].upper()}</b>", styles["small"]), Paragraph(
        f"<b>{roles[1].upper()}</b>" if len(roles) > 1 else "",
        styles["small"])]
    t = Table([label_row, cells, cap_cells],
              colWidths=[usable_w / 2, usable_w / 2])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BOX", (0, 0), (-1, -1), 0.4, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
    ]))
    return t


def _issue_section(story, issues: list, styles, photo_ref: dict,
                   issue_ref: dict, usable_w: float):
    story.append(PageBreak())
    story.append(Paragraph("Inspection Issues", styles["h2"]))
    story.append(Paragraph(
        "The issues below are MANUAL INPUT records captured at "
        "generation time (source class: MANUAL INPUT). Annotations and "
        "before/after photo associations are manual - they do not "
        "constitute acceptance of completed work or prove the photos "
        "show the same location.",
        styles["notes"]))
    story.append(Spacer(1, 0.10 * inch))

    # 1) register + breakdown + status summary
    _register_table(story, issues, styles, photo_ref, issue_ref)
    _summary_table(story, issues, styles, "severity")
    _summary_table(story, issues, styles, "status")
    story.append(PageBreak())

    # 2) per-issue detail
    for iss in issues:
        block = []
        ref = issue_ref.get(iss.get("issue_id", ""), "")
        sev = _SEVERITY_LABELS.get(iss.get("severity"), iss.get("severity"))
        st = _STATUS_LABELS.get(iss.get("status"), iss.get("status"))
        block.append(Paragraph(
            f"<b>{ref}. {_escape(iss.get('title', 'Untitled'))}</b>",
            styles["body"]))
        info_bits = [f"Severity: {sev}", f"Status: {st}",
                     "[MANUAL INPUT]"]
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
        if iss.get("remediation_note"):
            block.append(Paragraph(
                "<b>Remediation Recommendation (MANUAL INPUT):</b>",
                styles["small"]))
            block.append(Paragraph(_escape(iss["remediation_note"]),
                                    styles["small"]))
        if iss.get("photos"):
            role_bits = [
                f"{p['role'].title()}: "
                f"{_escape(_issue_photo_ref(p, photo_ref))}"
                for p in iss["photos"]]
            block.append(Paragraph(" | ".join(role_bits), styles["small"]))
        pair = _before_after_pair(iss, styles, photo_ref, usable_w)
        if pair is not None:
            block.append(pair)
            block.append(Paragraph(
                "Side-by-side layout is a report rendering of two RAW "
                "EVIDENCE originals; the pairing is a manual "
                "association and is not itself a stored image.",
                styles["small"]))
        if iss.get("history"):
            block.append(Paragraph("<b>History:</b>", styles["small"]))
            for h in iss["history"][:10]:
                parts = [h.get("created_at", "")]
                if h.get("action"):
                    parts.append(h["action"])
                if h.get("from_value") and h.get("to_value"):
                    parts.append(f"{h['from_value']} -> {h['to_value']}")
                block.append(Paragraph(" | ".join(parts), styles["small"]))
        block.append(Spacer(1, 0.10 * inch))
        story.append(KeepTogether(block))

    story.append(Spacer(1, 0.06 * inch))
    story.append(Paragraph(
        "Issue severity, status and remediation notes are MANUAL INPUT; "
        "no AI assessment is implied. Before/after photos are manual "
        "associations.",
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


# ------------------------------------------- B02-W015: LiDAR references


def _lidar_section(story, refs: list, styles, issue_ref: dict):
    """LiDAR measurement record references. Rows point at B02-W016
    analyzer artifacts by record id; every row is labeled an
    EXPERIMENTAL MEASUREMENT and states its device-data status. Real
    device data is DEVICE_PENDING at the time of this template; the
    reference placeholder never renders as a verified survey value."""
    story.append(PageBreak())
    story.append(Paragraph("LiDAR Measurement References", styles["h2"]))
    story.append(Paragraph(
        "Record ids below reference LiDAR measurement rows and analyzer "
        "artifacts from the B02-W016 LiDAR experiment protocol. These are "
        "EXPERIMENTAL MEASUREMENT references: ARKit depth is an estimate, "
        "not survey-grade instrumentation. No measurement in this report "
        "is presented as a certified or legally attested value.",
        styles["notes"]))
    story.append(Spacer(1, 0.08 * inch))
    rows = [["Record ID", "Description", "Related Issue", "Data Status"]]
    for r in refs:
        rid = str(r.get("record_id", ""))[:128]
        desc = _escape(str(r.get("description", ""))[:500])
        rel = r.get("related_issue_id") or ""
        related = issue_ref.get(rel, rel or "-")
        status = str(r.get("data_status", "DEVICE_PENDING"))
        status_text = _LIDAR_STATUS_TEXT.get(status, status)
        rows.append([_escape(rid), desc or "-",
                     _escape(related) if rel else "-",
                     status_text])
    t = Table(rows, colWidths=[1.7 * inch, 2.4 * inch, 0.7 * inch,
                                1.7 * inch], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
        ("BACKGROUND", (0, 0), (-1, 0), _SOFT),
        ("TEXTCOLOR", (0, 0), (-1, 0), _BRAND),
        ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SOFT]),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.10 * inch))
    story.append(Paragraph(
        "Source class: EXPERIMENTAL MEASUREMENT. Device-data status is "
        "stated per row; DEVICE_PENDING means real-device capture has "
        "not yet occurred and the reference is a placeholder for the "
        "B02-W016 analyzer artifact. Fill references only after the "
        "referenced rows exist in the B02-W016 experiment dataset.",
        styles["small"]))


# ----------------------------------------------- B02-W015: manual sign-off


def _measurement_deep_section(story, measurements: list,
                              styles) -> None:
    """M03-D06 measurement report: every record with full honest
    context (mode, calibration version, uncertainty) - the TM23
    measurement_nature wording carried into print."""
    story.append(PageBreak())
    story.append(Paragraph("Measurement Register", styles["h2"]))
    story.append(Paragraph(
        "All measurement values are DEVICE ESTIMATES unless a row "
        "explicitly says human-entered. Uncalibrated device "
        "measurements are conservative estimates, not certified "
        "measurements.", styles["small"]))
    rows = [["#", "Type", "Value", "Mode", "Calibration",
             "Uncertainty", "Photo Ref"]]
    for i, m in enumerate(measurements, start=1):
        cal = m.get("calibration_version", "uncalibrated")
        rows.append([str(i),
                     m.get("measurement_type", "?"),
                     f"{m.get('value', '?')} {m.get('units', '')}",
                     m.get("measurement_mode", "manual"),
                     cal,
                     ("+/- " + str(m.get("uncertainty", "?")))
                     if m.get("uncertainty") is not None else "n/a",
                     m.get("photo_ref", "")])
    t = Table(rows, colWidths=[0.3 * inch, 1.1 * inch, 1.1 * inch,
                               1.0 * inch, 1.1 * inch, 1.0 * inch,
                               1.2 * inch])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)


def _before_after_section(story, issues: list, styles, photo_ref,
                          issue_ref) -> None:
    """M03-D06 before/after report: per-issue photo pairs grouped by
    role. The section REPORTS what pairs exist - it never declares a
    remediation complete (that is a human sign-off)."""
    story.append(PageBreak())
    story.append(Paragraph("Before / After Comparison", styles["h2"]))
    story.append(Paragraph(
        "Photo pairs are human-linked. Pair presence does not by "
        "itself demonstrate remediation; the sign-off section "
        "carries the human confirmation.", styles["small"]))
    any_pairs = False
    for issue in issues:
        photos_by_role: dict[str, list[str]] = issue.get(
            "photos_by_role", {}) or {}
        before = photos_by_role.get("before", [])
        after = photos_by_role.get("after", [])
        if not before and not after:
            continue
        any_pairs = True
        iid = issue.get("issue_id", "?")
        story.append(Paragraph(
            f"Issue {_escape(str(issue_ref.get(iid, iid)))}: "
            f"{_escape(str(issue.get('title', '')))}",
            styles["h2"]))
        rows = [["Role", "Photo Reference"]]
        for role, ids in (("Before", before),
                          ("Progress", photos_by_role.get("progress",
                                                          [])),
                          ("After", after)):
            for pid in ids:
                rows.append([role, str(photo_ref.get(pid, pid))])
        t = Table(rows, colWidths=[1.2 * inch, 3 * inch])
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4,
             colors.HexColor("#cccccc")),
        ]))
        story.append(t)
        story.append(Spacer(1, 6))
    if not any_pairs:
        story.append(Paragraph(
            "No before/after photo roles recorded for the selected "
            "issues.", styles["small"]))


def _project_summary_section(story, meta: ReportMeta, photos: list,
                             issues: list, styles, photo_ref) -> None:
    """M03-D06 project summary: one-page executive digest of the
    evidence register - counts, severity mix, evidence-status mix.
    A digest is a VIEW of the register, not new claims."""
    story.append(PageBreak())
    story.append(Paragraph("Project Summary", styles["h2"]))
    counts = _severity_breakdown(issues)
    statuses = _status_summary(issues)
    verified = sum(1 for p in photos
                   if getattr(p, "evidence_status", None)
                   in ("cryptographically_valid", "verified"))
    rows = [
        ["Photos in report", str(len(photos))],
        ["Issues total", str(len(issues))],
        ["Severity: critical/high/medium/low",
         f"{counts.get('critical', 0)}/{counts.get('high', 0)}"
         f"/{counts.get('medium', 0)}/{counts.get('low', 0)}"],
        ["Open issues", str(statuses.get("open", 0))],
        ["Resolved issues", str(statuses.get("resolved", 0))],
        ["Photos with verified signatures", str(verified)],
    ]
    t = Table(rows, colWidths=[2.4 * inch, 2 * inch])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4,
         colors.HexColor("#cccccc")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Paragraph(
        "This summary is a view over the evidence register; it "
        "introduces no new verification claims.", styles["small"]))


def _sign_off_section(story, meta: ReportMeta, styles):
    """Manual sign-off block (source class: MANUAL INPUT). Layout only:
    name/date lines for wet or digital signatures. It makes no legal
    claim - no certification, attestation or notarization wording."""
    story.append(PageBreak())
    story.append(Paragraph("Manual Sign-off (Human Record)",
                           styles["h2"]))
    story.append(Paragraph(
        "Signatures in this section are MANUAL INPUT records created "
        "after the signer reviewed this report. Signing acknowledges "
        "review of the report content by the named individuals at the "
        "stated dates. It is a record-keeping field only: no legal "
        "attestation, certification or notarization is made or implied.",
        styles["notes"]))
    story.append(Spacer(1, 0.30 * inch))

    def _line(label, value=""):
        pre = f"{_escape(value)}" if value else ""
        row = [[Paragraph(f"<b>{label}</b>", styles["body"]),
                Paragraph(pre, styles["body"]),
                Paragraph("", styles["body"]),
                Paragraph("", styles["body"])]]
        t = Table(row, colWidths=[1.9 * inch, 2.4 * inch, 0.5 * inch,
                                  1.7 * inch])
        t.setStyle(TableStyle([
            ("LINEBELOW", (1, 0), (1, 0), 0.8, _TEXT),
            ("LINEBELOW", (3, 0), (3, 0), 0.8, _TEXT),
            ("TOPPADDING", (0, 0), (-1, -1), 14),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ]))
        return t

    hint = Paragraph(
        "Columns: signatory role / printed name on the signature line / "
        "date signed.", styles["small"])
    story.append(hint)
    story.append(Spacer(1, 0.10 * inch))
    story.append(_line("Inspector", meta.inspector_name or ""))
    story.append(Spacer(1, 0.34 * inch))
    story.append(_line("Reviewed by", ""))
    story.append(Spacer(1, 0.34 * inch))
    story.append(_line("Approved by", ""))


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
    issues = meta.issue_snapshot or []
    photo_ref = _ref_map([p.photo_id for p in photos], "PH")
    issue_ref = _ref_map([i.get("issue_id", "") for i in issues], "ISS")

    story: list = []
    _front_page(story, meta, styles, report_type, len(photos),
                page_size, len(issues))
    _photo_grid(story, photos, per_page, styles, meta, doc, photo_ref)

    # W013-R1 + B02-W015: issue register, breakdown, status summary,
    # per-issue details with remediation notes and before/after pairs
    if issues:
        _issue_section(story, issues, styles, photo_ref, issue_ref,
                       doc.width)

    # B02-W015: LiDAR measurement record references
    if meta.lidar_measurements:
        _lidar_section(story, meta.lidar_measurements, styles, issue_ref)

    # M03-D06: type-specific deep sections
    if report_type == ReportType.MEASUREMENT \
            and meta.lidar_measurements:
        # measurement reports expand every record with full context
        _measurement_deep_section(story, meta.lidar_measurements,
                                   styles)
    if report_type == ReportType.BEFORE_AFTER:
        _before_after_section(story, issues, styles, photo_ref,
                              issue_ref)
    if report_type == ReportType.PROJECT_SUMMARY:
        _project_summary_section(story, meta, photos, issues,
                                  styles, photo_ref)

    # B02-W015: manual sign-off block (layout; no legal claim)
    _sign_off_section(story, meta, styles)

    want_appendix = (include_appendix if include_appendix is not None
                     else report_type == ReportType.EVIDENCE)
    if want_appendix and photos:
        _evidence_appendix(story, photos, styles)

    doc.build(story)
    pdf = buf.getvalue()
    return {"pdf_bytes": pdf,
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "page_count": doc.page,
            "version": REPORTS_VERSION,
            "issue_summary": {
                "severity": _severity_breakdown(issues),
                "status": _status_summary(issues),
                "total": len(issues)},
            "source_classes": [c[0] for c in SOURCE_CLASSES]}
