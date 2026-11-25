# WorkCam backend bootstrap (Windows PowerShell 5.1+).
# Creates the local virtualenv and installs dependencies.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1
#   powershell ... -File bootstrap.ps1 -Locked          # use requirements.lock.txt
#   powershell ... -File bootstrap.ps1 -Proxy http://127.0.0.1:10809
param(
    [string]$Proxy = "",
    [switch]$Locked
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

# ---- locate Python 3.12+ -----------------------------------------------
$py = $null
try {
    $v = py -V:3.12 --version 2>$null
    if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(\d+)") { $py = "py -V:3.12" }
} catch {}
if (-not $py) {
    try {
        $v = python3.12 --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(\d+)") { $py = "python3.12" }
    } catch {}
}
if (-not $py) {
    try {
        $v = python --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(1[2-9]|[2-9]\d+)") { $py = "python" }
    } catch {}
}
if (-not $py) {
    Write-Error "Python 3.12+ not found. Install from https://www.python.org/downloads/ (tick 'Add to PATH')."
    exit 1
}
Write-Output "[bootstrap] using $($py)"

# ---- venv ---------------------------------------------------------------
if (Test-Path -LiteralPath ".venv\Scripts\python.exe") {
    Write-Output "[bootstrap] .venv already exists - reusing it"
} else {
    Write-Output "[bootstrap] creating .venv"
    Invoke-Expression "$py -m venv .venv"
    if ($LASTEXITCODE -ne 0) { Write-Error "venv creation failed"; exit 1 }
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

# ---- dependencies ------------------------------------------------------
$req = if ($Locked) { "requirements.lock.txt" } else { "requirements.txt" }
Write-Output "[bootstrap] installing from $req"
$installArgs = @("-m", "pip", "install", "-r", $req)
if ($Proxy -ne "") {
    Write-Output "[bootstrap] using proxy $Proxy"
    & $venvPy @installArgs "--proxy" $Proxy
} else {
    & $venvPy @installArgs
}
if ($LASTEXITCODE -ne 0) { Write-Error "dependency installation failed"; exit 1 }

Write-Output "[bootstrap] done. Next steps:"
Write-Output "  1. copy .env.development.example to backend\.env.development (or .env.production)"
Write-Output "  2. start dev server:  powershell -File scripts\start_dev.ps1"
Write-Output "  3. run tests:         powershell -File scripts\run_tests.ps1"
