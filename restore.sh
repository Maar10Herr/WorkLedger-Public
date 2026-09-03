#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
if [[ "${1:-}" != "--yes" || -z "${2:-}" ]]; then
  echo "Usage: ./restore.sh --yes BACKUP_DIRECTORY" >&2
  echo "This replaces the live database and data directory." >&2
  exit 2
fi
backup="$2"
[[ -d "$backup" && ! -L "$backup" ]] || { echo "Backup directory must be a regular directory: $backup" >&2; exit 1; }
[[ -f .env ]] || { echo "Missing .env" >&2; exit 1; }
set -a
# shellcheck disable=SC1091
source .env
set +a
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1
  fi
}
regular_file() {
  local path="$1"
  local label="$2"
  [[ -f "$path" && ! -L "$path" ]] || {
    echo "$label must be a regular, non-symbolic-link file" >&2
    exit 1
  }
}
regular_file "$backup/manifest.txt" "Backup manifest"
regular_file "$backup/database.dump" "Database dump"
regular_file "$backup/data.tar.gz" "Data archive"
regular_file "$backup/config.tar.gz" "Configuration archive"
manifest_format=""
manifest_created_at=""
expected_db=""
expected_data=""
expected_config=""
seen_manifest_keys=""
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    format=*)
      [[ "$seen_manifest_keys" != *"|format|"* ]] || { echo "Duplicate backup manifest key: format" >&2; exit 1; }
      seen_manifest_keys+="|format|"
      manifest_format="${line#format=}"
      ;;
    created_at=*)
      [[ "$seen_manifest_keys" != *"|created_at|"* ]] || { echo "Duplicate backup manifest key: created_at" >&2; exit 1; }
      seen_manifest_keys+="|created_at|"
      manifest_created_at="${line#created_at=}"
      ;;
    database_sha256=*)
      [[ "$seen_manifest_keys" != *"|database_sha256|"* ]] || { echo "Duplicate backup manifest key: database_sha256" >&2; exit 1; }
      seen_manifest_keys+="|database_sha256|"
      expected_db="${line#database_sha256=}"
      ;;
    data_sha256=*)
      [[ "$seen_manifest_keys" != *"|data_sha256|"* ]] || { echo "Duplicate backup manifest key: data_sha256" >&2; exit 1; }
      seen_manifest_keys+="|data_sha256|"
      expected_data="${line#data_sha256=}"
      ;;
    config_sha256=*)
      [[ "$seen_manifest_keys" != *"|config_sha256|"* ]] || { echo "Duplicate backup manifest key: config_sha256" >&2; exit 1; }
      seen_manifest_keys+="|config_sha256|"
      expected_config="${line#config_sha256=}"
      ;;
    *) echo "Malformed backup manifest" >&2; exit 1 ;;
  esac
done < "$backup/manifest.txt"
[[ "$manifest_format" == workledger-backup-v2 && -n "$manifest_created_at" ]] || {
  echo "Malformed backup manifest" >&2
  exit 1
}
for expected in "$expected_db" "$expected_data" "$expected_config"; do
  [[ "$expected" =~ ^[0-9A-Fa-f]{64}$ ]] || {
    echo "Malformed backup checksum" >&2
    exit 1
  }
done
[[ "$(sha256 "$backup/database.dump")" == "$expected_db" ]] || { echo "Database checksum mismatch" >&2; exit 1; }
[[ "$(sha256 "$backup/data.tar.gz")" == "$expected_data" ]] || { echo "Data checksum mismatch" >&2; exit 1; }
[[ "$(sha256 "$backup/config.tar.gz")" == "$expected_config" ]] || { echo "Config checksum mismatch" >&2; exit 1; }
data_dir="${WORKLEDGER_DATA_DIR:-./workledger-data}"
[[ -n "$data_dir" && "$data_dir" != "/" && "$data_dir" != "." ]] || { echo "Unsafe data directory" >&2; exit 1; }
repo_root="$(pwd -P)"
sh scripts/validate_data_directory.sh "$backup" "$repo_root" backup >/dev/null
data_dir="$(sh scripts/validate_data_directory.sh "$data_dir" "$repo_root" data --print)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
staging_dir="${data_dir}.restore-staging-$stamp"
rollback_dir="${data_dir}.pre-restore-$stamp"
[[ ! -e "$staging_dir" && ! -L "$staging_dir" && ! -e "$rollback_dir" && ! -L "$rollback_dir" ]] || {
  echo "Restore staging path already exists" >&2
  exit 1
}
mkdir -p "$staging_dir"
cleanup_staging() {
  if [[ -e "$staging_dir" || -L "$staging_dir" ]]; then rm -rf -- "$staging_dir"; fi
}
rollback_dump=""
writers_need_restart=0
restart_writers() {
  if [[ "$writers_need_restart" -eq 1 ]]; then
    docker compose up -d web worker beat >/dev/null
  fi
}
cleanup_restore() {
  local status=$?
  trap - EXIT
  set +e
  restart_writers
  [[ "$?" -eq 0 ]] || { echo "Could not restart application writers" >&2; status=1; }
  cleanup_staging
  [[ "$?" -eq 0 ]] || status=1
  if [[ -n "$rollback_dump" ]]; then rm -f -- "$rollback_dump" || status=1; fi
  exit "$status"
}
validate_archive() {
  if command -v python3 >/dev/null 2>&1; then
    python3 scripts/validate_backup_archive.py --project-root "$repo_root" \
      "$backup/data.tar.gz" "$staging_dir"
    return
  fi
  if ! command -v docker >/dev/null 2>&1; then
    echo "Archive validation requires python3 or Docker" >&2
    return 1
  fi
  backup_path="$(cd -- "$backup" && pwd -P)"
  staging_path="$(cd -- "$staging_dir" && pwd -P)"
  docker compose run --rm --no-deps \
    --volume "$backup_path:/restore-backup:ro" \
    --volume "$staging_path:/restore-staging" \
    --volume "$repo_root/scripts/validate_backup_archive.py:/tmp/validate_backup_archive.py:ro" \
    web python /tmp/validate_backup_archive.py \
    /restore-backup/data.tar.gz /restore-staging
}
trap cleanup_staging EXIT
validate_archive
writers_need_restart=1
trap cleanup_restore EXIT
echo "Stopping application writers..."
docker compose stop web worker beat
docker compose up -d postgres
rollback_dump="$(mktemp "${TMPDIR:-/tmp}/workledger-pre-restore.XXXXXX.dump")"
chmod 600 "$rollback_dump"
docker compose exec -T postgres pg_dump --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" --format=custom > "$rollback_dump"
echo "Restoring PostgreSQL database..."
if ! docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" < "$backup/database.dump"; then
  echo "Restore failed; rolling database back to its pre-restore state..." >&2
  if ! docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" < "$rollback_dump"; then
    echo "CRITICAL: database rollback failed; writers remain stopped for manual recovery" >&2
    writers_need_restart=0
  fi
  exit 1
fi
file_switch_ok=1
prior_data_moved=0
if [[ -d "$data_dir" ]]; then
  if mv "$data_dir" "$rollback_dir"; then prior_data_moved=1; else file_switch_ok=0; fi
fi
if [[ "$file_switch_ok" -eq 1 ]] && ! mv "$staging_dir" "$data_dir"; then file_switch_ok=0; fi
if [[ "$file_switch_ok" -ne 1 ]]; then
  echo "File switch failed; rolling database and files back..." >&2
  rollback_ok=1
  docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" < "$rollback_dump" || rollback_ok=0
  if [[ "$prior_data_moved" -eq 1 ]]; then
    [[ -d "$rollback_dir" && ! -L "$rollback_dir" ]] && mv "$rollback_dir" "$data_dir" || rollback_ok=0
  fi
  if [[ "$rollback_ok" -ne 1 ]]; then
    writers_need_restart=0
    echo "CRITICAL: rollback incomplete; manual recovery required" >&2
  fi
  exit 1
fi
if ! docker compose run --rm web python manage.py verify_integrity; then
  echo "Post-restore integrity verification failed; rolling back..." >&2
  rollback_ok=1
  docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" < "$rollback_dump" || rollback_ok=0
  rm -rf -- "$data_dir" || rollback_ok=0
  if [[ "$prior_data_moved" -eq 1 ]]; then
    [[ -d "$rollback_dir" && ! -L "$rollback_dir" ]] && mv "$rollback_dir" "$data_dir" || rollback_ok=0
  fi
  if [[ "$rollback_ok" -ne 1 ]]; then
    writers_need_restart=0
    echo "CRITICAL: rollback incomplete; manual recovery required" >&2
  fi
  exit 1
fi
rm -f -- "$rollback_dump"
rollback_dump=""
echo "Restore complete. Previous files retained at: $rollback_dir"
