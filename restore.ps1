param([Parameter(Mandatory=$true)][string]$BackupDirectory, [switch]$Yes)
$ErrorActionPreference = "Stop"
if (!$Yes) { throw "Restore is destructive. Re-run with -Yes -BackupDirectory PATH." }
Set-Location $PSScriptRoot
if (!(Test-Path .env)) { throw "Missing .env" }
Get-Content .env | Where-Object { $_ -match '^[A-Za-z_][A-Za-z0-9_]*=' } | ForEach-Object {
  $name, $value = $_ -split '=', 2; Set-Item -Path "Env:$name" -Value $value
}

function Get-RegularFile([string]$Path, [string]$Label) {
  try { $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop }
  catch { throw "$Label not found" }
  if ($item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
    throw "$Label must be a regular, non-symbolic-link file"
  }
  return $item
}

function Get-SafeDataDirectory([string]$Value, [string]$ProjectRoot) {
  if ([string]::IsNullOrWhiteSpace($Value)) { throw "Unsafe data directory" }
  if ($Value -match '(^|[\\/])\.\.([\\/]|$)') {
    throw "Unsafe data directory: '..' components are not allowed"
  }
  try { $full = [IO.Path]::GetFullPath($Value) }
  catch { throw "Unsafe data directory: invalid path" }
  $pathRoot = [IO.Path]::GetPathRoot($full)
  if ([string]::IsNullOrEmpty($pathRoot) -or $full.Length -eq $pathRoot.Length) {
    throw "Unsafe data directory: must not be a filesystem root"
  }
  $full = $full.TrimEnd([char[]]"\\/")

  $probe = $full
  $nearestExisting = $false
  while ($true) {
    if (Test-Path -LiteralPath $probe) {
      try { $item = Get-Item -LiteralPath $probe -Force -ErrorAction Stop }
      catch { throw "Unsafe data directory: inaccessible path" }
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Unsafe data directory: symlink or junction components are not allowed"
      }
      if (-not $nearestExisting -and -not $item.PSIsContainer) {
        throw "Unsafe data directory: path is not a directory"
      }
      $nearestExisting = $true
    }
    try { $parent = [IO.DirectoryInfo]::new($probe).Parent }
    catch { throw "Unsafe data directory: invalid parent path" }
    if ($null -eq $parent -or $parent.FullName -eq $probe) { break }
    $probe = $parent.FullName
  }
  if (-not $nearestExisting) { throw "Unsafe data directory: invalid parent path" }

  try { $project = [IO.Path]::GetFullPath($ProjectRoot) }
  catch { throw "Unsafe data directory: invalid project root" }
  $trimmedFull = $full.TrimEnd([char[]]"\\/")
  $trimmedProject = $project.TrimEnd([char[]]"\\/")
  $comparison = [StringComparison]::OrdinalIgnoreCase
  $isProject = $trimmedFull.Equals($trimmedProject, $comparison)
  $isProjectParent = $trimmedProject.StartsWith(
    "$trimmedFull$([IO.Path]::DirectorySeparatorChar)",
    $comparison
  ) -or $trimmedProject.StartsWith(
    "$trimmedFull$([IO.Path]::AltDirectorySeparatorChar)",
    $comparison
  )
  if ($isProject -or $isProjectParent) {
    throw "Unsafe data directory: must not be the project directory or one of its parents"
  }
  return $full
}

function Invoke-ArchiveValidator([string]$Archive, [string]$Destination, [string]$BackupRoot) {
  $validator = Join-Path $PSScriptRoot "scripts\validate_backup_archive.py"
  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($null -ne $python) {
    & $python.Source $validator --project-root $PSScriptRoot $Archive $Destination
    if ($LASTEXITCODE -ne 0) { throw "Could not validate file archive" }
    return
  }

  $docker = Get-Command docker -ErrorAction SilentlyContinue
  if ($null -eq $docker) {
    throw "Archive validation requires Python or Docker"
  }
  $backupPath = (Get-Item -LiteralPath $BackupRoot -Force).FullName
  $stagingPath = (Get-Item -LiteralPath $Destination -Force).FullName
  $validatorPath = (Get-Item -LiteralPath $validator -Force).FullName
  & $docker.Source compose run --rm --no-deps `
    --volume "${backupPath}:/restore-backup:ro" `
    --volume "${stagingPath}:/restore-staging" `
    --volume "${validatorPath}:/tmp/validate_backup_archive.py:ro" `
    web python /tmp/validate_backup_archive.py `
    /restore-backup/data.tar.gz /restore-staging
  if ($LASTEXITCODE -ne 0) { throw "Could not validate file archive in Docker" }
}

function Invoke-DatabaseRollback {
  docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error --username $env:POSTGRES_USER --dbname $env:POSTGRES_DB /tmp/workledger-pre-restore.dump
  if ($LASTEXITCODE -ne 0) { throw "Database rollback failed" }
}

$projectRoot = (Get-Location).Path
$backupItem = Get-Item -LiteralPath $BackupDirectory -Force -ErrorAction Stop
if (-not $backupItem.PSIsContainer -or (($backupItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
  throw "Backup directory must be a regular, non-symbolic-link directory"
}
$backupRoot = $backupItem.FullName
$manifestPath = Join-Path $backupRoot "manifest.txt"
$manifestFile = Get-RegularFile $manifestPath "Backup manifest"
$manifest = @{}
Get-Content -LiteralPath $manifestFile.FullName | ForEach-Object {
  if ($_ -notmatch '^([A-Za-z][A-Za-z0-9_]*)=([^\r\n]*)$') {
    throw "Malformed backup manifest"
  }
  $key = $Matches[1]
  if ($key -notin @("format", "created_at", "database_sha256", "data_sha256", "config_sha256")) {
    throw "Unknown backup manifest key: $key"
  }
  if ($manifest.ContainsKey($key)) { throw "Duplicate backup manifest key: $key" }
  $manifest[$key] = $Matches[2]
}
foreach ($key in @("format", "created_at", "database_sha256", "data_sha256", "config_sha256")) {
  if (-not $manifest.ContainsKey($key) -or [string]::IsNullOrEmpty($manifest[$key])) {
    throw "Malformed backup manifest"
  }
}
if ($manifest.format -ne "workledger-backup-v2") { throw "Unsupported backup format" }
$dbDump = (Get-RegularFile (Join-Path $backupRoot "database.dump") "Database dump").FullName
$dataArchive = (Get-RegularFile (Join-Path $backupRoot "data.tar.gz") "Data archive").FullName
$configArchive = (Get-RegularFile (Join-Path $backupRoot "config.tar.gz") "Configuration archive").FullName
foreach ($key in @("database_sha256", "data_sha256", "config_sha256")) {
  if ($manifest[$key] -notmatch '^[0-9a-fA-F]{64}$') {
    throw "Malformed checksum in backup manifest: $key"
  }
}
if ((Get-FileHash -LiteralPath $dbDump -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.database_sha256.ToLowerInvariant()) { throw "Database checksum mismatch" }
if ((Get-FileHash -LiteralPath $dataArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.data_sha256.ToLowerInvariant()) { throw "Data checksum mismatch" }
if ((Get-FileHash -LiteralPath $configArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.config_sha256.ToLowerInvariant()) { throw "Config checksum mismatch" }
$dataDir = Get-SafeDataDirectory ($env:WORKLEDGER_DATA_DIR ?? ".\workledger-data") $projectRoot
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$staging = "$dataDir.restore-staging-$stamp"
$rollback = "$dataDir.pre-restore-$stamp"
if ((Test-Path -LiteralPath $staging) -or (Test-Path -LiteralPath $rollback)) { throw "Restore staging path already exists" }
New-Item -ItemType Directory -Force -Path $staging | Out-Null
try { Invoke-ArchiveValidator $dataArchive $staging $backupRoot }
catch { Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue; throw }
$allowWriterRestart = $true
Write-Host "Stopping application writers..."
try {
  docker compose stop web worker beat
  if ($LASTEXITCODE -ne 0) { throw "Could not stop application writers" }
  docker compose up -d postgres
  if ($LASTEXITCODE -ne 0) { throw "Could not start PostgreSQL" }
  docker compose exec -T postgres rm -f /tmp/workledger-pre-restore.dump /tmp/workledger-restore.dump
  docker compose exec -T postgres pg_dump --username $env:POSTGRES_USER --dbname $env:POSTGRES_DB --format=custom --file=/tmp/workledger-pre-restore.dump
  if ($LASTEXITCODE -ne 0) { throw "Could not create restore rollback dump" }
  docker compose cp $dbDump postgres:/tmp/workledger-restore.dump
  if ($LASTEXITCODE -ne 0) { throw "Could not copy restore dump" }
  docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error --username $env:POSTGRES_USER --dbname $env:POSTGRES_DB /tmp/workledger-restore.dump
  if ($LASTEXITCODE -ne 0) {
    try { Invoke-DatabaseRollback }
    catch { $allowWriterRestart = $false; throw "CRITICAL: database rollback failed; writers remain stopped for manual recovery" }
    throw "Database restore failed; the pre-restore database was restored"
  }
  try {
    if (Test-Path $dataDir) { Move-Item $dataDir $rollback }
    Move-Item $staging $dataDir
  } catch {
    try {
      Invoke-DatabaseRollback
      if (Test-Path $rollback) { Move-Item $rollback $dataDir }
    } catch {
      $allowWriterRestart = $false
      throw "CRITICAL: restore rollback failed; writers remain stopped for manual recovery"
    }
    throw
  }
  docker compose run --rm web python manage.py verify_integrity
  if ($LASTEXITCODE -ne 0) {
    try {
      Invoke-DatabaseRollback
      Remove-Item -Recurse -Force $dataDir
      if (Test-Path $rollback) { Move-Item $rollback $dataDir }
    } catch {
      $allowWriterRestart = $false
      throw "CRITICAL: restore rollback failed; writers remain stopped for manual recovery"
    }
    throw "Post-restore integrity verification failed; restore was rolled back"
  }
} finally {
  $cleanupFailed = $false
  try {
    docker compose exec -T postgres rm -f /tmp/workledger-pre-restore.dump /tmp/workledger-restore.dump
    if ($LASTEXITCODE -ne 0) { $cleanupFailed = $true }
  } catch { $cleanupFailed = $true }
  if ($allowWriterRestart) {
    try {
      docker compose up -d web worker beat | Out-Null
      if ($LASTEXITCODE -ne 0) { $cleanupFailed = $true }
    } catch { $cleanupFailed = $true }
  }
  if ($cleanupFailed) { throw "Could not clean up restore containers or restart application writers" }
}
Write-Host "Restore complete. Prior files retained at: $rollback"
