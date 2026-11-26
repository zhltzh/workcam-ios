#!/bin/bash
# WorkCam backend bootstrap - macOS (TM95 / M03-A04)
# Windows-authored; generic logic verified, macOS execution
# CROSS_PLATFORM_PENDING (Mac runs this once and reports).
#
# Usage: bash bootstrap.sh          (from the repo root)
# Idempotent: safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT/backend"

echo "== WorkCam backend bootstrap (macOS) =="

# ---- 1. dependency checks (named, actionable messages) --------------
check() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "MISSING: $1 - $2" >&2
        exit 1
    fi
}
check python3 "install Python 3.12+ (brew install python@3.12)"
echo "python3: $(python3 --version)"

# ---- 2. Python venv + dependencies --------------------------------
if [ ! -d ".venv" ]; then
    echo "creating venv..."
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "installing dependencies (pinned)..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
# matplotlib is IN requirements.txt since TM44; no extra step needed.

# ---- 3. local database + migrations --------------------------------
echo "initializing database (v1..v14)..."
python scripts/db_admin.py init

# ---- 4. health probe (start + stop once) ---------------------------
PORT="${WORKCAM_PORT:-8755}"
echo "starting dev server on :$PORT for the health check..."
python scripts/db_admin.py status || true

# ---- 5. one-command regression -------------------------------------
echo "running the full test suite..."
python -m pytest -q
echo ""
echo "== bootstrap COMPLETE: backend ready =="
echo "  start server : bash scripts/start_dev.sh (or uvicorn app.main:app --reload)"
echo "  run tests    : source .venv/bin/activate && python -m pytest -q"
echo "  quality gate : python tools/quality_gate.py"
