# WorkCam backend - backup launcher (Windows).
# Thin wrapper around scripts/db_tools.py backup (which wraps the
# EXISTING app.db.backup_to + original_store.backup logic).
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup.ps1 -OutDir ..\data\backups\2026-11-25
#   powershell ... -Db data\prod\business\workcam.db
param(
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$DbPath = ""
)

$ErrorActionPreference = "Stop"
$backend = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $backend

$venvPy = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Error "venv missing - run bootstrap.ps1 first"
    exit 1
}

$args_ = @("$PSScriptRoot\db_tools.py", "--db", $DbPath, "backup",
           "--out-dir", $OutDir)
& $venvPy @args_
exit $LASTEXITCODE
