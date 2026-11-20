"""T09 - streaming multi-format export tests + memory benchmark."""

from __future__ import annotations

import json
import zipfile

import pytest

from techlab.export_engine import (
    ExportItem,
    export_csv,
    export_json,
    export_zip_bundle,
    make_benchmark_items,
    minimal_pdf_report,
    sanitize_filename,
)


def _item(i: int) -> ExportItem:
    data = bytes([i % 256]) * 4096
    return ExportItem(
        photo_id=f"p{i}",
        filename=f"photo_{i}.jpg",
        data=data,
        meta={"photo_id": f"p{i}", "content_hash": "ab" * 32, "note": f"备注 {i}"},
    )


def test_zip_bundle_contains_everything(tmp_path):
    items = [_item(i) for i in range(5)]
    target = tmp_path / "b.zip"
    stats = export_zip_bundle(items, target)
    assert stats.files == 5

    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
        for it in items:
            assert it.filename in names
            assert zf.read(it.filename) == it.data
        assert "metadata.csv" in names
        assert "metadata.json" in names
        assert "report.pdf" in names
        meta = json.loads(zf.read("metadata.json"))
        assert len(meta) == 5
        assert meta[0]["note"] == "备注 0"      # CJK preserved


def test_csv_and_json_roundtrip_cjk(tmp_path):
    items = [_item(i) for i in range(3)]
    csv_stats = export_csv(items, tmp_path / "m.csv")
    json_stats = export_json(items, tmp_path / "m.json")
    assert csv_stats.files == json_stats.files == 3

    text = (tmp_path / "m.csv").read_text(encoding="utf-8-sig")
    assert "备注 2" in text
    rows = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    assert rows[2]["note"] == "备注 2"


def test_pdf_writer_emits_valid_structure():
    pdf = minimal_pdf_report("WorkCam export", ["photo_1.jpg", "photo_2.jpg"])
    assert pdf.startswith(b"%PDF-1.4")
    assert b"%%EOF" in pdf
    assert b"/Type /Catalog" in pdf
    assert b"startxref" in pdf


def test_filename_sanitization():
    # path traversal neutralized
    assert ".." not in sanitize_filename("../../etc/passwd")
    assert ".." not in sanitize_filename("a../../win")
    # invalid chars replaced, CJK + emoji kept
    assert sanitize_filename('检查<"photo">?.jpg').startswith("检查")
    assert "<" not in sanitize_filename('a<b>c')
    # control characters dropped
    assert "\n" not in sanitize_filename("bad\nname.jpg")
    # long names truncated but unique & extension preserved
    long_name = "照" * 300 + ".jpg"
    short = sanitize_filename(long_name)
    assert len(short) <= 180
    assert short.endswith(".jpg")


def test_streaming_memory_stays_low_at_1000(tmp_path):
    """ACCEPTANCE: peak memory of a 1000-photo (~200 MB) streaming export
    must stay far below total payload size (streaming, not batch-load).
    Guard: peak must be > 0 - a zero reading means the meter was read at
    the wrong time (previous bug produced a FALSE PASS with peak=0)."""
    items = make_benchmark_items(1000, photo_size=200 * 1024)
    target = tmp_path / "big.zip"
    stats = export_zip_bundle(items, target)
    total_mb = sum(len(i.data) for i in items) / (1024 * 1024)
    # meter must have actually measured something
    assert stats.peak_memory_mb > 0, "memory meter returned 0 - false-pass guard"
    # streaming contract: engine peak << payload total (assert generous bound)
    assert stats.peak_memory_mb < total_mb * 0.5, (
        f"peak {stats.peak_memory_mb} MB vs payload {total_mb} MB - not streaming!"
    )
    assert stats.bytes_out == target.stat().st_size


def test_export_benchmark_numbers(tmp_path):
    """Benchmarks are DESKTOP figures; structure + plausibility pinned."""
    from techlab.export_engine import benchmark_export
    rows = benchmark_export(tmp_path, sizes=(100, 500))
    assert [r["photos"] for r in rows] == [100, 500]
    for r in rows:
        assert r["seconds"] > 0
        assert r["bytes_out"] > 0
    print("\n[T09 export benchmark (DESKTOP, not iPhone)]")
    for r in rows:
        print(f"  {r['photos']} photos -> zip: {r['seconds']}s, "
              f"{r['bytes_out']/1024/1024:.1f} MB, peak mem {r['peak_memory_mb']} MB")
