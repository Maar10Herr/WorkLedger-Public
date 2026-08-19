param([Parameter(Mandatory=$true)][string]$BackupDirectory, [switch]$Yes)
$ErrorActionPreference = "Stop"
if (!$Yes) { throw "Restore is destructive. Re-run with -Yes -BackupDirectory PATH." }
Set-Location $PSScriptRoot
if (!(Test-Path .env)) { throw "Missing .env" }
Get-Content .env | Where-Object { $_ -match '^[A-Za-z_][A-Za-z0-9_]*=' } | ForEach-Object {
  $name, $value = $_ -split '=', 2; Set-Item -Path "Env:$name" -Value $value
}
$manifest = @{}
Get-Content (Join-Path $BackupDirectory "manifest.txt") | ForEach-Object { $k,$v = $_ -split '=',2; $manifest[$k]=$v }
if ($manifest.format -ne "workledger-backup-v2") { throw "Unsupported backup format" }
$dbDump = Join-Path $BackupDirectory "database.dump"
$dataArchive = Join-Path $BackupDirectory "data.tar.gz"
$configArchive = Join-Path $BackupDirectory "config.tar.gz"
if ((Get-FileHash $dbDump -Algorithm SHA256).Hash.ToLower() -ne $manifest.database_sha256) { throw "Database checksum mismatch" }
if ((Get-FileHash $dataArchive -Algorithm SHA256).Hash.ToLower() -ne $manifest.data_sha256) { throw "Data checksum mismatch" }
if ((Get-FileHash $configArchive -Algorithm SHA256).Hash.ToLower() -ne $manifest.config_sha256) { throw "Config checksum mismatch" }
$dataDir = $env:WORKLEDGER_DATA_DIR ?? ".\workledger-data"
if (!$dataDir -or $dataDir -in @("\", "/", ".")) { throw "Unsafe data directory" }
$members = tar -tzf $dataArchive
if ($LASTEXITCODE -ne 0) { throw "Invalid data archive" }
foreach ($member in $members) {
  if ($member.StartsWith("/") -or $member -match '(^|/)\.\.(/|$)') { throw "Unsafe archive member: $member" }
}
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$staging = "$dataDir.restore-staging-$stamp"
$rollback = "$dataDir.pre-restore-$stamp"
if ((Test-Path $staging) -or (Test-Path $rollback)) { throw "Restore staging path already exists" }
New-Item -ItemType Directory -Force -Path $staging | Out-Null
tar -C $staging -xzf $dataArchive --no-same-owner
if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force $staging; throw "Could not stage file archive" }
Write-Host "Stopping application writers..."
docker compose stop web worker beat
docker compose up -d postgres
try {
  docker compose exec -T postgres rm -f /tmp/workledger-pre-restore.dump /tmp/workledger-restore.dump
  docker compose exec -T postgres pg_dump --username $env:POSTGRES_USER --dbname $env:POSTGRES_DB --format=custom --file=/tmp/workledger-pre-restore.dump
  if ($LASTEXITCODE -ne 0) { throw "Could not create restore rollback dump" }
  docker compose cp $dbDump postgres:/tmp/workledger-restore.dump
  if ($LASTEXITCODE -ne 0) { throw "Could not copy restore dump" }
  docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error --username $env:POSTGRES_USER --dbname $env:POSTGRES_DB /tmp/workledger-restore.dump
  if ($LASTEXITCODE -ne 0) {
    docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error --username $env:POSTGRES_USER --dbname $env:POSTGRES_DB /tmp/workledger-pre-restore.dump
    throw "Database restore failed; the pre-restore database was restored"
  }
  if (Test-Path $dataDir) { Move-Item $dataDir $rollback }
  try { Move-Item $staging $dataDir } catch {
    docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error --username $env:POSTGRES_USER --dbname $env:POSTGRES_DB /tmp/workledger-pre-restore.dump
    if (Test-Path $rollback) { Move-Item $rollback $dataDir }
    throw
  }
  docker compose run --rm web python manage.py verify_integrity
  if ($LASTEXITCODE -ne 0) {
    docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error --username $env:POSTGRES_USER --dbname $env:POSTGRES_DB /tmp/workledger-pre-restore.dump
    Remove-Item -Recurse -Force $dataDir
    if (Test-Path $rollback) { Move-Item $rollback $dataDir }
    throw "Post-restore integrity verification failed; restore was rolled back"
  }
} finally {
  docker compose exec -T postgres rm -f /tmp/workledger-pre-restore.dump /tmp/workledger-restore.dump
  docker compose up -d web worker beat | Out-Null
}
Write-Host "Restore complete. Prior files retained at: $rollback"
