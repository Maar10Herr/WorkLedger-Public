param([switch]$Volumes)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if ($Volumes) { docker compose down --volumes } else { docker compose down }
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
