"""Database administration CLI for the WorkCam backend.

Wraps the EXISTING migration chain in app/db.py (PRAGMA user_version,
idempotent, replayable). No schema logic lives here.

Commands:
  init      create the database (if needed) and migrate to latest
  migrate   apply pending migrations; --to N applies versions 1..N only
            (useful to build a historical-version database for rollback
            drills; DOWNGRADE of an existing database is NOT supported -
            rollback = restore from backup)
  status    show user_version, applied/pending migrations, tables
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.db as dbm  # noqa: E402


def _resolve_db(args: argparse.Namespace) -> Path:
    return Path(args.db) if args.db else dbm._db_path()


def _migrate_to(path: Path, to_version: int | None) -> int:
    if to_version is None:
        return dbm.migrate(path)
    latest = max(dbm.MIGRATIONS)
    if to_version > latest:
        raise SystemExit(f"--to {to_version} exceeds latest migration v{latest}")
    saved = dict(dbm.MIGRATIONS)
    try:
        for v in [v for v in saved if v > to_version]:
            dbm.MIGRATIONS.pop(v)
        return dbm.migrate(path)
    finally:
        dbm.MIGRATIONS.clear()
        dbm.MIGRATIONS.update(saved)


def cmd_init(args: argparse.Namespace) -> int:
    path = _resolve_db(args)
    applied = dbm.migrate(path)
    print(f"[init] db={path}")
    print(f"[init] applied {applied} migration(s), "
          f"user_version={_version(path)}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    path = _resolve_db(args)
    before = _version(path)
    applied = _migrate_to(path, args.to)
    print(f"[migrate] db={path} {before} -> {_version(path)} "
          f"({applied} applied)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    path = _resolve_db(args)
    version = _version(path)
    print(f"[status] db={path}")
    print(f"[status] user_version={version} "
          f"({'not initialized' if version == 0 else f'v{version}'})")
    print(f"[status] latest migration=v{max(dbm.MIGRATIONS)}")
    for v in sorted(dbm.MIGRATIONS):
        mark = "applied" if v <= version else "PENDING "
        print(f"  v{v:<3} {mark}")
    if path.exists():
        conn = dbm.connect(path)
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            print(f"[status] tables: {', '.join(tables) or '(none)'}")
        finally:
            conn.close()
    else:
        print("[status] file does not exist yet")
    return 0


def _version(path: Path) -> int:
    if not path.exists():
        return 0
    conn = dbm.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="WorkCam business-DB init/migrate/status CLI")
    parser.add_argument("--db", default=os.environ.get("WORKCAM_BUSINESS_DB"),
                        help="database path (default: WORKCAM_BUSINESS_DB "
                             "or data/business/workcam.db)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create + migrate to latest")
    p_migrate = sub.add_parser("migrate", help="apply pending migrations")
    p_migrate.add_argument("--to", type=int, default=None,
                           help="apply only versions up to N (fresh/older "
                                "DB; no downgrade)")
    sub.add_parser("status", help="show version + table inventory")
    args = parser.parse_args()
    return {"init": cmd_init, "migrate": cmd_migrate,
            "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
