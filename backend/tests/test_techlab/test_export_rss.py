"""R2 - T09 export memory & robustness tests.

The R2 review fix: the old 'streaming' test asserted tracemalloc numbers
(~1.2 MB), which only see the ENGINE's allocations - the 1000 in-memory
ExportItem.data bytes (~200 MB payload) were invisible to it. This suite
measures PROCESS-LEVEL RSS in a fresh subprocess, where PeakWorkingSetSize
is a true high-water mark.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

from techlab.export_engine import (
    DiskBudgetError,
    ExportItem,
    export_zip_bundle,
    make_file_items,
    sanitize_filename,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# Subprocess probe: build N photo FILES on disk, stream-export them from
# file sources, report process PeakWorkingSetSize. Fresh process => the
# peak is attributable to THIS export alone.
_RSS_PROBE = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, {backend!r})
    from pathlib import Path
    from techlab.export_engine import (
        make_file_items, export_zip_bundle, _peak_working_set_bytes)

    n = int(sys.argv[1]); photo_size = int(sys.argv[2])
    work = Path(sys.argv[3])
    photos_dir = work / "photos"
    items = make_file_items(n, photos_dir, photo_size, write_files=True)
    baseline = _peak_working_set_bytes()   # high-water so far
    stats = export_zip_bundle(items, work / "bundle.zip", extra_meta={{"synthetic": True}})
    peak = _peak_working_set_bytes()
    print(json.dumps({{
        "peak_rss_bytes": peak,
        "baseline_rss_bytes": baseline,
        "payload_bytes": n * photo_size,
        "seconds": stats.seconds,
    }}))
""")


def _run_rss_probe(tmp_path: Path, n: int, photo_size: int = 400 * 1024) -> dict:
    import os

    tmp_path.mkdir(parents=True, exist_ok=True)
    probe = tmp_path / "probe.py"
    probe.write_text(_RSS_PROBE.format(backend=str(REPO_ROOT / "backend")),
                     encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(probe), str(n), str(photo_size), str(tmp_path)],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_file_source_streaming_rss_far_below_payload(tmp_path):
    """ACCEPTANCE (R2): exporting 500 x 400 KiB = ~200 MB of photos from
    FILE sources in a fresh process. A CPython interpreter alone
    resident-peaks around ~15-25 MB; if the export streamed correctly the
    process peak stays far under the payload. The bound (25% of payload)
    is generous enough for interpreter noise, tight enough that a
    resident copy of the photos would fail it (~200 MB extra)."""
    result = _run_rss_probe(tmp_path, n=500, photo_size=400 * 1024)
    payload = result["payload_bytes"]
    peak = result["peak_rss_bytes"]
    assert peak < payload * 0.25, (
        f"peak RSS {peak/1e6:.1f} MB vs payload {payload/1e6:.1f} MB - "
        f"file sources are not streaming"
    )


def test_file_source_vs_inline_rss_gap(tmp_path):
    """The inline (data=) path legitimately holds the payload in memory;
    the file-source path must show a clear RSS gap at the same size.
    This test documents WHY the R2 fix was needed: the old benchmark's
    tracemalloc figure hid the input list entirely."""
    payload_mb = 60
    n = 60 * 1024 * 1024 // (400 * 1024)   # ~60 MB payload

    file_probe = _run_rss_probe(tmp_path / "f", n=n, photo_size=400 * 1024)
    # the standalone size bound lives in the 200 MB test; here the 60 MB
    # payload is mostly interpreter baseline (~20 MB), so the ASSERTION
    # is the GAP vs inline, below.

    # inline probe: same payload, data= bytes
    inline_probe = textwrap.dedent("""
        import json, sys
        sys.path.insert(0, {backend!r})
        from pathlib import Path
        from techlab.export_engine import make_benchmark_items, export_zip_bundle, _peak_working_set_bytes
        n = int(sys.argv[1]); photo_size = int(sys.argv[2])
        work = Path(sys.argv[3]); work.mkdir(parents=True, exist_ok=True)
        items = make_benchmark_items(n, photo_size)
        export_zip_bundle(items, work / "bundle.zip", extra_meta={{"synthetic": True}})
        print(json.dumps({{"peak_rss_bytes": _peak_working_set_bytes(),
                            "payload_bytes": n * photo_size}}))
    """).format(backend=str(REPO_ROOT / "backend"))
    inline_dir = tmp_path / "i"
    inline_dir.mkdir(parents=True, exist_ok=True)
    probe2 = inline_dir / "inline_probe.py"
    probe2.write_text(inline_probe, encoding="utf-8")
    import os
    proc = subprocess.run(
        [sys.executable, str(probe2), str(n), str(400 * 1024), str(inline_dir)],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    inline = json.loads(proc.stdout.strip().splitlines()[-1])

    # inline peak must exceed payload; file-source peak must be far below
    assert inline["peak_rss_bytes"] > inline["payload_bytes"]
    assert file_probe["peak_rss_bytes"] < inline["peak_rss_bytes"] * 0.5


# ------------------------------------------------------------------ edge cases


def test_duplicate_filenames_rejected_before_any_write(tmp_path):
    """Two photos with the same filename would silently overwrite each
    other in the archive - rejected up front."""
    a = ExportItem(photo_id="p1", filename="same.jpg", data=b"A",
                   meta={"photo_id": "p1"})
    b = ExportItem(photo_id="p2", filename="same.jpg", data=b"B",
                   meta={"photo_id": "p2"})
    with pytest.raises(ValueError, match="duplicate filename"):
        export_zip_bundle([a, b], tmp_path / "x.zip")
    assert not (tmp_path / "x.zip").exists()
    assert not (tmp_path / "x.zip.part").exists()   # nothing was written at all


def test_path_conflict_prefix_rejected(tmp_path):
    """'report' and 'report/main.jpg' cannot coexist in one archive -
    zipfile itself does NOT reject this (it writes both, and extraction
    explodes), so the engine's pre-flight check must catch it."""
    a = ExportItem(photo_id="p1", filename="report/main.jpg", data=b"A",
                   meta={})
    b = ExportItem(photo_id="p2", filename="report", data=b"B", meta={})
    with pytest.raises(ValueError, match="path conflict"):
        export_zip_bundle([a, b], tmp_path / "x.zip")
    assert not (tmp_path / "x.zip").exists()
    assert not (tmp_path / "x.zip.part").exists()


def test_disk_budget_abort_leaves_no_target(tmp_path):
    """Low-disk simulation: export aborts once past the injected budget;
    no torn target file exists afterwards."""
    items = make_file_items(5, tmp_path / "photos", photo_size=64 * 1024,
                            write_files=True)
    with pytest.raises(DiskBudgetError, match="simulated disk full"):
        export_zip_bundle(items, tmp_path / "low_disk.zip",
                          disk_budget_bytes=100 * 1024)   # 100 KB budget vs 320 KB payload
    assert not (tmp_path / "low_disk.zip").exists()
    # a .part remains (crash forensics), which is NOT a valid target
    assert (tmp_path / "low_disk.zip.part").exists()


def test_interrupted_part_then_reexport_completes(tmp_path):
    """A stale .part from a killed export is overwritten cleanly by the
    next export; final archive is complete and verifiable."""
    items = make_file_items(3, tmp_path / "photos", photo_size=32 * 1024,
                            write_files=True)
    target = tmp_path / "b.zip"
    part = target.with_suffix(target.suffix + ".part")
    part.write_bytes(b"STALE GARBAGE FROM KILLED RUN")

    stats = export_zip_bundle(items, target, extra_meta={"synthetic": True})
    assert stats.files == 3
    assert target.exists() and not part.exists()
    with zipfile.ZipFile(target) as zf:
        assert zf.testzip() is None
        # photos + metadata.csv/json + bundle-info.json + report.pdf
        assert len(zf.namelist()) == 3 + 4


def test_file_source_archive_bytes_match_disk(tmp_path):
    """Streaming must not corrupt bytes: archive contents byte-equal the
    source files (post-export verification of file sources)."""
    import hashlib

    items = make_file_items(4, tmp_path / "photos", photo_size=48 * 1024,
                            write_files=True)
    expected = {
        it.filename: hashlib.sha256(it.file_path.read_bytes()).hexdigest()
        for it in items
    }
    target = tmp_path / "b.zip"
    export_zip_bundle(items, target)
    with zipfile.ZipFile(target) as zf:
        for filename, digest in expected.items():
            assert hashlib.sha256(zf.read(filename)).hexdigest() == digest


def test_stats_report_rss_fields(tmp_path):
    """ExportStats now carries process-RSS fields; both must be > 0 in a
    real export (meter must have measured)."""
    items = make_file_items(2, tmp_path / "photos", photo_size=32 * 1024,
                            write_files=True)
    stats = export_zip_bundle(items, tmp_path / "b.zip")
    d = stats.to_dict()
    assert d["rss_peak_mb"] > 0
    assert d["engine_alloc_mb"] >= 0
    assert "rss_delta_mb" in d
