#!/usr/bin/env bash
# WorkCam backend - local DEV server launcher (macOS / Linux / Unix).
# Loads .env.development (or the file given via ENV_NAME), then starts
# uvicorn with hot reload, bound to loopback ONLY.
# Usage:
#   ./scripts/start_dev.sh
#   ENV_NAME=production PORT=8001 ./scripts/start_dev.sh
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_NAME="${ENV_NAME:-development}"
PORT="${PORT:-8000}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
LOG_LEVEL="${LOG_LEVEL:-info}"

ENV_FILE=".env.${ENV_NAME}"
if [[ -f "$ENV_FILE" ]]; then
    echo "[start_dev] loaded $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" | sed -e 's/^export //' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//")
    set +a
else
    echo "[start_dev] WARNING: $ENV_FILE not found - using current shell environment. Copy .env.${ENV_NAME}.example to .env.${ENV_NAME} to configure." >&2
fi

if [[ ! -x ".venv/bin/python" ]]; then
    echo "[start_dev] venv missing - run ./bootstrap.sh first" >&2
    exit 1
fi

echo "[start_dev] uvicorn app.main:app --reload on ${BIND_HOST}:${PORT} (env=${ENV_NAME})"
exec .venv/bin/python -m uvicorn app.main:app --reload \
    --host "$BIND_HOST" --port "$PORT" --log-level "$LOG_LEVEL"
