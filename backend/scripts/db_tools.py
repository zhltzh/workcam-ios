"""Backup / verify / restore CLI for the WorkCam backend.

Wraps the EXISTING logic only:
  - app.db.backup_to / verify_backup / restore_from
      (VACUUM INTO consistent online backup; restore refuses to
       overwrite a non-empty database)
  - app.services.original_store.backup / restore
      (manifest-driven, hash-verified originals copy)

Joint backup layout inside --out-dir:
  workcam.db      consistent DB snapshot (VACUUM INTO)
  originals/      content-addressed object copies (ab/<sha>.jpg)
  manifest.json   originals manifest + DB verification info + timestamp
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.db as dbm  # noqa: E402
from app.services import original_store  # noqa: E402

DB_NAME = "workcam.db"
ORIGINALS_DIR = "originals"
MANIFEST_NAME = "manifest.json"


def cmd_backup(args: argparse.Namespace) -> int:
    db_path = Path(args.db) if args.db else dbm._db_path()
    out = Path(args.out_dir)
    db_backup = out / DB_NAME
    pages = dbm.backup_to(db_backup, db_path)
    info = dbm.verify_backup(db_backup)
    manifest = original_store.backup(out / ORIGINALS_DIR)
    full = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_db": str(db_path),
        "db": {"path": str(db_backup), "pages": pages, **info},
        "originals": manifest,
    }
    (out / MANIFEST_NAME).write_text(
        json.dumps(full, indent=2), encoding="utf-8")
    print(f"[backup] db   : {db_backup} (pages={pages}, "
          f"v{info['version']}, counts={info['counts']})")
    print(f"[backup] origs: {len(manifest['objects'])} object(s) copied, "
          f"{len(manifest['failed'])} failed")
    print(f"[backup] manifest: {out / MANIFEST_NAME}")
    if manifest["failed"]:
        print(f"[backup] WARNING failed objects: {manifest['failed'][:5]}")
        return 1
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    backup_db = Path(args.backup_db)
    info = dbm.verify_backup(backup_db)
    print(f"[verify] {backup_db}: integrity ok, FK audit clean, "
          f"v{info['version']}, counts={info['counts']}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    backup_dir = Path(args.backup_dir)
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.exists():
        print(f"[restore] {manifest_path} not found", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = Path(args.target)
    info = dbm.restore_from(backup_dir / DB_NAME, target)
    res = original_store.restore(backup_dir / ORIGINALS_DIR,
                                 manifest["originals"])
    print(f"[restore] db   : -> {target} (v{info['version']}, "
          f"counts={info['counts']})")
    print(f"[restore] origs: {res['objects']} restored, "
          f"{res['skipped_intact']} skipped (already intact)")
    if res["refused"]:
        print(f"[restore] WARNING refused: {res['refused'][:5]}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="WorkCam backup/verify/restore CLI "
                    "(wraps app.db + original_store)")
    parser.add_argument("--db", default=None,
                        help="business DB path (default: "
                             "WORKCAM_BUSINESS_DB)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_backup = sub.add_parser("backup", help="DB + originals joint backup")
    p_backup.add_argument("--out-dir", required=True,
                          help="backup output directory")
    p_verify = sub.add_parser("verify", help="verify a DB backup file")
    p_verify.add_argument("--backup-db", required=True,
                          help="path of the backup DB file")
    p_restore = sub.add_parser("restore", help="restore DB + originals")
    p_restore.add_argument("--backup-dir", required=True,
                           help="directory written by 'backup'")
    p_restore.add_argument("--target", required=True,
                           help="FRESH target DB path (must not exist or "
                                "be empty - restore never overwrites)")
    args = parser.parse_args()
    return {"backup": cmd_backup, "verify": cmd_verify,
            "restore": cmd_restore}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
