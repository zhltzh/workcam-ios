#!/usr/bin/env bash
# WorkCam backend - restore launcher (macOS / Linux / Unix).
# Thin wrapper around scripts/db_tools.py restore (which wraps the
# EXISTING app.db.restore_from + original_store.restore logic).
# The target must be a FRESH path - restore never overwrites live data.
# Usage:
#   ./scripts/restore.sh BACKUP_DIR TARGET_DB
#   WORKCAM_BUSINESS_DB=<TARGET_DB> ./scripts/restore.sh backup-dir target.db
#   (setting WORKCAM_BUSINESS_DB to the restore target also restores
#    originals into the right store location)
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x ".venv/bin/python" ]]; then
    echo "[restore] venv missing - run ./bootstrap.sh first" >&2
    exit 1
fi

BACKUP_DIR="${1:?usage: restore.sh BACKUP_DIR TARGET_DB}"
TARGET_DB="${2:?usage: restore.sh BACKUP_DIR TARGET_DB}"

exec .venv/bin/python scripts/db_tools.py restore \
    --backup-dir "$BACKUP_DIR" --target "$TARGET_DB"
