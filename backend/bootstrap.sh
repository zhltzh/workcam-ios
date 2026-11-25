#!/usr/bin/env bash
# WorkCam backend bootstrap (macOS / Linux / Unix).
# Creates the local virtualenv and installs dependencies.
# Usage:
#   ./bootstrap.sh            # install from requirements.txt
#   ./bootstrap.sh --locked   # install from requirements.lock.txt
set -euo pipefail
cd "$(dirname "$0")"

REQ="requirements.txt"
if [[ "${1:-}" == "--locked" ]]; then
    REQ="requirements.lock.txt"
fi

# ---- locate Python 3.12+ -----------------------------------------------
PY=""
for candidate in python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    fi
done
if [[ -z "$PY" ]]; then
    echo "[bootstrap] Python 3.12+ not found. Install it first (macOS: brew install python@3.12)." >&2
    exit 1
fi
echo "[bootstrap] using $($PY --version)"

# ---- venv ---------------------------------------------------------------
if [[ -x ".venv/bin/python" ]]; then
    echo "[bootstrap] .venv already exists - reusing it"
else
    echo "[bootstrap] creating .venv"
    "$PY" -m venv .venv
fi
VENV_PY=".venv/bin/python"

# ---- dependencies ------------------------------------------------------
echo "[bootstrap] installing from $REQ"
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install -r "$REQ"

echo "[bootstrap] done. Next steps:"
echo "  1. cp .env.development.example backend/.env.development (or .env.production)"
echo "  2. start dev server:  ./scripts/start_dev.sh"
echo "  3. run tests:         ./scripts/run_tests.sh"
