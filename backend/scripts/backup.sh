#!/usr/bin/env bash
# WorkCam backend - backup launcher (macOS / Linux / Unix).
# Thin wrapper around scripts/db_tools.py backup (which wraps the
# EXISTING app.db.backup_to + original_store.backup logic).
# Usage:
#   ./scripts/backup.sh /srv/workcam/backup/2026-11-25 [db_path]
#   ./scripts/backup.sh out-dir            # uses WORKCAM_BUSINESS_DB
#   WORKCAM_BUSINESS_DB=data/prod/business/workcam.db ./scripts/backup.sh out-dir
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x ".venv/bin/python" ]]; then
    echo "[backup] venv missing - run ./bootstrap.sh first" >&2
    exit 1
fi

OUT_DIR="${1:?usage: backup.sh OUT_DIR [DB_PATH]}"
DB_PATH="${2:-}"

exec .venv/bin/python scripts/db_tools.py --db "$DB_PATH" backup \
    --out-dir "$OUT_DIR"
