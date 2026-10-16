param(
    [ValidateSet('backend', 'frontend', 'worker', 'test')]
    [string]$Service = 'backend',
    [string]$CondaEnv = 'cook-rag-1'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonCandidates = @(
    "D:\ProgramData\anaconda3\envs\$CondaEnv\python.exe",
    "$env:USERPROFILE\anaconda3\envs\$CondaEnv\python.exe",
    "$env:USERPROFILE\miniconda3\envs\$CondaEnv\python.exe"
)
$pythonPath = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $pythonPath) {
    $pythonPath = (Get-Command python -ErrorAction Stop).Source
}
if ($Service -eq 'frontend') {
    Set-Location (Join-Path $projectRoot 'frontend')
    & npm.cmd run dev -- --host 127.0.0.1
} else {
    Set-Location (Join-Path $projectRoot 'backend')
    if ($Service -eq 'worker') { & $pythonPath -m app.worker }
    elseif ($Service -eq 'test') {
        New-Item -ItemType Directory -Path data -Force | Out-Null
        & $pythonPath -m pytest -q --basetemp=./data/test-run
    } else { & $pythonPath -m uvicorn app.main:app --host 127.0.0.1 --port 8010 }
}
exit $LASTEXITCODE
