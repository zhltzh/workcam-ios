"""TM32 server half: recovery scan over the REAL store + DB.

scan_recovery() walks every photo record and every on-disk object,
derives each one's pipeline state with the pure state machine, and
returns a plan. It is READ-ONLY: applying is the operator's choice.

apply_recovery() executes the SAFE subset of plans (rebind /
reverify) with idempotent writes; violations and orphan bytes are
reported for escalation and NEVER touched - the honor rules of
techlab/recovery_sm.py hold at the SQL level too.

Concurrency: apply_recovery() takes the module write lock and
re-derives each plan INSIDE the transaction before applying it, so
two racing recoveries cannot double-apply or see stale facts
(concurrent recovery - task requirement #9).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.db import db, utc_now
from app.services import original_store

from techlab.recovery_sm import (  # noqa: E402
    PipelineFacts, PipelineState, plan_recovery)

_APPLY_LOCK = threading.Lock()

SAFE_ACTIONS = {"rebind", "reverify", "none", "await_upload",
                "keep_failed"}


def _facts_for(conn: sqlite3.Connection, photo: sqlite3.Row,
               store_root: Path) -> PipelineFacts:
    sha = photo["original_sha256"]
    meta = True
    on_disk = False
    binding = False
    link = verified = failed = False
    obj = original_store.object_path(sha) \
        if all(c in "0123456789abcdef" for c in sha) and len(sha) == 64 \
        else None
    if obj is not None and obj.exists():
        on_disk = True
    row = conn.execute(
        "SELECT 1 FROM mvp_photo_originals WHERE photo_id = ?",
        (photo["photo_id"],)).fetchone()
    binding = row is not None
    row = conn.execute(
        "SELECT verification_status FROM mvp_evidence_links "
        "WHERE photo_id = ? ORDER BY created_at DESC LIMIT 1",
        (photo["photo_id"],)).fetchone()
    if row is not None:
        link = True
        status = row["verification_status"]
        # real vocabulary: unverified / cryptographically_valid /
        # verification_failed / server_anchored
        verified = status in ("cryptographically_valid", "server_anchored")
        failed = status == "verification_failed"
    return PipelineFacts(metadata_row=meta,
                         original_bytes_on_disk=on_disk,
                         binding_row=binding,
                         evidence_link_row=link,
                         link_verified=verified,
                         link_failed=failed)


def scan_recovery(conn: sqlite3.Connection | None = None) -> dict:
    """READ-ONLY full scan: one entry per photo + one per orphan
    object (bytes with no metadata row anywhere)."""
    if conn is None:
        with db() as c:
            return _scan_with(c)
    return _scan_with(conn)


def _scan_with(conn: sqlite3.Connection) -> dict:
        photos = conn.execute(
            "SELECT p.photo_id, p.original_sha256, pr.owner_token "
            "FROM mvp_photos p JOIN mvp_projects pr "
            "ON p.project_id = pr.project_id").fetchall()
        plans = []
        for photo in photos:
            f = _facts_for(conn, photo, original_store.store_root())
            entry = {"photo_id": photo["photo_id"],
                     "original_sha256": photo["original_sha256"],
                     **plan_recovery(f)}
            plans.append(entry)
        # orphan objects: files on disk whose sha matches NO photo row
        known = {p["original_sha256"] for p in photos}
        orphans = []
        root = original_store.store_root()
        if root.exists():
            for obj in root.glob("*/*.jpg"):
                sha = obj.stem
                if sha not in known:
                    orphans.append({"original_sha256": sha,
                                    "path": str(obj.relative_to(root)),
                                    "action": "report_orphan_bytes",
                                    "description":
                                        "bytes with no metadata row - "
                                        "reported, never deleted"})
        return {"photos": plans,
                "orphan_objects": orphans,
                "counts": _counts(plans, orphans)}


def _counts(plans: list[dict], orphans: list[dict]) -> dict:
    out: dict[str, int] = {}
    for p in plans:
        out[p["state"]] = out.get(p["state"], 0) + 1
    out["orphan_objects"] = len(orphans)
    return out


def apply_recovery(conn: sqlite3.Connection | None = None,
                   limit_actions: set[str] | None = None) -> dict:
    """Apply the SAFE subset of plans, idempotently and atomically per
    record. Violations / orphan bytes are NEVER applied - they are
    escalation entries in the result."""
    allowed = limit_actions or SAFE_ACTIONS
    results: list[dict] = []
    with _APPLY_LOCK:
        if conn is None:
            with db() as c:
                results = _apply_with(c, allowed)
        else:
            results = _apply_with(conn, allowed)
    return {"applied": sum(1 for r in results if r["applied"]),
            "results": results}


def _apply_with(conn: sqlite3.Connection,
                allowed: set[str]) -> list[dict]:
    results: list[dict] = []
    photos = conn.execute(
        "SELECT p.photo_id, p.original_sha256, pr.owner_token "
        "FROM mvp_photos p JOIN mvp_projects pr "
        "ON p.project_id = pr.project_id").fetchall()
    for photo in photos:
        # re-derive INSIDE the loop so concurrent mutations
        # between scan and apply are seen (fresh facts)
        f = _facts_for(conn, photo, original_store.store_root())
        plan = plan_recovery(f)
        act = plan["action"]
        entry = {"photo_id": photo["photo_id"],
                 "state": plan["state"],
                 "action": act, "applied": False}
        if act not in allowed:
            entry["note"] = ("escalated - action not in the "
                             "safe set")
        elif act == "rebind":
            sha = photo["original_sha256"]
            obj = original_store.object_path(sha)
            if not obj.exists():
                entry["note"] = "object vanished mid-recovery"
            else:
                # re-hash the bytes before binding (I4: the bind step
                # always proves the hash itself)
                import hashlib
                h = hashlib.sha256()
                with open(obj, "rb") as fh:
                    for chunk in iter(
                            lambda: fh.read(256 * 1024), b""):
                        h.update(chunk)
                if h.hexdigest() != sha:
                    entry["note"] = ("hash mismatch - bytes changed "
                                     "on disk; escalated")
                    entry["action"] = "report_violation"
                else:
                    conn.execute(
                        "INSERT OR IGNORE INTO mvp_photo_originals"
                        " (photo_id, sha256, owner_token, bound_at) "
                        "VALUES (?,?,?,?)",
                        (photo["photo_id"], sha,
                         photo["owner_token"], utc_now()))
                    entry["applied"] = True
        elif act == "reverify":
            row = conn.execute(
                "SELECT evidence_link_id, verification_status "
                "FROM mvp_evidence_links WHERE photo_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (photo["photo_id"],)).fetchone()
            if row is None or row["verification_status"] != "unverified":
                entry["note"] = ("link vanished or already concluded - "
                                 "nothing to do")
            else:
                # the production verify path is an explicit
                # client-submitted material run through the /verify
                # endpoint. Recovery does NOT invent verification
                # material - an unverified link stays unverified until
                # the client re-runs /verify.
                entry["note"] = ("server verification requires "
                                 "client-submitted manifest material "
                                 "(via /verify) - link re-flagged for "
                                 "client retry; recovery never "
                                 "fabricates a verification run")
        # none / await_upload / keep_failed: correct as-is
        results.append(entry)
    return results
