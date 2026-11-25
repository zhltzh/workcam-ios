#!/usr/bin/env bash
# WorkCam backend - one-command test entry (macOS / Linux / Unix).
# Usage:
#   ./scripts/run_tests.sh                       # full suite
#   ./scripts/run_tests.sh tests/test_api.py     # single file / node
#   ./scripts/run_tests.sh --seed 0              # PYTHONHASHSEED=0 variant
#   ./scripts/run_tests.sh --seed 42 tests/test_api.py
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x ".venv/bin/python" ]]; then
    echo "[run_tests] venv missing - run ./bootstrap.sh first" >&2
    exit 1
fi

SEED=""
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --seed)
            SEED="$2"
            shift 2
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ -n "$SEED" ]]; then
    export PYTHONHASHSEED="$SEED"
    echo "[run_tests] PYTHONHASHSEED=$SEED"
else
    unset PYTHONHASHSEED || true
fi

if [[ ${#ARGS[@]} -eq 0 ]]; then
    echo "[run_tests] full suite"
else
    echo "[run_tests] target: ${ARGS[*]}"
fi

exec .venv/bin/python -m pytest -q "${ARGS[@]}"
