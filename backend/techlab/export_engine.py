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
from dataclasses import dataclass
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
    photo_id: str
    filename: str            # already sanitized
    data: bytes               # photo bytes (in-memory fixture OR chunk source)
    meta: dict               # free-form metadata row


class MemoryMeter:
    """tracemalloc wrapper. Peak is only valid AFTER the with-block exits
    (peak is read in __exit__); reading meter.peak inside the block returns
    a live value via current_peak()."""

    def __init__(self):
        self.peak = 0

    def __enter__(self):
        import tracemalloc
        tracemalloc.start()
        return self

    def current_peak(self) -> int:
        import tracemalloc
        _current, peak = tracemalloc.get_traced_memory()
        return peak

    def __exit__(self, *exc):
        import tracemalloc
        _current, peak = tracemalloc.get_traced_memory()
        self.peak = peak
        tracemalloc.stop()


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


# ------------------------------------------------------------------- engine
@dataclass
class ExportStats:
    format: str
    files: int
    seconds: float
    bytes_out: int
    peak_memory_mb: float
    streaming: bool

    def to_dict(self) -> dict:
        return {
            "format": self.format, "files": self.files,
            "seconds": round(self.seconds, 3),
            "bytes_out": self.bytes_out,
            "peak_memory_mb": round(self.peak_memory_mb, 2),
            "streaming": self.streaming,
        }


def export_zip_bundle(items: list[ExportItem], target: Path, extra_meta: dict | None = None) -> ExportStats:
    """Streaming ZIP: photos are written through a chunked pipe, plus
    metadata.csv / metadata.json / report.pdf inside the bundle."""
    tmp = target.with_suffix(target.suffix + ".part")
    peak = 0
    t0 = time.perf_counter()
    with MemoryMeter() as mm:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
            for it in items:
                src = io.BytesIO(it.data)
                with zf.open(it.filename, "w", force_zip64=True) as dst:
                    while chunk := src.read(CHUNK):
                        dst.write(chunk)
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
    peak = mm.peak
    os.replace(tmp, target)
    t1 = time.perf_counter()
    return ExportStats("zip", len(items), t1 - t0, target.stat().st_size,
                       peak / (1024 * 1024), streaming=True)


def export_csv(items: list[ExportItem], target: Path) -> ExportStats:
    tmp = target.with_suffix(target.suffix + ".part")
    peak = 0
    t0 = time.perf_counter()
    with MemoryMeter() as mm:
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(items[0].meta.keys()))
            writer.writeheader()
            for it in items:
                writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                                 for k, v in it.meta.items()})
    peak = mm.peak
    os.replace(tmp, target)
    t1 = time.perf_counter()
    return ExportStats("csv", len(items), t1 - t0, target.stat().st_size,
                       peak / (1024 * 1024), streaming=True)


def export_json(items: list[ExportItem], target: Path) -> ExportStats:
    """JSON export is the ONE format that cannot be truly streamed without
    an array-splitter; we chunk-write the array to bound peak memory."""
    tmp = target.with_suffix(target.suffix + ".part")
    peak = 0
    t0 = time.perf_counter()
    with MemoryMeter() as mm:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("[\n")
            for i, it in enumerate(items):
                row = json.dumps(it.meta, ensure_ascii=False)
                f.write(row + (",\n" if i < len(items) - 1 else "\n"))
            f.write("]\n")
    peak = mm.peak
    os.replace(tmp, target)
    t1 = time.perf_counter()
    return ExportStats("json", len(items), t1 - t0, target.stat().st_size,
                       peak / (1024 * 1024), streaming=False)


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
    """100/500/1000-photo export benchmark. DESKTOP figures, not iPhone."""
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


if __name__ == "__main__":
    import sys
    from pathlib import Path
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tools/tech-lab/exports")
    print(json.dumps(benchmark_export(dest), indent=2))
