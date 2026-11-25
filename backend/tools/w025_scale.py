"""B02-W025 scale test: staged 1k / 5k / 10k synthetic-photo ingest.

DESKTOP_SIMULATION: single-run desktop observations for the delivery
report - NOT a performance claim or SLA. What each stage measures:

  payload build : synthetic JPEG generation (30% duplicated content
                  to exercise physical dedup)
  ingest        : N photos through the STREAMING store path
                  (chunked hash+write) + bulk SQL registration
  thumbnails    : 10% sample thumbnail generation storm
  stats         : global physical accounting
  audit         : FS<->DB consistency audit (full hash re-verify)
  prune         : regenerable-cache pruning
  backup        : VACUUM INTO + verified originals copy
  disk          : real on-disk bytes per class after each phase

Memory: tracemalloc samples BOTH ingest paths on an identical payload
- streaming (this task) vs the legacy bytes-buffer store - so the
report can show the RISK-W021-R1 gap with real numbers.

Disk discipline: everything lands under a TEMP workdir (outside the
repo); each stage directory is deleted right after its numbers are
recorded. Nothing synthetic ever reaches git.

Usage:
  .venv\\Scripts\\python.exe tools\\w025_scale.py --stages 1000,5000,10000
  .venv\\Scripts\\python.exe tools\\w025_scale.py --stages 1000 --keep
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import shutil
import sys
import time
import tracemalloc
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("WORKCAM_ENROLLMENT_CODE", "w025-scale")

import app.db as dbm                                    # noqa: E402
from app.services import original_store, storage_ops   # noqa: E402

DEDUP_RATIO = 0.30          # fraction of photos sharing 30 bodies
THUMB_SAMPLE = 0.10         # fraction of objects that get thumbnails
JPEG_W, JPEG_H = 320, 240   # closer to real site-photo geometry


def jpeg_bytes(seed: int) -> bytes:
    from PIL import Image
    rng = random.Random(seed)
    img = Image.new("RGB", (JPEG_W, JPEG_H))
    img.putdata([(rng.randrange(256), rng.randrange(256),
                  rng.randrange(256)) for _ in range(JPEG_W * JPEG_H)])
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def tree_bytes(root: Path) -> tuple[int, int]:
    if not root.exists():
        return 0, 0
    total = files = 0
    for p in root.rglob("*"):
        if p.is_file() and not p.name.endswith(".part"):
            total += p.stat().st_size
            files += 1
    return total, files


def db_bytes(db_path: Path) -> int:
    total = db_path.stat().st_size if db_path.exists() else 0
    for suffix in ("-wal", "-shm"):
        w = db_path.with_name(db_path.name + suffix)
        if w.exists():
            total += w.stat().st_size
    return total


def _chunks_of(payload: bytes, size: int = 256 * 1024):
    for i in range(0, len(payload), size):
        yield payload[i:i + size]


def memory_probe(payload: bytes, stage_dir: Path) -> dict:
    """tracemalloc peak for BOTH store paths, same input model: a
    network-style CHUNK STREAM.

      streaming path : the chunks feed store_streaming directly ->
                       peak = one chunk
      legacy path    : simulates `await request.body()` exactly - the
                       chunks are joined into ONE in-memory bytes
                       object first (that IS the RISK-W021-R1 risk),
                       then store_original(data)

    Both peaks count only allocations inside the measurement window
    (the payload itself is pre-allocated). Probe DBs live under the
    stage dir so the stage teardown removes them."""
    sha = hashlib.sha256(payload).hexdigest()
    probe1 = stage_dir / "probe-stream.db"
    probe2 = stage_dir / "probe-legacy.db"

    os.environ["WORKCAM_BUSINESS_DB"] = str(probe1)
    dbm.migrate(probe1)
    tracemalloc.start()
    r1 = storage_ops.store_streaming(_chunks_of(payload), sha)
    _, peak_stream = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # remove the streamed object so the LEGACY probe cannot take the
    # idempotent fast path (it must actually buffer + write)
    shutil.rmtree(probe1.parent / "originals", ignore_errors=True)

    os.environ["WORKCAM_BUSINESS_DB"] = str(probe2)
    dbm.migrate(probe2)
    tracemalloc.start()
    data = b"".join(_chunks_of(payload))     # what request.body() does
    r2 = original_store.store_original(data, sha)
    _, peak_legacy = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    for probe in (probe1, probe2):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(probe) + suffix)
            p.unlink(missing_ok=True)
        d = probe.parent / "originals"
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    return {"payload_bytes": len(payload),
            "streaming_peak_py_heap": peak_stream,
            "legacy_peak_py_heap": peak_legacy,
            "note": "legacy peak simulates await request.body() "
                    "(join of the same chunk stream); streaming "
                    "consumes the chunks directly",
            "streaming_result": r1["idempotent_replay"],
            "legacy_result": r2["idempotent_replay"]}


def run_stage(n: int, workdir: Path, keep: bool = False) -> dict:
    stage_dir = workdir / f"stage-{n}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    db_path = stage_dir / "scale.db"
    os.environ["WORKCAM_BUSINESS_DB"] = str(db_path)
    dbm.migrate(db_path)

    result = {"photos": n, "dedup_ratio": DEDUP_RATIO,
              "jpeg_geometry": f"{JPEG_W}x{JPEG_H}"}

    # ---- payload build -------------------------------------------
    t0 = time.perf_counter()
    bodies = 30
    base = [jpeg_bytes(seed=7000 + i) for i in range(bodies)]
    t1 = time.perf_counter()
    result["payload_build_s"] = round(t1 - t0, 2)
    result["jpeg_avg_bytes"] = round(
        sum(len(b) for b in base) / len(base), 1)

    # ---- ingest (streaming) + registration ------------------------
    # JPEG synthesis is timed SEPARATELY from storage: the ingest
    # number must measure the store, not Pillow.
    gen_s = 0.0
    store_s = 0.0
    photo_rows, binding_rows = [], []
    now = dbm.utc_now()
    n_dup = int(n * DEDUP_RATIO)
    seen_shas = set()
    t_all = time.perf_counter()
    for i in range(n):
        t_g0 = time.perf_counter()
        data = base[i % bodies] if i < n_dup else jpeg_bytes(
            seed=9000 + i)
        t_g1 = time.perf_counter()
        gen_s += t_g1 - t_g0
        sha = hashlib.sha256(data).hexdigest()
        t_s0 = time.perf_counter()
        storage_ops.store_streaming(_chunks_of(data), sha)
        t_s1 = time.perf_counter()
        store_s += t_s1 - t_s0
        seen_shas.add(sha)
        pid = f"sc-{i:06d}"
        photo_rows.append((pid, "proj-scale", f"ik-{i}", sha,
                           "f.jpg", "image/jpeg", len(data), "t",
                           now, "d", None, now))
        binding_rows.append((pid, sha, "scale-owner", now))
    # project row first (FK target)
    with dbm.db() as conn:
        conn.execute(
            "INSERT INTO mvp_projects (project_id, owner_token, name, "
            "description, site_address, status, created_at, "
            "updated_at) VALUES ('proj-scale','scale-owner','Scale',"
            "'','','active',?,?)", (now, now))
        conn.executemany(
            "INSERT INTO mvp_photos (photo_id, project_id, "
            "idempotency_key, original_sha256, original_filename, "
            "mime_type, byte_size, captured_at, received_at, "
            "device_id, evidence_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", photo_rows)
        conn.executemany(
            "INSERT INTO mvp_photo_originals (photo_id, sha256, "
            "owner_token, bound_at) VALUES (?,?,?,?) "
            "ON CONFLICT(photo_id, sha256) DO NOTHING", binding_rows)
    t1 = time.perf_counter()
    result["jpeg_synthesis_s"] = round(gen_s, 2)
    result["ingest_s"] = round(store_s, 2)
    result["ingest_photos_per_s"] = round(n / store_s, 1)
    result["bulk_register_s"] = round((t1 - t_all) - gen_s - store_s, 2)
    result["distinct_objects"] = len(seen_shas)

    # ---- thumbnail storm ------------------------------------------
    t0 = time.perf_counter()
    thumb_targets = list(seen_shas)[::max(1, int(1 / THUMB_SAMPLE))]
    for sha in thumb_targets:
        storage_ops.ensure_thumbnail(sha, 512)
    t1 = time.perf_counter()
    result["thumbnails_generated"] = len(thumb_targets)
    result["thumbnail_storm_s"] = round(t1 - t0, 2)

    # ---- stats ----------------------------------------------------
    t0 = time.perf_counter()
    stats = storage_ops.storage_stats()
    t1 = time.perf_counter()
    result["stats_s"] = round(t1 - t0, 3)
    result["stats_dedup_saved_bytes"] = stats["originals"][
        "dedup_saved_bytes"]

    # ---- audit (full hash re-verify of every object) --------------
    t0 = time.perf_counter()
    audit = storage_ops.audit_storage()
    t1 = time.perf_counter()
    result["audit_s"] = round(t1 - t0, 2)
    result["audit_verdict"] = audit["verdict"]
    result["audit_intact"] = audit["intact"]

    # ---- prune + regenerate sample ---------------------------------
    t0 = time.perf_counter()
    pruned = storage_ops.prune_cache(scope="all")
    t1 = time.perf_counter()
    result["prune_s"] = round(t1 - t0, 2)
    result["prune_files"] = pruned["files_deleted"]
    result["prune_freed_bytes"] = pruned["freed_bytes"]
    t0 = time.perf_counter()
    for sha in thumb_targets[:20]:
        storage_ops.ensure_thumbnail(sha, 512)
    t1 = time.perf_counter()
    result["thumb_regenerate_20_s"] = round(t1 - t0, 2)

    # ---- backup ----------------------------------------------------
    t0 = time.perf_counter()
    db_backup = stage_dir / "backup.db"
    dbm.backup_to(db_backup, db_path)
    bdir = stage_dir / "backup-objs"
    manifest = original_store.backup(bdir)
    t1 = time.perf_counter()
    result["backup_s"] = round(t1 - t0, 2)
    result["backup_objects"] = len(manifest["objects"])
    result["backup_db_bytes"] = db_backup.stat().st_size

    # ---- disk accounting (real numbers) ---------------------------
    o_b, o_f = tree_bytes(original_store.store_root())
    t_b, t_f = tree_bytes(storage_ops.thumb_root())
    d_b, _ = tree_bytes(storage_ops.derivative_root())
    result["disk"] = {
        "originals_bytes": o_b, "originals_files": o_f,
        "thumbnails_bytes": t_b, "thumbnails_files": t_f,
        "derivatives_bytes": d_b,
        "db_bytes": db_bytes(db_path),
        "total_bytes": o_b + t_b + d_b + db_bytes(db_path),
    }
    result["originals_avg_object_bytes"] = round(
        o_b / max(1, o_f), 1)

    # ---- memory probe (both paths, same payload) ------------------
    probe_payload = jpeg_bytes(seed=42) * 40      # ~ multi-MB
    result["memory_probe"] = memory_probe(probe_payload, stage_dir)

    # restore env for next stage
    os.environ["WORKCAM_BUSINESS_DB"] = str(db_path)

    if not keep:
        shutil.rmtree(stage_dir, ignore_errors=True)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="1000,5000,10000")
    ap.add_argument("--keep", action="store_true",
                    help="keep stage dirs for inspection (default: "
                         "delete after recording numbers)")
    ap.add_argument("--out", default="",
                    help="write the JSON result here (default: print "
                         "only)")
    args = ap.parse_args()

    workdir = Path(os.environ.get(
        "WORKCAM_W025_SCALE_DIR",
        os.path.join(os.environ.get("TEMP", "/tmp"),
                     "zhanlu", "w025-scale")))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"[w025-scale] workdir={workdir} (outside the repo; stage "
          f"dirs are deleted after their numbers are recorded)")
    print(f"[w025-scale] python={sys.version.split()[0]} "
          f"machine={os.environ.get('COMPUTERNAME', '?')}")

    results = []
    for stage in [int(s) for s in args.stages.split(",")]:
        print(f"\n=== stage: {stage} photos "
              f"({int(stage * DEDUP_RATIO)} duplicated "
              f"content, {int(stage * (1 - DEDUP_RATIO))} unique) ===")
        r = run_stage(stage, workdir, keep=args.keep)
        results.append(r)
        print(json.dumps(r, indent=2, ensure_ascii=False))

    print("\n================ STAGE SUMMARY ================")
    hdr = (f"{'n':>7} {'synth_s':>8} {'ingest_s':>9} {'p/s':>8} "
           f"{'objects':>8} {'thumb_s':>8} {'stats_s':>8} "
           f"{'audit_s':>8} {'prune_s':>8} {'bkp_s':>7} {'disk_MB':>9}")
    print(hdr)
    for r in results:
        print(f"{r['photos']:>7} {r['jpeg_synthesis_s']:>8} "
              f"{r['ingest_s']:>9} {r['ingest_photos_per_s']:>8} "
              f"{r['distinct_objects']:>8} {r['thumbnail_storm_s']:>8} "
              f"{r['stats_s']:>8} {r['audit_s']:>8} {r['prune_s']:>8} "
              f"{r['backup_s']:>7} "
              f"{r['disk']['total_bytes'] / 1e6:>9.2f}")

    mp = results[0]["memory_probe"]
    print(f"\n[memory probe, {mp['payload_bytes']}B payload] "
          f"streaming peak py-heap={mp['streaming_peak_py_heap']}B "
          f"vs legacy request.body()-style peak="
          f"{mp['legacy_peak_py_heap']}B "
          f"(x{mp['legacy_peak_py_heap'] / max(1, mp['streaming_peak_py_heap']):.1f})")

    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"[w025-scale] workdir {workdir} removed")
    if args.out:
        Path(args.out).write_text(
            json.dumps(results, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"[w025-scale] json -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
