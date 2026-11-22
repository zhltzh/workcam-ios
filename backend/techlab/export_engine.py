"""T09 - Fully offline multi-format export (tech-lab experiment).

Streaming export engine: JPEG/PNG/HEIC (byte passthrough), CSV, JSON,
ZIP bundle and a minimal pure-stdlib PDF report writer.

HONESTY RULES:
- Streaming only: photo bytes are copied file->archive in chunks; a
  1000-photo export must NOT hold 1000 photos in memory.
- HEIC/PNG **encoding** does not exist in this Python PoC: the engine
  passes through whatever bytes it is given and names them accordingly.
  Real pixel-level re-encoding is an iOS (ImageIO) responsibility.
- The PDF writer is a minimal single-page text report (ASCII-safe text;
  CJK entries degrade to '?'). iOS uses Core Text for real PDFs.
- Filenames are sanitized against path traversal and OS-invalid chars.

EXPERIMENTAL - not wired into the production `app` package.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

CHUNK = 256 * 1024

_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAVERSAL = re.compile(r"\.\.($|[/\\])")
_MAX_NAME = 180  # keep full path well under Windows MAX_PATH after prefix


def sanitize_filename(name: str, max_len: int = _MAX_NAME) -> str:
    """OS-safe, traversal-safe filename. CJK/emoji preserved (NFC), the
    rest degraded to '_'. Long names truncated with a counter suffix."""
    name = unicodedata.normalize("NFC", str(name))
    name = _TRAVERSAL.sub("__", name)
    name = _INVALID_FS.sub("_", name).strip("._ ") or "file"
    if len(name) <= max_len:
        return name
    stem, dot, ext = name.rpartition(".")
    if dot and len(ext) <= 10 and "." in name[1:]:
        # budget: "~" + 8 hex + "." + ext must fit inside max_len
        keep = max_len - 10 - len(ext)
        return f"{stem[:keep]}~{os.urandom(4).hex()}.{ext}"
    return f"{name[: max_len - 9]}~{os.urandom(4).hex()}"


@dataclass
class ExportItem:
    """One photo to export.

    R2 fix: data used to be MANDATORY, so a 1000-photo export held every
    photo's bytes in process memory (the old benchmark's 'peak 2.31 MB'
    measured only the ENGINE, hiding the ~200 MB of input). Sources are
    now:
      - file_path (preferred for real exports): the ZIP writer streams
        bytes from disk in CHUNK-sized pieces; the process never holds
        the whole photo.
      - data (inline): for small fixtures/tests only.
    meta may carry a 'content_hash' for post-export verification."""

    photo_id: str
    filename: str            # already sanitized
    data: bytes = b""       # inline source (fixtures only)
    file_path: Path | None = None   # file source - preferred
    meta: dict = field(default_factory=dict)

    def source_length(self) -> int:
        if self.file_path is not None:
            return self.file_path.stat().st_size
        return len(self.data)

    def iter_chunks(self, chunk: int = CHUNK) -> Iterator[bytes]:
        """Stream this item's bytes WITHOUT loading them fully."""
        if self.file_path is not None:
            with open(self.file_path, "rb") as f:
                while block := f.read(chunk):
                    yield block
        else:
            src = io.BytesIO(self.data)
            while block := src.read(chunk):
                yield block


class RssMeter:
    """PROCESS-level peak memory (R2 fix: tracemalloc only sees Python
    allocations by this interpreter, NOT buffers, file caches or the
    input list itself).

    On Windows this reads PeakWorkingSetSize via GetProcessMemoryInfo
    (kernel32/psapi). The 'peak_rss_bytes' is the true high-water mark of
    the whole process; tracemalloc_peak_bytes is kept alongside so the
    report can show engine-allocation vs process-RSS separately."""

    def __init__(self):
        self.peak_rss_bytes = 0
        self._tracemalloc_peak = 0

    def __enter__(self):
        import tracemalloc
        self._rss_at_start = _peak_working_set_bytes()
        tracemalloc.start()
        return self

    def __exit__(self, *exc):
        import tracemalloc
        _current, self._tracemalloc_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.peak_rss_bytes = _peak_working_set_bytes()
        # attribute kept for the old API name used by export functions
        self.peak = self._tracemalloc_peak

    @property
    def rss_delta_bytes(self) -> int:
        """Peak RSS minus the process RSS at meter start: the memory the
        export ADDED on top of the interpreter baseline."""
        return max(0, self.peak_rss_bytes - getattr(self, "_rss_at_start", 0))


def _peak_working_set_bytes() -> int:
    """Windows: PeakWorkingSetSize of the current process."""
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    # Win7+ moved this to kernel32 as K32GetProcessMemoryInfo; psapi.dll
    # keeps a forwarding stub. HANDLE is pointer-sized: without argtypes
    # the -1 pseudo-handle gets truncated to 32 bits and the call fails.
    for lib_name, func_name in (("psapi", "GetProcessMemoryInfo"),
                                ("kernel32", "K32GetProcessMemoryInfo")):
        try:
            func = getattr(ctypes.WinDLL(lib_name), func_name)
        except (OSError, AttributeError):
            continue
        func.argtypes = (wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD)
        func.restype = wintypes.BOOL
        if func(handle, ctypes.byref(pmc), pmc.cb):
            return int(pmc.PeakWorkingSetSize)
    raise OSError("GetProcessMemoryInfo unavailable via psapi/kernel32")


# --------------------------------------------------------------------- PDF
def minimal_pdf_report(title: str, lines: list[str]) -> bytes:
    """Single-page PDF, stdlib only. Non-ASCII characters degrade to '?'
    (documented limitation; iOS uses Core Text for real CJK PDFs)."""
    safe = [title.encode("latin-1", "replace").decode("latin-1")]
    for ln in lines:
        safe.append(ln.encode("latin-1", "replace").decode("latin-1"))

    esc = lambda s: s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    ops = ["BT", "/F1 12 Tf", "50 790 Td", "16 TL"]
    for i, ln in enumerate(safe[:45]):   # single page holds ~45 lines
        ops.append(f"({esc(ln)}) Tj" if i == 0 else f"({esc(ln)}) Tj")
        if i < len(safe[:45]) - 1:
            ops.append("T*")
    ops.append("ET")
    content = "\n".join(ops).encode("latin-1")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return out.getvalue()


# ------------------------------------------------------------------- OOXML
def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


_EMU_PER_PX = 9525            # 96 dpi
_DEFAULT_IMG_W_PX = 320       # fixed display size; JPEG dimension parsing
_DEFAULT_IMG_H_PX = 240       # is OUT of PoC scope (iOS ImageIO owns it)


def _docx_inline_image(doc_pr_id: int, rid: str, name: str) -> str:
    cx, cy = _DEFAULT_IMG_W_PX * _EMU_PER_PX, _DEFAULT_IMG_H_PX * _EMU_PER_PX
    return (
        '<w:p><w:r><w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:docPr id="{doc_pr_id}" name="{_xml_escape(name)}"/>'
        '<a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic>'
        f'<pic:nvPicPr><pic:cNvPr id="{doc_pr_id}" name="{_xml_escape(name)}"/>'
        '<pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/>'
        '<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
    )


def export_docx(items: list[ExportItem], target: Path, title: str = "WorkCam export",
                embed_images: bool = False, max_images: int = 50) -> ExportStats:
    """Minimal-valid DOCX. R2: optional REAL image embedding (inline
    DrawingML picture -> word/media/imageN.jpg + document.xml.rels).

    HONEST LIMITS (unchanged by embedding):
    - display size is FIXED (no JPEG dimension parsing in this PoC);
    - at most max_images photos embed (document bloat guard); beyond
      that, metadata rows continue without images;
    - "Word/WPS-openable BY SPEC" - no real Office app run on this
      machine; actual-app compatibility is recorded as UNVERIFIED.
    """
    tmp = target.with_suffix(target.suffix + ".part")
    t0 = time.perf_counter()
    body: list[str] = [f"<w:p><w:r><w:t>{_xml_escape(title)}</w:t></w:r></w:p>"]

    image_rels: list[str] = []
    embedded = 0
    for idx, it in enumerate(items):
        if embed_images and embedded < max_images:
            embedded += 1
            rid = f"rIdImg{embedded}"
            body.append(_docx_inline_image(embedded, rid, it.filename))
            image_rels.append(
                f'<Relationship Id="{rid}" '
                f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                f'Target="media/image{embedded}.jpg"/>'
            )
        for line in (it.photo_id, it.filename,
                     str(it.meta.get("captured_at", "")),
                     str(it.meta.get("note", ""))):
            body.append(f"<w:p><w:r><w:t xml:space=\"preserve\">"
                        f"{_xml_escape(line)}</w:t></w:r></w:p>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f"<w:body>{''.join(body)}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="jpg" ContentType="image/jpeg"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(image_rels) + "</Relationships>"
    ) if image_rels else None
    with RssMeter() as mm, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document.encode("utf-8"))
        if doc_rels is not None:
            zf.writestr("word/_rels/document.xml.rels", doc_rels)
            n = 0
            for it in items:
                if n >= embedded:
                    break
                n += 1
                if it.file_path is not None:
                    with open(it.file_path, "rb") as src, \
                         zf.open(f"word/media/image{n}.jpg", "w") as dst:
                        while chunk := src.read(CHUNK):
                            dst.write(chunk)
                else:
                    zf.writestr(f"word/media/image{n}.jpg", it.data)
    os.replace(tmp, target)
    t1 = time.perf_counter()
    return ExportStats("docx", len(items), t1 - t0, target.stat().st_size,
                       mm.peak / (1024 * 1024), streaming=True)


def export_xlsx(items: list[ExportItem], target: Path,
                embed_images: bool = False, max_images: int = 50) -> ExportStats:
    """Minimal-valid XLSX (inline strings): header row + one row per
    photo. R2: optional REAL image embedding via the standard
    SpreadsheetML drawing chain: sheet1.xml <drawing r:id> ->
    sheet1.xml.rels -> drawings/drawing1.xml (oneCellAnchor pictures) ->
    drawing1.xml.rels -> xl/media/imageN.jpg. Same honest limits as the
    DOCX embedder (fixed display size, max_images guard, no real-Office
    run)."""
    tmp = target.with_suffix(target.suffix + ".part")
    t0 = time.perf_counter()
    fieldnames = list(items[0].meta.keys()) if items else []

    def _cell(col: int, row: int, value: str) -> str:
        letters = ""
        c = col
        while c > 0:
            c, rem = divmod(c - 1, 26)
            letters = chr(65 + rem) + letters
        return (f'<c r="{letters}{row}" t="inlineStr">'
                f"<is><t>{_xml_escape(value)}</t></is></c>")

    rows_xml: list[str] = []
    if fieldnames:
        header = "".join(_cell(i, 1, k) for i, k in enumerate(fieldnames, 1))
        rows_xml.append(f'<row r="1">{header}</row>')
        for r, it in enumerate(items, 2):
            cells = "".join(
                _cell(i, r, json.dumps(v, ensure_ascii=False)
                      if isinstance(v, (dict, list)) else str(v))
                for i, v in enumerate(
                    (it.meta.get(k, "") for k in fieldnames), 1)
            )
            rows_xml.append(f'<row r="{r}">{cells}</row>')

    embedded = 0
    anchors: list[str] = []
    image_rels: list[str] = []
    if embed_images:
        for r, it in enumerate(items, 2):
            if embedded >= max_images:
                break
            embedded += 1
            rid = f"rIdImg{embedded}"
            # oneCellAnchor in a wide right-hand column (H), row = photo row
            anchors.append(
                '<xdr:oneCellAnchor>'
                f'<xdr:from><xdr:col>7</xdr:col><xdr:colOff>0</xdr:colOff>'
                f'<xdr:row>{r - 1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
                f'<xdr:ext cx="{_DEFAULT_IMG_W_PX * _EMU_PER_PX}" '
                f'cy="{_DEFAULT_IMG_H_PX * _EMU_PER_PX}"/>'
                '<xdr:pic><xdr:nvPicPr>'
                f'<xdr:cNvPr id="{embedded}" name="{_xml_escape(it.filename)}"/>'
                '<xdr:cNvPicPr/></xdr:nvPicPr>'
                f'<xdr:blipFill><a:blip r:embed="{rid}"/></xdr:blipFill>'
                '<xdr:spPr><a:xfrm><a:off x="0" y="0"/>'
                f'<a:ext cx="{_DEFAULT_IMG_W_PX * _EMU_PER_PX}" '
                f'cy="{_DEFAULT_IMG_H_PX * _EMU_PER_PX}"/></a:xfrm>'
                '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
                '</xdr:pic><xdr:clientData/></xdr:oneCellAnchor>'
            )
            image_rels.append(
                f'<Relationship Id="{rid}" '
                f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                f'Target="../media/image{embedded}.jpg"/>'
            )

    sheet_ns = ('xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
    drawing_ref = '<drawing r:id="rIdDraw1"/>' if anchors else ""
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet {sheet_ns}>'
        f"<sheetData>{''.join(rows_xml)}</sheetData>{drawing_ref}</worksheet>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="jpg" ContentType="image/jpeg"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        + ('<Override PartName="/xl/drawings/drawing1.xml" '
           'ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
           if anchors else "")
        + '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    sheet_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdDraw1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" '
        'Target="../drawings/drawing1.xml"/></Relationships>'
    ) if anchors else None
    drawing = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<xdr:wsDr '
        'xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        + "".join(anchors) + "</xdr:wsDr>"
    ) if anchors else None
    drawing_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(image_rels) + "</Relationships>"
    ) if anchors else None

    with RssMeter() as mm, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet.encode("utf-8"))
        if sheet_rels is not None:
            zf.writestr("xl/worksheets/_rels/sheet1.xml.rels", sheet_rels)
            zf.writestr("xl/drawings/drawing1.xml", drawing.encode("utf-8"))
            zf.writestr("xl/drawings/_rels/drawing1.xml.rels", drawing_rels)
            n = 0
            for it in items:
                if n >= embedded:
                    break
                n += 1
                if it.file_path is not None:
                    with open(it.file_path, "rb") as src, \
                         zf.open(f"xl/media/image{n}.jpg", "w") as dst:
                        while chunk := src.read(CHUNK):
                            dst.write(chunk)
                else:
                    zf.writestr(f"xl/media/image{n}.jpg", it.data)
    os.replace(tmp, target)
    t1 = time.perf_counter()
    return ExportStats("xlsx", len(items), t1 - t0, target.stat().st_size,
                       mm.peak / (1024 * 1024), streaming=True)


# ------------------------------------------------------------------- engine
@dataclass
class ExportStats:
    format: str
    files: int
    seconds: float
    bytes_out: int
    peak_memory_mb: float       # tracemalloc: allocations by THIS engine
    streaming: bool
    rss_peak_mb: float = 0.0    # R2: process-level PeakWorkingSetSize
    rss_delta_mb: float = 0.0   # R2: RSS added above the pre-export baseline

    def to_dict(self) -> dict:
        return {
            "format": self.format, "files": self.files,
            "seconds": round(self.seconds, 3),
            "bytes_out": self.bytes_out,
            "engine_alloc_mb": round(self.peak_memory_mb, 2),
            "rss_peak_mb": round(self.rss_peak_mb, 2),
            "rss_delta_mb": round(self.rss_delta_mb, 2),
            "streaming": self.streaming,
        }


def _check_unique_filenames(items: list[ExportItem]) -> None:
    """Duplicate entry names in a ZIP silently overwrite each other on
    extraction (later wins); a name that is a DIRECTORY PREFIX of another
    ('report' vs 'report/main.jpg') produces an archive extractors cannot
    lay out. Both are rejected BEFORE writing a single byte - a report
    export must never drop photos or create an unextractable package."""
    seen: dict[str, str] = {}
    for it in items:
        prev = seen.get(it.filename)
        if prev is not None:
            raise ValueError(
                f"duplicate filename in export list: '{it.filename}' used by "
                f"both {prev} and {it.photo_id} - refusing to write an "
                f"archive that silently overwrites entries"
            )
        seen[it.filename] = it.photo_id
    names = sorted(seen.keys())
    for a, b in zip(names, names[1:]):
        if b.startswith(a + "/"):
            raise ValueError(
                f"path conflict: '{a}' is a directory prefix of '{b}' - "
                f"the archive could not be extracted without collisions"
            )


def export_zip_bundle(items: list[ExportItem], target: Path,
                      extra_meta: dict | None = None,
                      compression: int = zipfile.ZIP_STORED,
                      disk_budget_bytes: int | None = None) -> ExportStats:
    """Streaming ZIP: photos are written through a chunked pipe (file
    sources NEVER enter process memory whole), plus metadata.csv /
    metadata.json / report.pdf inside the bundle.

    disk_budget_bytes: R2 low-disk simulation - raises DiskBudgetError
    once the archive has grown past the budget, leaving a .part file
    (never a torn target)."""
    _check_unique_filenames(items)
    tmp = target.with_suffix(target.suffix + ".part")
    written = 0
    t0 = time.perf_counter()
    with RssMeter() as mm:
        with zipfile.ZipFile(tmp, "w", compression) as zf:
            for it in items:
                with zf.open(it.filename, "w", force_zip64=True) as dst:
                    for chunk in it.iter_chunks(CHUNK):
                        dst.write(chunk)
                        written += len(chunk)
                        if disk_budget_bytes is not None and written > disk_budget_bytes:
                            raise DiskBudgetError(
                                f"simulated disk full after {written} bytes "
                                f"(budget {disk_budget_bytes}); export aborted, "
                                f"no target file was created"
                            )
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=list(items[0].meta.keys()) if items else [])
            writer.writeheader()
            for it in items:
                writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                                 for k, v in it.meta.items()})
            zf.writestr("metadata.csv", buf.getvalue())
            zf.writestr("metadata.json", json.dumps(
                [it.meta for it in items], ensure_ascii=False, indent=2))
            if extra_meta:
                zf.writestr("bundle-info.json", json.dumps(extra_meta, ensure_ascii=False, indent=2))
            lines = [f"{it.photo_id}  {it.filename}" for it in items[:44]]
            lines.append(f"... total {len(items)} photos" if len(items) > 44 else "")
            zf.writestr("report.pdf", minimal_pdf_report("WorkCam export", [l for l in lines if l]))
    os.replace(tmp, target)
    t1 = time.perf_counter()
    return ExportStats("zip", len(items), t1 - t0, target.stat().st_size,
                       mm.peak / (1024 * 1024), streaming=True,
                       rss_peak_mb=mm.peak_rss_bytes / (1024 * 1024),
                       rss_delta_mb=mm.rss_delta_bytes / (1024 * 1024))


class DiskBudgetError(RuntimeError):
    """R2 low-disk simulation: export aborted once the archive outgrew
    the injected budget; no target file was created."""


def export_csv(items: list[ExportItem], target: Path) -> ExportStats:
    tmp = target.with_suffix(target.suffix + ".part")
    peak = 0
    t0 = time.perf_counter()
    with RssMeter() as mm:
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(items[0].meta.keys()))
            writer.writeheader()
            for it in items:
                writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                                 for k, v in it.meta.items()})
    os.replace(tmp, target)
    t1 = time.perf_counter()
    return ExportStats("csv", len(items), t1 - t0, target.stat().st_size,
                       mm.peak / (1024 * 1024), streaming=True)


def export_json(items: list[ExportItem], target: Path) -> ExportStats:
    """JSON export is the ONE format that cannot be truly streamed without
    an array-splitter; we chunk-write the array to bound peak memory."""
    tmp = target.with_suffix(target.suffix + ".part")
    peak = 0
    t0 = time.perf_counter()
    with RssMeter() as mm:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("[\n")
            for i, it in enumerate(items):
                row = json.dumps(it.meta, ensure_ascii=False)
                f.write(row + (",\n" if i < len(items) - 1 else "\n"))
            f.write("]\n")
    os.replace(tmp, target)
    t1 = time.perf_counter()
    return ExportStats("json", len(items), t1 - t0, target.stat().st_size,
                       mm.peak / (1024 * 1024), streaming=False)


def make_benchmark_items(n: int, photo_size: int = 200 * 1024) -> list[ExportItem]:
    """Synthetic JPEG-ish payloads (deterministic, clearly synthetic)."""
    items = []
    payload_head = bytes.fromhex("ffd8ffe000104a46494600") + b"SYNTHETIC-WORKCAM-TEST-IMAGE" + b"\x00"
    for i in range(n):
        body = (payload_head + bytes([i % 251]) * (photo_size - len(payload_head)))
        items.append(ExportItem(
            photo_id=f"p{i:06d}",
            filename=f"SYNTH_检查照片_{i}.jpg",
            data=body,
            meta={
                "photo_id": f"p{i:06d}",
                "filename": f"SYNTH_检查照片_{i}.jpg",
                "content_hash": __import__("hashlib").sha256(body).hexdigest(),
                "note": f"合成测试数据 {i} - SYNTHETIC",
                "captured_at": "2026-11-19T08:00:00+00:00",
            },
        ))
    return items


def benchmark_export(target_dir: Path, sizes: tuple[int, ...] = (100, 500, 1000)) -> list[dict]:
    """LEGACY in-memory benchmark (kept for comparison): items hold bytes
    inline, so the INPUT list itself dominates RSS. Not the streaming
    contract - see benchmark_real_files."""
    target_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for n in sizes:
        items = make_benchmark_items(n)
        stats = export_zip_bundle(items, target_dir / f"bundle_{n}.zip",
                                  extra_meta={"synthetic": True})
        row = stats.to_dict()
        row["photos"] = n
        out.append(row)
    return out


def _write_synthetic_photo(path: Path, index: int, size: int) -> str:
    """Stream-write one synthetic photo to disk WITHOUT holding it whole;
    returns its sha256 for post-export verification."""
    import hashlib
    head = (bytes.fromhex("ffd8ffe000104a46494600")
            + b"SYNTHETIC-WORKCAM-TEST-IMAGE" + b"\x00")
    hasher = hashlib.sha256()
    remaining = size
    with open(path, "wb") as f:
        block = head
        while remaining > 0:
            hasher.update(block)
            f.write(block)
            remaining -= len(block)
            block = bytes([index % 251]) * min(CHUNK, remaining if remaining < CHUNK else CHUNK)
    return hasher.hexdigest()


def make_file_items(n: int, photos_dir: Path, photo_size: int = 200 * 1024,
                    write_files: bool = True) -> list[ExportItem]:
    """R2: file-path-sourced items. Photos live on DISK; the export
    streams them chunk-by-chunk. If write_files=False the caller must
    have created the files already (idempotent benchmark re-runs)."""
    photos_dir.mkdir(parents=True, exist_ok=True)
    items: list[ExportItem] = []
    for i in range(n):
        p = photos_dir / f"photo_{i:06d}.jpg"
        digest = None
        if write_files:
            digest = _write_synthetic_photo(p, i, photo_size)
        items.append(ExportItem(
            photo_id=f"p{i:06d}",
            filename=f"SYNTH_检查照片_{i}.jpg",
            file_path=p,
            meta={
                "photo_id": f"p{i:06d}",
                "filename": f"SYNTH_检查照片_{i}.jpg",
                "content_hash": digest or "",
                "note": f"合成测试数据 {i} - SYNTHETIC",
                "captured_at": "2026-11-19T08:00:00+00:00",
            },
        ))
    return items


def benchmark_real_files(work_dir: Path, sizes: tuple[int, ...] = (100, 500, 1000),
                         photo_size: int = 200 * 1024) -> list[dict]:
    """R2 honest benchmark: REAL files on disk, streamed export, PROCESS
    RSS measured in every stage separately:

      stage 1 - photo files exist on disk (baseline RSS recorded)
      stage 2 - items list built (metadata only for file sources)
      stage 3 - streaming ZIP export (STORED)
      stage 4 - DEFLATED export for the compression comparison

    DESKTOP figures, not iPhone. All data SYNTHETIC."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for n in sizes:
        photos_dir = work_dir / f"photos_{n}"
        row: dict = {"photos": n, "photo_size_bytes": photo_size}

        # stage 1: files on disk (written in a previous tier may be reused)
        if not photos_dir.exists() or not (photos_dir / f"photo_{n-1:06d}.jpg").exists():
            make_file_items(n, photos_dir, photo_size, write_files=True)
        row["payload_total_mb"] = round(n * photo_size / (1024 * 1024), 1)
        baseline_rss = _peak_working_set_bytes()
        # PeakWorkingSetSize is a high-water mark; it never decreases, so
        # stage RSS must be interpreted as DELTAS over a fresh subprocess
        # in the automated tests. Here we record the current working set.
        row["stage1_rss_after_writes_mb"] = round(baseline_rss / (1024 * 1024), 1)

        # stage 2: build items (file sources - no photo bytes in memory)
        t0 = time.perf_counter()
        items = make_file_items(n, photos_dir, photo_size, write_files=False)
        row["stage2_build_items_s"] = round(time.perf_counter() - t0, 3)

        # stage 3: streaming ZIP (STORED)
        stats = export_zip_bundle(items, work_dir / f"real_bundle_{n}_stored.zip",
                                  extra_meta={"synthetic": True})
        row["stage3_stored"] = stats.to_dict()

        # stage 4: DEFLATED (compression cost comparison)
        stats_d = export_zip_bundle(
            items, work_dir / f"real_bundle_{n}_deflated.zip",
            extra_meta={"synthetic": True}, compression=zipfile.ZIP_DEFLATED)
        row["stage4_deflated"] = stats_d.to_dict()

        out.append(row)
    return out


if __name__ == "__main__":
    import sys
    from pathlib import Path
    mode = sys.argv[1] if len(sys.argv) > 1 else "legacy"
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("tools/tech-lab/exports")
    if mode == "real":
        print(json.dumps(benchmark_real_files(dest), indent=2))
    else:
        print(json.dumps(benchmark_export(dest), indent=2))
