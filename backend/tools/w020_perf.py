#!/usr/bin/env python
"""B02-W020 performance & stability stress on the RC2 integrated backend.

DESKTOP_SIMULATION - synthetic JPEG photos, in-process TestClient.
All numbers are single-run desktop observations on THIS machine; they
are regression baselines, NOT service-level agreements.

Phases (staged to respect desktop resources):
  1  10 projects /  1,000 photos /  10,000 metadata records
  2  100 projects / 5,000 photos /  50,000 metadata records
  3  100 projects / 10,000 photos / 100,000 metadata records

Measured per phase (real latencies, percentiles, real bytes):
  ingest throughput + latency percentiles (upload original bytes)
  query suite: project list, photo search (tag+time filter), cursor
    pagination walk, photo detail, evidence verify endpoint
  sync batch replay latency (annotations via 50-op batches)
  batch watermark (render 25 photos), report+package (2 projects)
  concurrent reads: 8 threads x 40 ops (search + verify mix)
  memory: tracemalloc peak during the query suite
  disk: on-disk data footprint at end of phase

Output: tools/w020-results/phase<N>.json + summary.md

Usage:
  python tools/w020_perf.py --phase 1|2|3|all [--keep-data]
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.main import app  # noqa: E402
import app.db as dbm  # noqa: E402

PHOTOS_DIR = Path(tempfile.mkdtemp(prefix="w020-photos-"))


def jpeg_bytes(seed: int) -> bytes:
    """Small deterministic synthetic site photo (~14 KB)."""
    from PIL import ImageDraw
    img = Image.new("RGB", (640, 480),
                    ((seed * 37) % 255, (seed * 17) % 255, seed % 255))
    d = ImageDraw.Draw(img)
    for i in range(12):
        x = (seed * 53 + i * 97) % 600
        y = (seed * 31 + i * 61) % 440
        d.rectangle([x, y, x + 40, y + 30],
                    fill=((i * 71) % 255, (i * 29) % 255, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=72)
    return buf.getvalue()


def pct(samples: list[float]) -> dict:
    s = sorted(samples)

    def p(q):
        return round(s[min(len(s) - 1, int(q * len(s)))], 3)
    return {"n": len(s), "p50": p(0.50), "p95": p(0.95), "p99": p(0.99),
            "max": round(s[-1], 3),
            "mean": round(statistics.fmean(s), 3)}


class Env:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        import os
        os.environ["WORKCAM_BUSINESS_DB"] = str(root / "biz.db")
        os.environ["WORKCAM_ENROLLMENT_CODE"] = "w020-code"
        os.environ["WORKCAM_UPLOAD_EXPERIMENTAL"] = "1"
        from app.services.key_registry import reset_registry
        reset_registry()
        # perf harness measures business throughput, not the 60/60s
        # device rate limiter - neutralize it exactly like the test
        # fixtures do (monkeypatch check_rate to a no-op)
        from app.services import device_identity as _di
        _di.check_rate = lambda *a, **k: None
        dbm.migrate(root / "biz.db")
        self.client = TestClient(app)
        self.token = self._enroll()

    def _enroll(self) -> str:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import \
            Encoding, PublicFormat
        key = ec.generate_private_key(ec.SECP256R1())
        pub = key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)
        import base64
        r = self.client.post(
            "/api/v1/devices/enroll",
            json={"public_key_b64": base64.b64encode(pub).decode(),
                  "device_id": "w020-bench", "enrollment_code": "w020-code"})
        assert r.status_code in (200, 201), r.text
        return r.json()["device_token"]

    @property
    def H(self):
        return {"X-Device-Token": self.token}


def run_phase(n: int, env: Env, results: dict) -> None:
    projects = n_projects = 10 if n == 1 else 100
    photos = {1: 1000, 2: 5000, 3: 10000}[n]
    meta_target = {1: 10_000, 2: 50_000, 3: 100_000}[n]
    r = env.client
    H = env.H
    log = results.setdefault(f"phase{n}", {})
    log["scale"] = {"projects": projects, "photos": photos,
                    "metadata_target": meta_target}

    # ---- create projects (sync batch for speed) --------------------
    lat = []
    proj_ids = []
    ops = []
    for i in range(projects):
        oid = f"w020-p{n}-{i}"
        ops.append({"op": "project_create", "operation_id": oid,
                    "payload": {"operation_id": oid,
                                "local_id": f"lp{n}-{i}",
                                "name": f"W020 Site {n}-{i}",
                                "description": "perf phase",
                                "site_address": f"km {i}"}})
        if len(ops) == 50:
            t = time.perf_counter()
            rr = r.post("/api/v1/sync/batch", headers=H,
                       json={"operations": ops})
            dt = time.perf_counter() - t
            assert rr.status_code == 200, rr.text[:200]
            lat.append(dt)
            proj_ids += [x["payload"]["local_id"] for x in ops]
            ops = []
    if ops:
        t = time.perf_counter()
        rr = r.post("/api/v1/sync/batch", headers=H, json={"operations": ops})
        assert rr.status_code == 200
        lat.append(time.perf_counter() - t)
        proj_ids += [x["payload"]["local_id"] for x in ops]
    # map local -> server ids via project list
    server_ids = []
    cursor = None
    while True:
        q = f"/api/v1/projects?limit=100" + (f"&cursor={cursor}"
                                              if cursor else "")
        rr = r.get(q, headers=H)
        body = rr.json()
        for p in body["items"]:
            server_ids.append(p["project_id"])
        cursor = body.get("next_cursor")
        if not cursor:
            break
    server_ids = [p for p in server_ids if p][:projects]
    log["project_create_sync_batch"] = pct(lat)

    # ---- photo ingest (register + upload original) -------------------
    # sample original bytes per 50 photos (dedup store shares objects)
    uniq = max(photos // 2, 200)
    bytes_pool = [jpeg_bytes(i) for i in range(uniq)]
    ingest_lat = []
    photo_ids = []
    per_proj = photos // projects
    t0 = time.perf_counter()
    idx = 0
    for pi, pid in enumerate(server_ids):
        for k in range(per_proj):
            data = bytes_pool[idx % len(bytes_pool)]
            import hashlib
            sha = hashlib.sha256(data).hexdigest()
            rr = r.post(f"/api/v1/projects/{pid}/photos", headers=H, json={
                "idempotency_key": f"w020-reg-{n}-{pi}-{k}",
                "original_sha256": sha,
                "original_filename": f"site-{pi}-{k}.jpg",
                "mime_type": "image/jpeg",
                "byte_size": len(data),
                "captured_at": "2026-11-25T10:00:00+00:00",
                "device_id": "w020-bench"})
            assert rr.status_code == 201, rr.text[:200]
            fid = rr.json()["photo_id"]
            t = time.perf_counter()
            rr = r.put(f"/api/v1/photos/{fid}/original", headers=H,
                       content=data)
            assert rr.status_code == 200, rr.text[:200]
            ingest_lat.append(time.perf_counter() - t)
            photo_ids.append((fid, pid))
            idx += 1
    ingest_wall = time.perf_counter() - t0
    log["ingest"] = {"photos": len(photo_ids),
                     "wall_s": round(ingest_wall, 1),
                     "per_photo_upload": pct(ingest_lat),
                     "throughput_per_s": round(len(photo_ids) / ingest_wall, 1)}

    # ---- metadata: annotations via sync batch + tags/notes via batch
    ann_ops_target = min(meta_target // 5, 20_000)
    tag_note_target = meta_target - ann_ops_target
    ann_lat = []
    ann_count = 0
    ops = []
    for j, (fid, _) in enumerate(photo_ids):
        if ann_count >= ann_ops_target:
            break
        oid = f"w020-a{n}-{j}"
        ops.append({"op": "annotation_create", "operation_id": oid,
                    "payload": {"operation_id": oid,
                                "photo_id": fid,
                                "note": f"annot {j} on phase {n}",
                                "author": "w020"}})
        ann_count += 1
        if len(ops) == 50:
            t = time.perf_counter()
            rr = r.post("/api/v1/sync/batch", headers=H,
                       json={"operations": ops})
            assert rr.status_code == 200
            ann_lat.append(time.perf_counter() - t)
            ops = []
    if ops:
        rr = r.post("/api/v1/sync/batch", headers=H, json={"operations": ops})
        assert rr.status_code == 200
    log["annotations_sync"] = {"records": ann_count,
                               "per_batch_of_50": pct(ann_lat)}

    # tags + business notes via W017 batch endpoints
    tag_lat = []
    tag_count = 0
    per_tag_photo = max(tag_note_target // (photos * 2), 1)
    for j, (fid, _) in enumerate(photo_ids):
        if tag_count >= tag_note_target:
            break
        rr = r.post("/api/v1/photos/batch/tags", headers=H, json={
            "batch_id": f"w020-tag-{n}-{j}", "photo_ids": [fid],
            "action": "add", "tags": [f"t{n}-{j % 25}"]})
        assert rr.status_code == 200, rr.text[:200]
        tag_count += 1
    log["tags_notes_batch"] = {"records": tag_count}
    # note rows double as metadata; count via db later

    # ---- query suite -------------------------------------------------
    qs = {}
    t = time.perf_counter(); r.get("/api/v1/projects?limit=50", headers=H)
    qs["projects_list"] = round(time.perf_counter() - t, 4)
    lat = []
    for i in range(40):
        t = time.perf_counter()
        rr = r.get(f"/api/v1/photos/search?tag=t{n}-7&limit=20",
                   headers=H)
        lat.append(time.perf_counter() - t)
        assert rr.status_code == 200
    qs["search_by_tag_40x"] = pct(lat)
    lat = []
    cursor = None
    walked = 0
    t_walk = time.perf_counter()
    while walked < 500:
        q = "/api/v1/photos/search?limit=50&sort=captured_at" + \
            (f"&cursor={cursor}" if cursor else "")
        rr = r.get(q, headers=H)
        body = rr.json()
        walked += len(body.get("items", []))
        cursor = body.get("next_cursor")
        lat.append(time.perf_counter() - t_walk)
        t_walk = time.perf_counter()
        if not cursor:
            break
    qs["cursor_page_walk_500"] = {"pages": len(lat), **pct(lat)}
    lat = []
    sample = photo_ids[:30]
    for fid, _ in sample:
        t = time.perf_counter()
        rr = r.get(f"/api/v1/photos/{fid}", headers=H)
        lat.append(time.perf_counter() - t)
        assert rr.status_code == 200
    qs["photo_detail_30x"] = pct(lat)
    lat = []
    for fid, pid in sample[:20]:
        t = time.perf_counter()
        rr = r.post(f"/api/v1/projects/{pid}/photos/{fid}/verify",
                    headers=H)
        lat.append(time.perf_counter() - t)
    qs["evidence_verify_20x"] = pct(lat) if lat else {"n": 0}

    # ---- concurrent reads: 8 threads x 40 ops ------------------------
    conc_lat: list[float] = []
    lock = threading.Lock()
    errors = []

    def worker(wid: int):
        local = []
        for i in range(40):
            try:
                t = time.perf_counter()
                if i % 2:
                    rr = r.get("/api/v1/photos/search?tag="
                               f"t{n}-7&limit=20", headers=H)
                else:
                    rr = r.get("/api/v1/projects?limit=20", headers=H)
                assert rr.status_code == 200
                local.append(time.perf_counter() - t)
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
        with lock:
            conc_lat.extend(local)
    threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
    t0 = time.perf_counter()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    qs["concurrent_reads_8x40"] = {"wall_s": round(time.perf_counter() - t0, 2),
                                   "errors": len(errors), **pct(conc_lat)}

    # ---- batch watermark (25 photos) + report (2 projects) ----------
    lat = []
    for ci, chunk in enumerate([photo_ids[i:i + 25] for i in range(0, 250, 25)]):
        ids = [f for f, _ in chunk]
        t = time.perf_counter()
        rr = r.post("/api/v1/photos/batch/watermark", headers=H, json={
            "batch_id": f"w020-wm-{n}-{ci}", "photo_ids": ids,
            "template": "construction",
            "fields": {"show_project_name": True, "project_name":
                       f"W020 Phase {n} Site"}})
        lat.append(time.perf_counter() - t)
        assert rr.status_code == 200, rr.text[:200]
    qs["batch_watermark_25x10"] = pct(lat)
    lat = []
    for ci, pid in enumerate(server_ids[:2]):
        ids = [f for f, p in photo_ids if p == pid][:12]
        t = time.perf_counter()
        rr = r.post(f"/api/v1/projects/{pid}/reports", headers=H, json={
            "report_type": "evidence", "photo_ids": ids,
            "page_size": "a4", "operator": "W020 bench"})
        lat.append(time.perf_counter() - t)
        assert rr.status_code == 201, rr.text[:200]
    qs["report_generate_2x"] = pct(lat) if lat else {"n": 0}
    log["queries"] = qs

    # ---- memory + disk ------------------------------------------------
    tracemalloc.start()
    for i in range(60):
        r.get("/api/v1/photos/search?tag=t"
              f"{n}-{i % 25}&limit=20", headers=H)
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    log["memory"] = {"query_suite_peak_mb": round(peak / 1e6, 1)}

    def dir_size(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    log["disk"] = {"data_mb": round(dir_size(env.root) / 1e6, 1)}

    # real metadata row count from db
    import sqlite3
    conn = sqlite3.connect(str(env.root / "biz.db"))
    rows = 0
    for t_ in ("mvp_annotations", "mvp_photo_tags",
               "mvp_photo_business", "mvp_issues"):
        try:
            rows += conn.execute(f"SELECT COUNT(*) FROM {t_}").fetchone()[0]
        except sqlite3.OperationalError:
            pass
    conn.close()
    log["scale"]["metadata_rows_actual"] = rows + ann_count


def main() -> None:
    import logging
    logging.disable(logging.INFO)     # silence httpx per-request spam
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all")
    ap.add_argument("--keep-data", action="store_true")
    args = ap.parse_args()
    out_dir = Path(__file__).parent / "w020-results"
    out_dir.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="w020-db-"))
    env = Env(root)
    results = {"meta": {"mode": "DESKTOP_SIMULATION",
                        "machine": "single desktop run, not an SLA",
                        "python": sys.version.split()[0]}}
    phases = [1, 2, 3] if args.phase == "all" else [int(args.phase)]
    for ph in phases:
        print(f"[phase {ph}] start", flush=True)
        t0 = time.perf_counter()
        run_phase(ph, env, results)
        print(f"[phase {ph}] done in "
              f"{time.perf_counter() - t0:.0f}s", flush=True)
        (out_dir / f"phase{ph}.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8")
    if not args.keep_data:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(PHOTOS_DIR, ignore_errors=True)
    print("results ->", out_dir)


if __name__ == "__main__":
    main()
