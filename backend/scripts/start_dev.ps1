# WorkCam backend - local DEV server launcher (Windows).
# Loads .env.development (or the file passed via -Env), then starts
# uvicorn with hot reload, bound to loopback ONLY.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_dev.ps1
#   powershell ... -Port 8001 -Env production     # any env file suffix
param(
    [string]$Env = "development",
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [string]$LogLevel = "info"
)

$ErrorActionPreference = "Stop"
$backend = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $backend

function Import-EnvFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim()
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or
            ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        Set-Item -Path ("Env:" + $key) -Value $val
    }
    return $true
}

$envFile = Join-Path $backend ".env.$Env"
if (Import-EnvFile $envFile) {
    Write-Output "[start_dev] loaded $envFile"
} else {
    Write-Warning "[start_dev] $envFile not found - using current shell environment. Copy .env.$Env.example to .env.$Env to configure."
}

$venvPy = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Error "venv missing - run bootstrap.ps1 first"
    exit 1
}

Write-Output "[start_dev] uvicorn app.main:app --reload on ${BindHost}:${Port} (env=$Env)"
& $venvPy -m uvicorn app.main:app --reload --host $BindHost --port $Port --log-level $LogLevel
exit $LASTEXITCODE
