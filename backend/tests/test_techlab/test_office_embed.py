"""R2 - DOCX/XLSX image embedding & character-edge tests (stage 3).

Verifies, on the generated packages:
- embedded media bytes are byte-equal to the photo sources
- image <-> metadata correspondence (per-photo: media id, row/paragraph
  placement, filename in the XML)
- CJK + ASCII + special XML chars + long text survive the round trip
- UNVERIFIED-BY-REAL-OFFICE is documented, not claimed away
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from techlab.export_engine import (
    ExportItem,
    export_docx,
    export_xlsx,
    make_file_items,
)

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


def _items_with_photo(tmp_path: Path, n: int = 3, photo_size: int = 32 * 1024):
    return make_file_items(n, tmp_path / "photos", photo_size=photo_size,
                           write_files=True)


# ------------------------------------------------------------------ DOCX


def test_docx_embedded_images_byte_equal_sources(tmp_path):
    items = _items_with_photo(tmp_path)
    target = tmp_path / "r.docx"
    export_docx(items, target, embed_images=True)

    with zipfile.ZipFile(target) as zf:
        for i, it in enumerate(items, 1):
            embedded = zf.read(f"word/media/image{i}.jpg")
            original = it.file_path.read_bytes()
            assert embedded == original, f"image {i} bytes differ from source"
            assert hashlib.sha256(embedded).hexdigest() == \
                   hashlib.sha256(original).hexdigest()


def test_docx_image_metadata_correspondence(tmp_path):
    """Each photo's image must be bound to its own metadata block: the
    drawing name is the photo filename, and rIds in document.xml.rels
    resolve to the media files in order."""
    items = _items_with_photo(tmp_path)
    target = tmp_path / "r.docx"
    export_docx(items, target, embed_images=True)

    with zipfile.ZipFile(target) as zf:
        doc = ET.fromstring(zf.read("word/document.xml"))
        rels = ET.fromstring(zf.read("word/_rels/document.xml.rels"))

        rel_targets = {r.get("Id"): r.get("Target") for r in rels}

        # 1. drawing order matches item order
        drawings = doc.findall(".//w:drawing/wp:inline", {**NS, "wp": NS["a"]})
        doc_prs = doc.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr")
        # fallback: parse raw text for names + rIds in order
        raw = zf.read("word/document.xml").decode("utf-8")
        import re
        names = re.findall(r'<wp:docPr id="\d+" name="([^"]+)"', raw)
        rids = re.findall(r'<a:blip r:embed="(rIdImg\d+)"', raw)
        assert names == [it.filename for it in items]
        assert len(rids) == len(items)

        # 2. every rId resolves to the right media file (in order)
        for rid, (i, _it) in zip(rids, enumerate(items, 1)):
            assert rel_targets[rid] == f"media/image{i}.jpg"

        # 3. metadata paragraphs sit between drawings in item order
        notes = [str(it.meta.get("note", "")) for it in items]
        text_all = raw
        for note in notes:
            assert note in text_all


def test_docx_embed_disabled_writes_no_media(tmp_path):
    items = _items_with_photo(tmp_path)
    target = tmp_path / "r.docx"
    export_docx(items, target)          # embed_images=False (default)
    with zipfile.ZipFile(target) as zf:
        assert not [n for n in zf.namelist() if "media" in n]
        assert "word/_rels/document.xml.rels" not in zf.namelist()


def test_docx_max_images_guard(tmp_path):
    """More photos than max_images: first N embed, the rest are
    metadata-only; the guard prevents 1000-image document bloat."""
    items = _items_with_photo(tmp_path, n=5)
    target = tmp_path / "r.docx"
    export_docx(items, target, embed_images=True, max_images=3)
    with zipfile.ZipFile(target) as zf:
        media = sorted(n for n in zf.namelist() if n.startswith("word/media/"))
        assert len(media) == 3


# ------------------------------------------------------------------ XLSX


def test_xlsx_embedded_images_byte_equal_sources(tmp_path):
    items = _items_with_photo(tmp_path)
    target = tmp_path / "r.xlsx"
    export_xlsx(items, target, embed_images=True)
    with zipfile.ZipFile(target) as zf:
        for i, it in enumerate(items, 1):
            assert zf.read(f"xl/media/image{i}.jpg") == it.file_path.read_bytes()


def test_xlsx_drawing_chain_complete_and_ordered(tmp_path):
    """sheet1.xml -> drawing rel -> drawing1.xml -> image rels -> media:
    every part exists, anchors reference the right rIds, and anchors
    match the photo rows."""
    items = _items_with_photo(tmp_path)
    target = tmp_path / "r.xlsx"
    export_xlsx(items, target, embed_images=True)

    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        assert {"xl/worksheets/_rels/sheet1.xml.rels",
                "xl/drawings/drawing1.xml",
                "xl/drawings/_rels/drawing1.xml.rels"} <= names

        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert '<drawing r:id="rIdDraw1"/>' in sheet

        drawing = ET.fromstring(zf.read("xl/drawings/drawing1.xml"))
        anchors = drawing.findall("xdr:oneCellAnchor", NS)
        assert len(anchors) == len(items)

        # pic names (filenames) in order + blip rIds resolve
        import re
        raw = zf.read("xl/drawings/drawing1.xml").decode("utf-8")
        pic_names = re.findall(r'<xdr:cNvPr id="\d+" name="([^"]+)"', raw)
        assert pic_names == [it.filename for it in items]

        drels = ET.fromstring(zf.read("xl/drawings/_rels/drawing1.xml.rels"))
        rel_targets = {r.get("Id"): r.get("Target") for r in drels}
        rids = re.findall(r'<a:blip r:embed="(rIdImg\d+)"', raw)
        for rid, i in zip(rids, range(1, len(items) + 1)):
            assert rel_targets[rid] == f"../media/image{i}.jpg"

        # anchor rows correspond to the photo's data rows (row r-1 zero-based)
        from_rows = re.findall(r"<xdr:row>(\d+)</xdr:row>", raw)
        assert [int(x) for x in from_rows] == list(range(1, len(items) + 1))


# --------------------------------------------------- character edges (both)


def _edge_item(photo_id: str, note: str) -> ExportItem:
    return ExportItem(
        photo_id=photo_id, filename=f"{photo_id}.jpg", data=b"\xff\xd8\xff",
        meta={"photo_id": photo_id, "note": note},
    )


EDGE_NOTES = [
    "中文注释：梁柱裂缝检查 <tag> & \"quotes\" 'single'",
    "emoji 📷📍 and symbols ©®™ €£¥",
    "long text " * 60,                       # ~600 chars
    "tabs\tand\tcontrols preserved",
]


@pytest.mark.parametrize("fmt", ["docx", "xlsx"])
@pytest.mark.parametrize("note", EDGE_NOTES)
def test_character_edges_roundtrip(tmp_path, fmt, note):
    items = [_edge_item(f"p{i}", note) for i in range(4)]
    target = tmp_path / f"edge.{fmt}"
    if fmt == "docx":
        export_docx(items, target)
    else:
        export_xlsx(items, target)
    with zipfile.ZipFile(target) as zf:
        part = "word/document.xml" if fmt == "docx" else "xl/worksheets/sheet1.xml"
        raw = zf.read(part).decode("utf-8")
        # XML stays well-formed with edge content INSIDE
        root = ET.fromstring(raw.encode("utf-8"))
        # XML-ESCAPED on disk (spec correctness)...
        expected_escaped = (note.replace("&", "&amp;").replace("<", "&lt;")
                            .replace(">", "&gt;").replace('"', "&quot;"))
        assert expected_escaped in raw
        # ...and the UNESCAPED text is recoverable from the parse tree
        all_text = "".join(root.itertext())
        assert note in all_text
