#!/bin/bash
# TM-04 A07: Mac ONE-COMMAND intake + test runner.
# Run from anywhere inside the repo: bash handoff/mac-ready/mac-intake-onecommand.sh
#
# What it does (in dependency order, all pinned, no personal dirs):
#   1. backend bootstrap   (venv + pinned requirements.txt + DB v1..v14)
#   2. full backend tests   (pytest -q, the same suite the Windows
#                            gate runs; numbers must match the report)
#   3. Swift package tests  (swift test --package-path swift/WorkCamCore;
#                            golden vectors are packaged, no downloads)
#   4. live-HTTP contract   (scripts/win_hotfix_live.py, loopback only)
#
# Honest reporting: every step prints STEP-n PASS/FAIL; the script
# never masks a failure - exit code is non-zero if ANY step failed.
# Report the four lines back; that is the entire Mac intake.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
FAILED=0
declare -a RESULTS

step() { # step <n> <label> <command...>
    local n="$1"; shift
    local label="$1"; shift
    echo ""
    echo "== STEP $n: $label =="
    if "$@"; then
        RESULTS+=("STEP $n PASS  $label")
    else
        RESULTS+=("STEP $n FAIL  $label")
        FAILED=1
    fi
}

echo "WorkCam Mac one-command intake (TM-04 A07)"
echo "repo: $(pwd)"
git rev-parse --short HEAD || true

# 1. backend bootstrap (idempotent; pinned deps; DB v1..v14)
step 1 "backend bootstrap (venv + pinned requirements + migrations)" \
    bash backend/bootstrap.sh

# 2. backend full suite
if [ -d "backend/.venv" ]; then
    step 2 "backend full test suite (pytest -q)" \
        bash -c "source backend/.venv/bin/activate && cd backend && python -m pytest -q"
fi

# 3. Swift package tests (golden vectors packaged in-repo)
step 3 "WorkCamCore Swift tests" \
    swift test --package-path swift/WorkCamCore

# 4. live-HTTP contract check (loopback, no external network)
if [ -d "backend/.venv" ]; then
    step 4 "live HTTP contract (win_hotfix_live)" \
        bash -c "source backend/.venv/bin/activate && cd backend && python scripts/win_hotfix_live.py ."
fi

echo ""
echo "================ INTAKE SUMMARY ================"
for r in "${RESULTS[@]}"; do echo "$r"; done
if [ "$FAILED" -ne 0 ]; then
    echo "RESULT: FAIL (report the lines above back)"
    exit 1
fi
echo "RESULT: PASS (paste this summary as the Mac intake report)"
