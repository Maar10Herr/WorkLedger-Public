param([string]$Destination = "")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (!(Test-Path .env)) { throw "Missing .env; run start.ps1 first." }
Get-Content .env | Where-Object { $_ -match '^[A-Za-z_][A-Za-z0-9_]*=' } | ForEach-Object {
  $name, $value = $_ -split '=', 2; Set-Item -Path "Env:$name" -Value $value
}
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
if (!$Destination) { $Destination = Join-Path ($env:WORKLEDGER_BACKUP_DIR ?? ".\workledger-backups") "workledger-$timestamp" }
$dataDir = $env:WORKLEDGER_DATA_DIR ?? ".\workledger-data"
if (!(Test-Path $dataDir -PathType Container)) { throw "Data directory not found: $dataDir" }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$dbDump = Join-Path $Destination "database.dump"
Write-Host "Pausing application writers for a consistent database/file snapshot..."
docker compose stop web worker beat
if ($LASTEXITCODE -ne 0) { throw "Could not stop application writers" }
try {
  docker compose exec -T postgres rm -f /tmp/workledger-backup.dump
  docker compose exec -T postgres pg_dump --username $env:POSTGRES_USER --dbname $env:POSTGRES_DB --format=custom --file=/tmp/workledger-backup.dump
  if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
  docker compose cp postgres:/tmp/workledger-backup.dump $dbDump
  if ($LASTEXITCODE -ne 0) { throw "Could not copy database dump" }
  docker compose exec -T postgres rm -f /tmp/workledger-backup.dump
  tar -C $dataDir -czf (Join-Path $Destination "data.tar.gz") .
  if ($LASTEXITCODE -ne 0) { throw "File archive failed" }
  tar -czf (Join-Path $Destination "config.tar.gz") compose.yaml compose.review.yaml .env.example README.md
  if ($LASTEXITCODE -ne 0) { throw "Configuration archive failed" }
} finally {
  docker compose up -d web worker beat | Out-Null
}
$dbHash = (Get-FileHash $dbDump -Algorithm SHA256).Hash.ToLower()
$dataHash = (Get-FileHash (Join-Path $Destination "data.tar.gz") -Algorithm SHA256).Hash.ToLower()
$configHash = (Get-FileHash (Join-Path $Destination "config.tar.gz") -Algorithm SHA256).Hash.ToLower()
@("format=workledger-backup-v2", "created_at=$timestamp", "database_sha256=$dbHash", "data_sha256=$dataHash", "config_sha256=$configHash") | Set-Content (Join-Path $Destination "manifest.txt")
Write-Host "Backup complete: $Destination"
