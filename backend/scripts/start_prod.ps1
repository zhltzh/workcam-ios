# WorkCam backend - PRODUCTION-style server launcher (Windows).
# Loads .env.production (or the file passed via -Env), then starts
# uvicorn WITHOUT hot reload, multi-worker, bound to loopback ONLY
# (this service must never be exposed to the public internet - see
# docs/mvp/w009-deployment.md).
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_prod.ps1
#   powershell ... -Workers 2 -Port 8000 -LogConfig scripts\logging_rotate.json
param(
    [string]$Env = "production",
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [int]$Workers = 4,
    [string]$LogLevel = "info",
    [string]$LogConfig = ""
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
    Write-Output "[start_prod] loaded $envFile"
} else {
    Write-Warning "[start_prod] $envFile not found - using current shell environment. Copy .env.$Env.example to .env.$Env to configure."
}

$venvPy = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Error "venv missing - run bootstrap.ps1 first"
    exit 1
}

$uvArgs = @("-m", "uvicorn", "app.main:app", "--host", $BindHost,
            "--port", $Port, "--workers", $Workers, "--log-level", $LogLevel)
if ($LogConfig -ne "") {
    New-Item -ItemType Directory -Force -Path (Join-Path $backend "logs") | Out-Null
    Write-Output "[start_prod] log config: $LogConfig"
    $uvArgs += @("--log-config", $LogConfig)
}

Write-Output "[start_prod] uvicorn app.main:app x${Workers} workers on ${BindHost}:${Port} (env=$Env, no reload)"
& $venvPy @uvArgs
exit $LASTEXITCODE
