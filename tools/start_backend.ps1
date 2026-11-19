# WorkCam local backend launcher (Windows).
# Creates/reuses a virtualenv, installs dependencies, starts FastAPI.
# TEST/DEVELOPMENT ONLY - do not use as a production launcher.

param(
    [int]$Port = 8000,
    [string]$Host = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$BackendDir = Split-Path -Parent $PSScriptRoot

Set-Location $BackendDir

if (-not (Test-Path ".venv")) {
    Write-Host "[setup] creating virtualenv .venv ..."
    py -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "failed to create virtualenv (is Python 3.12+ installed?)" }
}

Write-Host "[setup] installing dependencies ..."
& ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host ""
Write-Host "[start] WorkCam verification service on http://${Host}:${Port}"
Write-Host "[start] WARNING: experimental prototype, not for production."
& ".venv\Scripts\python.exe" -m uvicorn app.main:app --host $Host --port $Port
