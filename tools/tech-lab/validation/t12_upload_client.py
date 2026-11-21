#!/usr/bin/env python3
"""W003 - T12 upload test client (cross-platform, stdlib-only).

The reference client Mac uses for joint debugging. It exercises the
FULL protocol flow against the real FastAPI endpoints, printing a
step-by-step log:

  1. register batch (content plan from a local file)
  2. upload chunks OUT OF ORDER, drop one on purpose
  3. resume from server status (the missing index list)
  4. duplicate one chunk (must get duplicate_acked)
  5. complete (server recomposes + whole-batch sha256)
  6. verify server acked hash == local file hash

Usage:
    python tools/tech-lab/validation/t12_upload_client.py <file> <base-url>
    # example:
    python tools/tech-lab/validation/t12_upload_client.py photo.zip http://127.0.0.1:8000

Exit code 0 = full round-trip verified; 1 = any protocol step failed.
Works on Windows and macOS (urllib, no third-party deps).
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CHUNK_SIZE = 256 * 1024


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(CHUNK_SIZE):
            h.update(block)
    return h.hexdigest()


def _request(method: str, url: str, body: bytes | None = None,
             headers: dict | None = None):
    req = urllib.request.Request(url, data=body, method=method,
                                  headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    path = Path(argv[1])
    base = argv[2].rstrip("/")
    if not path.exists():
        print(f"FAIL: file not found: {path}")
        return 1

    total = path.stat().st_size
    total_sha = _hash_file(path)
    print(f"file: {path.name}  bytes={total}  sha256={total_sha[:12]}...")

    # 1. register
    status, body = _request(
        "POST", f"{base}/upload/experimental/batches",
        json.dumps({"total_bytes": total, "chunk_size": CHUNK_SIZE,
                    "total_sha256": total_sha}).encode(),
        {"Content-Type": "application/json"})
    if status != 200:
        print(f"FAIL: register -> {status}: {body.decode()[:200]}")
        return 1
    bid = json.loads(body)["batch_id"]
    print(f"[1/6] registered batch {bid}")

    # 2. upload chunks out of order, dropping one
    chunks = [(i, path.read_bytes()[i:i + CHUNK_SIZE])
              for i in range(0, total, CHUNK_SIZE)]
    drop_index = chunks[-1][0] if len(chunks) > 1 else 0
    sent = 0
    for idx, data in reversed(chunks):        # reverse order on purpose
        if idx == drop_index:
            print(f"[2/6] skipping chunk {idx} (simulated network drop)")
            continue
        status, body = _request(
            "PUT", f"{base}/upload/experimental/batches/{bid}/chunks/{idx}",
            data, {"Content-Length": str(len(data))})
        if status != 200:
            print(f"FAIL: chunk {idx} -> {status}: {body.decode()[:200]}")
            return 1
        sent += 1
    print(f"[2/6] uploaded {sent} chunks (out of order)")

    # 3. resume from status
    status, body = _request("GET", f"{base}/upload/experimental/batches/{bid}")
    missing = json.loads(body)["missing"]
    print(f"[3/6] server reports missing: {missing}")
    for idx in missing:
        data = chunks[idx][1]
        status, body = _request(
            "PUT", f"{base}/upload/experimental/batches/{bid}/chunks/{idx}",
            data, {"Content-Length": str(len(data))})
        if status != 200:
            print(f"FAIL: resume chunk {idx} -> {status}")
            return 1
    print(f"[3/6] resumed {len(missing)} chunk(s)")

    # 4. duplicate one chunk (idempotency check)
    dup_idx, dup_data = chunks[0]
    status, body = _request(
        "PUT", f"{base}/upload/experimental/batches/{bid}/chunks/{dup_idx}",
        dup_data, {"Content-Length": str(len(dup_data))})
    dup_status = json.loads(body).get("status")
    if status != 200 or dup_status != "duplicate_acked":
        print(f"FAIL: duplicate chunk -> {status} {body.decode()[:200]}")
        return 1
    print(f"[4/6] duplicate chunk -> {dup_status} (idempotent)")

    # 5. complete
    status, body = _request(
        "POST", f"{base}/upload/experimental/batches/{bid}/complete")
    if status != 200:
        print(f"FAIL: complete -> {status}: {body.decode()[:200]}")
        return 1
    result = json.loads(body)
    print(f"[5/6] complete: {result['status']}  "
          f"server hash {result['total_sha256'][:12]}...")

    # 6. verify server hash == local hash (independent)
    if result["total_sha256"] != total_sha:
        print("FAIL: server-acked hash DIFFERS from local file hash")
        return 1
    print(f"[6/6] verified: server hash == local hash")
    print("RESULT: upload protocol round-trip PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
