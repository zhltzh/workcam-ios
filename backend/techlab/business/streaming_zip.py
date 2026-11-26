"""M03-D05: streaming ZIP export (beyond ZIP32/128MiB).

Night 05's hand-written ZIP32 caps delivery at 128 MiB in memory.
This module is the Windows reference for the streaming successor:

  - writes entries CHUNK-BY-CHUNK through a sink (never whole-file
    in memory): hashing and CRC happen during the stream
  - ZIP64 (EOCD64) is enabled for >65535 entries and >4GiB
    payloads - the Python stdlib zipfile handles ZIP64 but is
    buffered per-entry; this stream writer keeps peak memory at
    one chunk (~1 MiB)
  - progress callback + cooperative cancellation (the caller sees
    bytes-so-far after every chunk)
  - interrupted exports leave a .part file (cleanup-safe) and are
    RESUMABLE only by full restart - a partial ZIP has no valid
    central directory, so honesty beats cleverness: we never
    deliver a partial as if it were complete
  - the final integrity proof is the streamed SHA-256 over the
    produced ZIP bytes (re-verified by reading the artifact back)

The Swift port notes (for the Mac side replacing DeliveryZIP32):
same chunking contract, ZIP64 EOCD64 record layout, crc32 via the
hardware-accelerated path - no third-party archive dependency.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

CHUNK = 1024 * 1024          # 1 MiB reference chunk


class ExportCancelled(Exception):
    """Cooperative cancellation - the caller raised through the
    progress hook."""


ProgressFn = Callable[[str, int], None]      # (entry_name, bytes)


def stream_export_zip(target: Path,
                      entries: list[tuple[str, Iterator[bytes]]],
                      on_progress: ProgressFn | None = None,
                      *,
                      cancel_check: Callable[[], None] | None = None
                      ) -> dict:
    """Stream `entries` into a ZIP at `target`.

    entries: (name, byte-chunk-iterator). Chunk iterators keep peak
    memory at ~1 chunk. The file is written to `<target>.part` first
    and ATOMICALLY RENAMED on success - an interrupted export never
    masquerades as a complete delivery.
    Returns {"entries": n, "bytes": N, "zip_sha256": ..., "zip64":
    bool} - the digest is computed over the FINAL artifact bytes.
    """
    if not entries:
        raise ValueError("no entries to export")
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    total_bytes = 0
    zip64 = False
    try:
        # zipfile with allowZip64 (default True) emits ZIP64 records
        # automatically when limits require them; we force64 when the
        # caller streams huge payloads, and we write via our own
        # chunk loop so memory stays flat
        with zipfile.ZipFile(part, "w",
                             compression=zipfile.ZIP_STORED,
                             allowZip64=True) as zf:
            for idx, (name, chunks) in enumerate(entries):
                if cancel_check:
                    cancel_check()
                wrote = 0
                hasher = hashlib.sha256()
                with zf.open(name, mode="w",
                             force_zip64=True) as sink:
                    for chunk in chunks:
                        if not chunk:
                            continue
                        if cancel_check:
                            cancel_check()
                        sink.write(chunk)
                        hasher.update(chunk)
                        wrote += len(chunk)
                        total_bytes += len(chunk)
                        if on_progress:
                            on_progress(name, wrote)
                if wrote > 0xFFFFFFFF or idx >= 0xFFEF:
                    zip64 = True
                # the per-entry sha256 of the STORED content is not
                # part of the zip format - callers needing per-file
                # digests read them from their own manifest; the zip
                # CRC (computed by zipfile internally) covers
                # integrity here
                _ = hasher.hexdigest()
        # streamed digest over the COMPLETE artifact
        digest = _digest_file(part)
        part.replace(target)
        # force_zip64=True makes EVERY entry carry ZIP64 records - a
        # payload of any size can exceed ZIP32 limits when entry
        # count grows later; the flag reports the FORMAT we WROTE
        return {"entries": len(entries), "bytes": total_bytes,
                "zip_sha256": digest, "zip64": True}
    except ExportCancelled:
        if part.exists():
            part.unlink()          # never leave a lying .part
        raise


def _digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_zip_artifact(path: Path) -> dict:
    """Open the produced ZIP and verify EVERY entry's CRC (zipfile
    does this on read). Returns entry count + names; raises on any
    corrupt entry - a delivery that cannot be verified is not a
    delivery."""
    with zipfile.ZipFile(path, "r") as zf:
        bad = zf.testzip()          # None == all CRCs pass
        if bad is not None:
            raise ValueError(f"corrupt entry in delivery: {bad}")
        infos = zf.infolist()
        names = [i.filename for i in infos]
        total = sum(i.file_size for i in infos)
        return {"entries": len(infos), "uncompressed_bytes": total,
                "names": names[:32],
                "zip64_flag": any(i.header_offset > 0xFFFFFFFF
                                  or i.file_size > 0xFFFFFFFF
                                  for i in infos) or len(infos) >= 0xFFFF}


def file_chunks(path: Path,
                chunk: int = CHUNK) -> Iterator[bytes]:
    """Chunk iterator over a file - the memory-flat source side."""
    with open(path, "rb") as f:
        for piece in iter(lambda: f.read(chunk), b""):
            yield piece


def iter_chunks(data: bytes,
               chunk: int = CHUNK) -> Iterator[bytes]:
    for i in range(0, len(data), chunk):
        yield data[i:i + chunk]
