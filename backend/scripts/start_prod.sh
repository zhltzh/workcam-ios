#!/usr/bin/env bash
# WorkCam backend - PRODUCTION-style server launcher (macOS / Linux / Unix).
# Loads .env.production (or the file given via ENV_NAME), then starts
# uvicorn WITHOUT hot reload, multi-worker, bound to loopback ONLY
# (this service must never be exposed to the public internet - see
# docs/mvp/w009-deployment.md).
# Usage:
#   ./scripts/start_prod.sh
#   WORKERS=2 PORT=8000 LOG_CONFIG=scripts/logging_rotate.json ./scripts/start_prod.sh
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_NAME="${ENV_NAME:-production}"
PORT="${PORT:-8000}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
WORKERS="${WORKERS:-4}"
LOG_LEVEL="${LOG_LEVEL:-info}"
LOG_CONFIG="${LOG_CONFIG:-}"

ENV_FILE=".env.${ENV_NAME}"
if [[ -f "$ENV_FILE" ]]; then
    echo "[start_prod] loaded $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" | sed -e 's/^export //' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//")
    set +a
else
    echo "[start_prod] WARNING: $ENV_FILE not found - using current shell environment. Copy .env.${ENV_NAME}.example to .env.${ENV_NAME} to configure." >&2
fi

if [[ ! -x ".venv/bin/python" ]]; then
    echo "[start_prod] venv missing - run ./bootstrap.sh first" >&2
    exit 1
fi

LOG_ARGS=()
if [[ -n "$LOG_CONFIG" ]]; then
    mkdir -p logs
    echo "[start_prod] log config: $LOG_CONFIG"
    LOG_ARGS=(--log-config "$LOG_CONFIG")
fi

echo "[start_prod] uvicorn app.main:app x${WORKERS} workers on ${BIND_HOST}:${PORT} (env=${ENV_NAME}, no reload)"
exec .venv/bin/python -m uvicorn app.main:app \
    --host "$BIND_HOST" --port "$PORT" --workers "$WORKERS" \
    --log-level "$LOG_LEVEL" "${LOG_ARGS[@]}"
