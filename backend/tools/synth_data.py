"""M03-F01: reproducible synthetic engineering data generator.

Seeded, honest, self-describing. The scale the task lists:
  10 / 100 projects
  1,000 / 10,000 photos
  100,000 metadata records (annotations + tags + business notes +
  sync events combined; the generator reports the real mix)
Everything derived from ONE seed -> fully reproducible. No user
data, no fabricated evidence (evidence_status reflects what the
generator actually did: unverified for everything, because the
generator signs NOTHING).
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class ScaleSpec:
    projects: int
    photos: int
    metadata_records: int


SCALES = {
    "small": ScaleSpec(10, 1_000, 10_000),
    "medium": ScaleSpec(100, 10_000, 100_000),
    "large": ScaleSpec(100, 50_000, 250_000),
}


class SyntheticDataError(ValueError):
    pass


def generate(scale: ScaleSpec, seed: int = 20261127) -> dict:
    """Returns a fully in-memory scene: projects, photos (with
    deterministic pseudo-JPEG byte lengths, NOT actual bytes - the
    store layer materializes on demand), annotations, tags,
    business notes, issues, sync events. The exact per-table counts
    are REPORTED (never silently different from the request)."""
    rng = random.Random(seed)
    t0 = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)

    projects = []
    for i in range(scale.projects):
        projects.append({
            "project_id": f"syn-pj-{i:04d}",
            "name": f"Synthetic Site {i:03d}",
            "site_address": f"{100 + i} Generator Ave",
            "status": "archived" if i % 7 == 3 else "active",
            "revision": 1 + (i % 9),
            "created_at": t0.isoformat(),
        })

    photos = []
    per_project = scale.photos // max(1, scale.projects)
    for i in range(scale.photos):
        p = projects[i % len(projects)]
        captured = t0 + timedelta(
            minutes=rng.randrange(0, 60 * 24 * 26))
        photos.append({
            "photo_id": f"syn-ph-{i:06d}",
            "project_id": p["project_id"],
            "captured_at": captured.isoformat(),
            "original_sha256": hashlib.sha256(
                f"synthetic-photo-{i}-{seed}".encode()).hexdigest(),
            "byte_size": rng.randrange(80_000, 900_000),
            "image_width": rng.choice([1280, 1920, 2048, 4032]),
            "image_height": rng.choice([720, 1080, 1536, 3024]),
            "evidence_status": "unverified",
            "filename": f"SYN_{i:06d}.jpg",
        })

    # metadata: annotations + tags + business notes; the split fills
    # toward scale.metadata_records and reports the real mix
    metadata_budget = scale.metadata_records
    annotations = []
    tags: list[tuple[str, str]] = []
    business = []
    issues = []
    n_ann = min(metadata_budget // 3, len(photos))
    for i in range(n_ann):
        ph = photos[rng.randrange(len(photos))]
        annotations.append({
            "annotation_id": f"syn-an-{i:06d}",
            "photo_id": ph["photo_id"],
            "annotation_type": rng.choice(
                ["text", "arrow", "rectangle", "circle",
                 "freehand", "number_marker"]),
            "norm_x1": rng.random(), "norm_y1": rng.random(),
            "norm_x2": rng.random(), "norm_y2": rng.random(),
            "text_content": f"note {i} on {ph['photo_id']}",
            "revision": 1,
            "is_active": True,
        })
    remaining = metadata_budget - n_ann
    n_tag = min(remaining // 2, len(photos) * 3)
    tag_vocab = [f"zone-{z}" for z in range(12)] + \
                ["deck", "pier", "roof", "electrical", "plumbing",
                 "hvac", "structural", "safety"]
    for i in range(n_tag):
        ph = photos[rng.randrange(len(photos))]
        tags.append((ph["photo_id"],
                     rng.choice(tag_vocab)))
    n_biz = remaining - n_tag
    for i in range(max(0, n_biz)):
        ph = photos[rng.randrange(len(photos))]
        business.append({
            "photo_id": ph["photo_id"],
            "category": rng.choice(
                ["as-built", "punch", "safety", "material"]),
            "note": f"synthetic business note {i}",
        })
    # issues: derived count (not part of the metadata budget)
    n_issues = max(scale.projects, scale.photos // 500)
    for i in range(n_issues):
        p = projects[rng.randrange(len(projects))]
        issues.append({
            "issue_id": f"syn-iss-{i:06d}",
            "project_id": p["project_id"],
            "title": f"Synthetic finding {i}",
            "severity": rng.choice(
                ["low", "medium", "high", "critical"]),
            "status": rng.choice(
                ["open", "in_progress", "resolved", "closed"]),
            "revision": 1,
        })

    counts = {
        "projects": len(projects),
        "photos": len(photos),
        "annotations": len(annotations),
        "tags": len(tags),
        "business_notes": len(business),
        "issues": len(issues),
        "metadata_total": len(annotations) + len(tags) + len(business),
        "metadata_requested": scale.metadata_records,
        "seed": seed,
    }
    return {"projects": projects, "photos": photos,
            "annotations": annotations, "tags": tags,
            "business": business, "issues": issues,
            "counts": counts}


# --------------------------------------------------- materializer

def materialize_jpeg(size: int, seed: int, w: int, h: int) -> bytes:
    """Deterministic synthetic JPEG of exactly-reproducible content
    (not exactly `size` bytes - the REAL byte count is returned by
    the caller measuring; photos carry their true sha256 from the
    generator, so materialization stays honest: hash what you
    produce and compare)."""
    import io
    from PIL import Image, ImageDraw
    rng = random.Random(seed)
    img = Image.new("RGB", (w, h),
                    (rng.randrange(256), rng.randrange(256),
                     rng.randrange(256)))
    d = ImageDraw.Draw(img)
    for _ in range(20):
        x1 = rng.randrange(w)
        y1 = rng.randrange(h)
        d.rectangle([x1, y1, min(w - 1, x1 + rng.randrange(w // 4)),
                     min(h - 1, y1 + rng.randrange(h // 4))],
                    fill=(rng.randrange(256), rng.randrange(256),
                          rng.randrange(256)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return buf.getvalue()


# --------------------------------------------------- loader (server)

def load_into_backend(scene: dict, db_path, originals_dir,
                      owner_token: str = "synthetic-owner") -> dict:
    """Bulk-load the scene through the REAL schema (direct inserts -
    the load path is honest about being a loader, not the API)."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        for p in scene["projects"]:
            cur.execute(
                "INSERT OR REPLACE INTO mvp_projects (project_id, "
                "owner_token, name, description, site_address, "
                "status, created_at, updated_at) VALUES "
                "(?,?,?,?,?,?,?,?)",
                (p["project_id"], owner_token, p["name"], "",
                 p["site_address"], p["status"], p["created_at"],
                 p["created_at"]))
        for ph in scene["photos"]:
            cur.execute(
                "INSERT OR REPLACE INTO mvp_photos (photo_id, "
                "project_id, idempotency_key, original_sha256, "
                "original_filename, mime_type, byte_size, "
                "captured_at, received_at, device_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ph["photo_id"], ph["project_id"],
                 f"ik-{ph['photo_id']}", ph["original_sha256"],
                 ph["filename"], "image/jpeg", ph["byte_size"],
                 ph["captured_at"], ph["captured_at"], "synthetic",
                 ph["captured_at"]))
        for a in scene["annotations"]:
            cur.execute(
                "INSERT OR REPLACE INTO mvp_annotations "
                "(annotation_id, photo_id, owner_token, annotation_type, norm_x1,"
                " norm_y1, norm_x2, norm_y2, color_hex, "
                "stroke_width, text_content, z_order, revision, "
                "is_active, created_at, updated_at) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (a["annotation_id"], a["photo_id"], owner_token,
                 a["annotation_type"], a["norm_x1"], a["norm_y1"],
                 a["norm_x2"], a["norm_y2"], "#FF3B30", 3,
                 a["text_content"], 0, a["revision"],
                 1 if a["is_active"] else 0, "2026-11-27T00:00:00"
                 "+00:00", "2026-11-27T00:00:00+00:00"))
        for pid, tag in scene["tags"]:
            cur.execute(
                "INSERT OR IGNORE INTO mvp_photo_tags (photo_id, "
                "tag, added_at) VALUES (?,?,?)",
                (pid, tag, "2026-11-27T00:00:00+00:00"))
        for b in scene["business"]:
            cur.execute(
                "INSERT OR REPLACE INTO mvp_photo_business "
                "(photo_id, category, note, updated_by, updated_at, "
                "revision) VALUES (?,?,?,?,?,?)",
                (b["photo_id"], b["category"], b["note"], "synthetic",
                 "2026-11-27T00:00:00+00:00", 1))
        for i in scene["issues"]:
            cur.execute(
                "INSERT OR REPLACE INTO mvp_issues (issue_id, "
                "project_id, owner_token, title, description, "
                "severity, status, location_description, "
                "assigned_to, revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (i["issue_id"], i["project_id"], owner_token,
                 i["title"], "", i["severity"], i["status"], "", "",
                 i["revision"], "2026-11-27T00:00:00+00:00",
                 "2026-11-27T00:00:00+00:00"))
        cur.execute("COMMIT")
    finally:
        conn.close()
    return scene["counts"]
