# WorkCam backend - one-command test entry (Windows).
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_tests.ps1            # full suite
#   powershell ... -Target tests\test_api.py                                             # single file
#   powershell ... -Target "tests/test_api.py::test_health"                              # single test
#   powershell ... -Seed 0                                                               # PYTHONHASHSEED=0 variant
#   powershell ... -Target tests\test_api.py -Seed 42
param(
    [string]$Target = "",
    [string]$Seed = ""
)

$ErrorActionPreference = "Stop"
$backend = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $backend

$venvPy = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Error "venv missing - run bootstrap.ps1 first"
    exit 1
}

if ($Seed -ne "") {
    $env:PYTHONHASHSEED = $Seed
    Write-Output "[run_tests] PYTHONHASHSEED=$Seed"
} else {
    Remove-Item Env:PYTHONHASHSEED -ErrorAction SilentlyContinue
}

$pytestArgs = @("-m", "pytest", "-q")
if ($Target -ne "") {
    Write-Output "[run_tests] target: $Target"
    $pytestArgs += $Target
} else {
    Write-Output "[run_tests] full suite"
}

& $venvPy @pytestArgs
exit $LASTEXITCODE
