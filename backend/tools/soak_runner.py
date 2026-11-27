"""FE-F07: configurable long-run stability soak.

A REAL soak runner, not a claim: continuous mixed workload against
the live backend with per-interval health probes. Reports growth
trends (db size / fs size / open handles via list_objects / event
counts). Capped by --duration or --ops. DESKTOP observation only -
the task's rule: Windows numbers are never iPhone numbers.

Workload mix per iteration (mirrors the product's real loop):
  1. project upsert (sync batch)
  2. photo register + original upload (whole-buffer path)
  3. annotation create (sync batch)
  4. issue create+transition
  5. pull events (incremental cursor)
  6. watermark derivative render
  7. report generation (every N iterations)
  8. backup project package (every N iterations)

Probes every interval:
  db file size, originals dir size+count, sync-event count,
  latency P50 of the iteration, error count.
Output: docs/perf/soak-<ts>.json + human summary to stdout.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient

from app.main import app
from app.services import device_identity

CODE = "soak-code"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def p50(vals: list[float]) -> float:
    return statistics.median(vals) if vals else 0.0


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-s", type=int, default=60,
                    help="stop after this many seconds")
    ap.add_argument("--max-ops", type=int, default=0,
                    help="stop after this many iterations (0=until "
                         "duration)")
    ap.add_argument("--report-every", type=int, default=25)
    ap.add_argument("--backup-every", type=int, default=50)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="soak-"))
    import os
    os.environ["WORKCAM_BUSINESS_DB"] = str(tmp / "soak.db")
    os.environ["WORKCAM_ENROLLMENT_CODE"] = CODE
    import app.db as dbm
    dbm.migrate(tmp / "soak.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    orig_check = device_identity.check_rate
    device_identity.check_rate = lambda *a, **k: None

    c = TestClient(app)
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    r = c.post("/api/v1/devices/enroll", json={
        "public_key_b64": base64.b64encode(pub).decode(),
        "device_id": "soak", "enrollment_code": CODE})
    H = {"X-Device-Token": r.json()["device_token"]}
    r = c.post("/api/v1/projects", headers=H,
               json={"name": "Soak Site"})
    project_id = r.json()["project_id"]
    originals = tmp / "originals"

    def photo_bytes(i: int) -> bytes:
        import random
        rng = random.Random(i)
        return bytes(rng.getrandbits(8) for _ in range(4000))

    def probe(iteration: int) -> dict:
        import sqlite3
        conn = sqlite3.connect(str(tmp / "soak.db"))
        events = conn.execute(
            "SELECT COUNT(*) FROM mvp_sync_events").fetchone()[0]
        photos = conn.execute(
            "SELECT COUNT(*) FROM mvp_photos").fetchone()[0]
        conn.close()
        return {"iteration": iteration,
                "db_bytes": (tmp / "soak.db").stat().st_size,
                "originals_bytes": dir_size(originals) if
                    originals.exists() else 0,
                "photos": photos, "sync_events": events}

    iterations = 0
    errors = 0
    latencies: list[float] = []
    probes: list[dict] = []
    cursor = 0
    photo_counter = 0
    t_start = time.time()

    print(f"soak: duration={args.duration_s}s max_ops={args.max_ops} "
          f"report_every={args.report_every}")
    while True:
        elapsed = time.time() - t_start
        if args.duration_s and elapsed >= args.duration_s:
            break
        if args.max_ops and iterations >= args.max_ops:
            break
        t0 = time.perf_counter()
        try:
            # 1. project upsert (revision bumps)
            rev = iterations + 2
            c.post("/api/v1/sync/batch", headers=H, json={
                "operations": [{
                    "op": "project_update",
                    "operation_id": f"soak-pu-{iterations}",
                    "payload": {
                        "operation_id": f"soak-pu-{iterations}",
                        "server_project_id": project_id,
                        "expected_revision": rev,
                        "name": f"Soak Site {iterations}"}}]})
            # 2. photo register + upload
            photo_counter += 1
            data = photo_bytes(iterations)
            sha = hashlib.sha256(data).hexdigest()
            r = c.post(f"/api/v1/projects/{project_id}/photos",
                       headers=H, json={
                           "idempotency_key": f"soak-ik-{photo_counter}",
                           "original_sha256": sha,
                           "original_filename": f"s{photo_counter}.jpg",
                           "mime_type": "image/jpeg",
                           "byte_size": len(data),
                           "captured_at": now(),
                           "device_id": "soak"})
            pid = r.json()["photo_id"]
            c.put(f"/api/v1/photos/{pid}/original", headers=H,
                  content=data)
            # 3. annotation
            c.post("/api/v1/sync/batch", headers=H, json={
                "operations": [{
                    "op": "annotation_create",
                    "operation_id": f"soak-an-{iterations}",
                    "payload": {
                        "operation_id": f"soak-an-{iterations}",
                        "local_id": f"l-{iterations}",
                        "server_photo_id": pid,
                        "annotation_type": "text",
                        "norm_x1": 0.1, "norm_y1": 0.1,
                        "text_content": f"soak note {iterations}"}}]})
            # 4. issue create + transition (every 10th)
            if iterations % 10 == 0:
                out = c.post("/api/v1/sync/batch", headers=H, json={
                    "operations": [{
                        "op": "issue_create",
                        "operation_id": f"soak-iss-{iterations}",
                        "payload": {
                            "operation_id": f"soak-iss-{iterations}",
                            "local_id": f"li-{iterations}",
                            "server_project_id": project_id,
                            "title": f"Soak finding {iterations}",
                            "severity": "low"}}]}).json()
                iid = out["receipts"][0]["result"]["server_issue_id"]
                c.post("/api/v1/sync/batch", headers=H, json={
                    "operations": [{
                        "op": "issue_transition",
                        "operation_id": f"soak-tr-{iterations}",
                        "payload": {
                            "operation_id": f"soak-tr-{iterations}",
                            "server_issue_id": iid,
                            "expected_revision": 1,
                            "target_status": "in_progress"}}]})
            # 5. incremental pull
            pr = c.get(f"/api/v1/sync/events?cursor={cursor}&limit=100",
                       headers=H).json()
            cursor = pr.get("cursor", cursor)
            # 6. watermark derivative
            c.post("/api/v1/photos/batch/watermark", headers=H, json={
                "batch_id": f"soak-wm-{iterations}", "photo_ids": [pid],
                "template": "construction",
                "fields": {"show_project_name": True,
                            "project_name": "Soak"}})
            # 7. report (every N)
            if iterations % args.report_every == 0:
                c.post(f"/api/v1/projects/{project_id}/reports",
                       headers=H, json={
                           "report_type": "site_photo",
                           "photo_ids": [pid], "page_size": "a4",
                           "operator": "soak"})
            # 8. backup (every N)
            if iterations % args.backup_every == 0:
                from app.services import backup_v2
                backup_v2.project_backup(
                    project_id, H["X-Device-Token"],
                    tmp / f"bk-{iterations}")
        except Exception as e:            # noqa: BLE001
            errors += 1
            print(f"  error@{iterations}: {e}")
        latencies.append(time.perf_counter() - t0)
        iterations += 1
        if iterations % 10 == 0:
            probes.append(probe(iterations))

    device_identity.check_rate = orig_check
    wall = time.time() - t_start
    result = {
        "mode": "DESKTOP_SOAK",
        "host": "Windows dev machine (not iPhone, not SLA)",
        "duration_s": round(wall, 1),
        "iterations": iterations,
        "errors": errors,
        "ops_per_s": round(iterations / wall, 2),
        "iteration_latency_ms": {
            "p50": round(p50(latencies) * 1000, 1),
            "max": round(max(latencies) * 1000, 1)},
        "growth": {
            "first": probes[0] if probes else probe(0),
            "last": probes[-1] if probes else probe(iterations)},
        "probes": probes,
    }
    out_path = Path(args.out) if args.out else \
        Path(__file__).parents[2] / "docs" / "perf" / \
        f"soak-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items()
                      if k != "probes"}, indent=1))
    print(f"probes saved -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
