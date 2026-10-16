param([string]$CondaEnv = 'cook-rag-1', [switch]$WithModels, [switch]$WithEvaluation)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
$extras = @('dev')
if ($WithModels) { $extras += 'ml' }
if ($WithEvaluation) { $extras += 'evaluation' }
$dependency = ".[$($extras -join ',')]"
Set-Location (Join-Path $projectRoot 'backend')
& conda run -n $CondaEnv python -m pip install -e $dependency
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Set-Location (Join-Path $projectRoot 'frontend')
& npm.cmd ci
exit $LASTEXITCODE
