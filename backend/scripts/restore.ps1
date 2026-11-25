# WorkCam backend - restore launcher (Windows).
# Thin wrapper around scripts/db_tools.py restore (which wraps the
# EXISTING app.db.restore_from + original_store.restore logic).
# The target must be a FRESH path - restore never overwrites live data.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restore.ps1 -BackupDir ..\data\backups\2026-11-25 -Target data\prod\business\workcam-restored.db
param(
    [Parameter(Mandatory = $true)][string]$BackupDir,
    [Parameter(Mandatory = $true)][string]$Target
)

$ErrorActionPreference = "Stop"
$backend = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $backend

$venvPy = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Error "venv missing - run bootstrap.ps1 first"
    exit 1
}

$args_ = @("$PSScriptRoot\db_tools.py", "restore",
           "--backup-dir", $BackupDir, "--target", $Target)
& $venvPy @args_
exit $LASTEXITCODE
