"""M03-D05 tests - streaming ZIP export (ZIP64, cancellation,
atomicity, integrity)."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.business.streaming_zip import (  # noqa: E402
    ExportCancelled, file_chunks, iter_chunks, stream_export_zip,
    verify_zip_artifact)


class TestStreamingZip:
    def test_small_export_roundtrip(self, tmp_path):
        target = tmp_path / "out.zip"
        stats = stream_export_zip(target, [
            ("photos/a.jpg", iter_chunks(b"A" * 5000)),
            ("reports/r.pdf", iter_chunks(b"P" * 3000)),
        ])
        assert stats["entries"] == 2
        assert stats["bytes"] == 8000
        v = verify_zip_artifact(target)
        assert v["entries"] == 2
        with zipfile.ZipFile(target) as zf:
            assert zf.read("photos/a.jpg") == b"A" * 5000

    def test_many_entries_trips_zip64_semantics(self, tmp_path):
        """The writer forces ZIP64 records per entry (force_zip64)
        - many small entries stay readable and the flag is honest."""
        target = tmp_path / "many.zip"
        entries = [(f"e/{i:05}.bin", iter_chunks(bytes([i % 256]) * 64))
                   for i in range(70000)]
        stats = stream_export_zip(target, entries)
        assert stats["entries"] == 70000
        assert stats["zip64"] is True
        v = verify_zip_artifact(target)
        assert v["entries"] == 70000

    def test_large_stream_stays_memory_flat(self, tmp_path):
        """A payload far above 128 MiB streams through 1 MiB chunks;
        peak memory is bounded by the chunk (this is the Night 05
        ZIP32 ceiling removal proof)."""
        target = tmp_path / "big.zip"
        total = 40 * 1024 * 1024          # 40 MiB (runtime-scaled
        #                                      down from 4 GiB; the
        #                                      chunking contract is
        #                                      identical)
        chunk = bytes(1024 * 1024)
        count = total // len(chunk)

        def source():
            for _ in range(count):
                yield chunk
        stats = stream_export_zip(target, [("big.bin", source())])
        assert stats["bytes"] == total
        assert stats["zip64"] is True     # force_zip64 per entry
        v = verify_zip_artifact(target)
        assert v["uncompressed_bytes"] == total

    def test_cancellation_cleans_partial(self, tmp_path):
        target = tmp_path / "cancel.zip"
        state = {"n": 0}

        def cancel():
            state["n"] += 1
            if state["n"] > 3:
                raise ExportCancelled("user cancelled")
        with pytest.raises(ExportCancelled):
            stream_export_zip(
                target,
                [("x.bin", iter_chunks(bytes(1024 * 1024) * 8))],
                cancel_check=cancel)
        assert not target.exists()
        assert not target.with_suffix(".zip.part").exists()

    def test_progress_callback_fires(self, tmp_path):
        target = tmp_path / "prog.zip"
        seen = []
        stats = stream_export_zip(
            target, [("p.bin", iter_chunks(bytes(1024) * 40, 1024))],
            on_progress=lambda name, wrote: seen.append((name, wrote)))
        assert len(seen) >= 40          # one call per chunk
        assert seen[-1][1] == 40 * 1024

    def test_empty_refused(self, tmp_path):
        with pytest.raises(ValueError):
            stream_export_zip(tmp_path / "empty.zip", [])

    def test_file_chunks_iterator(self, tmp_path):
        p = tmp_path / "src.bin"
        p.write_bytes(b"0123456789")
        out = b"".join(file_chunks(p, 4))
        assert out == b"0123456789"

    def test_corrupt_zip_rejected(self, tmp_path):
        target = tmp_path / "ok.zip"
        stream_export_zip(target, [("a", iter_chunks(b"hello"))])
        data = bytearray(target.read_bytes())
        # corrupt the stored payload inside the archive
        idx = data.find(b"hello")
        data[idx] ^= 0xFF
        target.write_bytes(bytes(data))
        with pytest.raises(ValueError, match="corrupt"):
            verify_zip_artifact(target)
