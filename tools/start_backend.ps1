# WorkCam local backend launcher (Windows).
# Creates/reuses a virtualenv, installs dependencies, starts FastAPI.
# TEST/DEVELOPMENT ONLY - do not use as a production launcher.
#
# Works from ANY working directory: locations are derived from the script
# location (tools/ -> repo root -> backend), never from the caller's CWD.
#
# Usage (repo root or anywhere):
#   powershell -ExecutionPolicy Bypass -File tools\start_backend.ps1
#   powershell -ExecutionPolicy Bypass -File tools\start_backend.ps1 -Port 8000 -ListenHost 0.0.0.0
#
# Exit codes: 0 on clean shutdown, non-zero on any setup or startup failure.

param(
    [int]$Port = 8000,
    [string]$ListenHost = "127.0.0.1"   # NOTE: $Host is a read-only automatic
                                        # variable in PowerShell and cannot be
                                        # used as a parameter name.
)

$ErrorActionPreference = "Stop"

# --- locate repo root and backend directory (from the script location) ------
$RepoRoot  = Split-Path -Parent $PSScriptRoot      # tools\.. -> repo root
$BackendDir = Join-Path $RepoRoot "backend"

if (-not (Test-Path (Join-Path $BackendDir "app\main.py"))) {
    Write-Host "[error] backend not found at: $BackendDir" -ForegroundColor Red
    exit 1
}

Set-Location $BackendDir

# --- virtualenv --------------------------------------------------------------
$PyExe = Join-Path $BackendDir ".venv\Scripts\python.exe"

if (-not (Test-Path $PyExe)) {
    Write-Host "[setup] creating virtualenv at $BackendDir\.venv ..."
    py -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[error] failed to create virtualenv (is Python 3.12+ installed?)" -ForegroundColor Red
        exit 1
    }
}

# --- dependencies -------------------------------------------------------------
$ReqPath = Join-Path $BackendDir "requirements.txt"
if (-not (Test-Path $ReqPath)) {
    Write-Host "[error] requirements.txt not found at: $ReqPath" -ForegroundColor Red
    exit 1
}

Write-Host "[setup] installing dependencies ..."
# Bounded network behaviour: pip must fail fast (non-zero) instead of
# hanging for minutes on slow/broken networks.
& $PyExe -m pip install --quiet --disable-pip-version-check --no-input --timeout 15 --retries 2 -r $ReqPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "[error] pip install failed" -ForegroundColor Red
    exit 1
}

# --- start --------------------------------------------------------------------
Write-Host ""
Write-Host "[start] WorkCam verification service on http://${ListenHost}:${Port}"
Write-Host "[start] WARNING: experimental prototype, not for production."
& $PyExe -m uvicorn app.main:app --host $ListenHost --port $Port
$Code = $LASTEXITCODE
if ($Code -ne 0) {
    Write-Host "[error] uvicorn exited with code $Code" -ForegroundColor Red
    exit $Code
}
exit 0
