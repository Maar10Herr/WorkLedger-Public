#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
if [[ "${1:-}" != "--yes" || -z "${2:-}" ]]; then
  echo "Usage: ./restore.sh --yes BACKUP_DIRECTORY" >&2
  echo "This replaces the live database and data directory." >&2
  exit 2
fi
backup="$2"
[[ -f .env ]] || { echo "Missing .env" >&2; exit 1; }
[[ -f "$backup/manifest.txt" && -f "$backup/database.dump" \
   && -f "$backup/data.tar.gz" && -f "$backup/config.tar.gz" ]] || {
  echo "Incomplete backup directory: $backup" >&2; exit 1;
}
set -a
# shellcheck disable=SC1091
source .env
set +a
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1
  fi
}
expected_db="$(grep '^database_sha256=' "$backup/manifest.txt" | cut -d= -f2)"
expected_data="$(grep '^data_sha256=' "$backup/manifest.txt" | cut -d= -f2)"
expected_config="$(grep '^config_sha256=' "$backup/manifest.txt" | cut -d= -f2)"
grep -qx 'format=workledger-backup-v2' "$backup/manifest.txt" || { echo "Unsupported backup format" >&2; exit 1; }
[[ "$(sha256 "$backup/database.dump")" == "$expected_db" ]] || { echo "Database checksum mismatch" >&2; exit 1; }
[[ "$(sha256 "$backup/data.tar.gz")" == "$expected_data" ]] || { echo "Data checksum mismatch" >&2; exit 1; }
[[ "$(sha256 "$backup/config.tar.gz")" == "$expected_config" ]] || { echo "Config checksum mismatch" >&2; exit 1; }
data_dir="${WORKLEDGER_DATA_DIR:-./workledger-data}"
[[ -n "$data_dir" && "$data_dir" != "/" && "$data_dir" != "." ]] || { echo "Unsafe data directory" >&2; exit 1; }
while IFS= read -r member; do
  case "$member" in /*|../*|*/../*|*/..) echo "Unsafe archive member: $member" >&2; exit 1;; esac
done < <(tar -tzf "$backup/data.tar.gz")
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
staging_dir="${data_dir}.restore-staging-$stamp"
rollback_dir="${data_dir}.pre-restore-$stamp"
[[ ! -e "$staging_dir" && ! -e "$rollback_dir" ]] || { echo "Restore staging path already exists" >&2; exit 1; }
mkdir -p "$staging_dir"
tar -C "$staging_dir" -xzf "$backup/data.tar.gz" --no-same-owner
echo "Stopping application writers..."
docker compose stop web worker beat
restart_writers() { docker compose up -d web worker beat >/dev/null; }
trap restart_writers EXIT
docker compose up -d postgres
rollback_dump="$(mktemp "${TMPDIR:-/tmp}/workledger-pre-restore.XXXXXX.dump")"
chmod 600 "$rollback_dump"
docker compose exec -T postgres pg_dump --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" --format=custom > "$rollback_dump"
echo "Restoring PostgreSQL database..."
if ! docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" < "$backup/database.dump"; then
  echo "Restore failed; rolling database back to its pre-restore state..." >&2
  docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" < "$rollback_dump"
  rm -rf "$staging_dir"; rm -f "$rollback_dump"
  docker compose up -d web worker beat
  exit 1
fi
if [[ -d "$data_dir" ]]; then mv "$data_dir" "$rollback_dir"; fi
if ! mv "$staging_dir" "$data_dir"; then
  echo "File switch failed; rolling database and files back..." >&2
  docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" < "$rollback_dump"
  [[ -d "$rollback_dir" ]] && mv "$rollback_dir" "$data_dir"
  docker compose up -d web worker beat
  exit 1
fi
if ! docker compose run --rm web python manage.py verify_integrity; then
  echo "Post-restore integrity verification failed; rolling back..." >&2
  docker compose exec -T postgres pg_restore --clean --if-exists --exit-on-error \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" < "$rollback_dump"
  rm -rf "$data_dir"
  [[ -d "$rollback_dir" ]] && mv "$rollback_dir" "$data_dir"
  docker compose up -d web worker beat
  exit 1
fi
rm -f "$rollback_dump"
docker compose up -d web worker beat
trap - EXIT
echo "Restore complete. Previous files retained at: $rollback_dir"
