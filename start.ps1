$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    $secret = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    $dbPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)).ToLowerInvariant()
    $appDbPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)).ToLowerInvariant()
    $content = Get-Content ".env" -Raw
    $content = $content.Replace("CHANGE_ME_generate_a_long_random_value", $secret).Replace("CHANGE_ME_database_password", $dbPassword).Replace("CHANGE_ME_application_database_password", $appDbPassword)
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), $content)
    Write-Host "Created .env with generated secrets."
}

function Get-DotEnvValue([string] $Name) {
    if (-not (Test-Path ".env")) { return "" }
    $escapedName = [regex]::Escape($Name)
    $line = Get-Content ".env" | Where-Object { $_ -match "^\s*$escapedName\s*=" } | Select-Object -Last 1
    if ($null -eq $line) { return "" }
    $value = ($line -replace "^\s*$escapedName\s*=", "").Trim()
    if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    return $value
}

function Get-Setting([string] $Name) {
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) { $value = Get-DotEnvValue $Name }
    return $value
}

function Assert-SafeDirectory([string] $Value, [string] $Name) {
    try { $fullPath = [IO.Path]::GetFullPath($Value) } catch { throw "$Name is not a valid path." }
    if ([IO.Path]::GetPathRoot($fullPath) -eq $fullPath) {
        throw "$Name must not be a filesystem root."
    }
}

$dataDir = Get-Setting "WORKLEDGER_DATA_DIR"
$backupDir = Get-Setting "WORKLEDGER_BACKUP_DIR"
if ([string]::IsNullOrWhiteSpace($dataDir)) { $dataDir = ".\workledger-data" }
if ([string]::IsNullOrWhiteSpace($backupDir)) { $backupDir = ".\workledger-backups" }
Assert-SafeDirectory $dataDir "WORKLEDGER_DATA_DIR"
Assert-SafeDirectory $backupDir "WORKLEDGER_BACKUP_DIR"
$portText = Get-Setting "WORKLEDGER_PORT"
if ([string]::IsNullOrWhiteSpace($portText)) { $portText = "8787" }
if ($portText -notmatch '^\d+$') { throw "WORKLEDGER_PORT must be a decimal port number." }
$port = [int]$portText
if ($port -lt 1 -or $port -gt 65535) { throw "WORKLEDGER_PORT must be between 1 and 65535." }
@("attachments", "previews", "exports", "backups") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $dataDir $_) | Out-Null
}
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
docker compose up --build --detach
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose ps
Write-Host "WorkLedger: http://127.0.0.1:$port"
