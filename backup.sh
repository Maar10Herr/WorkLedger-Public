#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo "Missing .env; run ./start.sh first." >&2; exit 1; }
set -a
# shellcheck disable=SC1091
source .env
set +a
backup_root="${WORKLEDGER_BACKUP_DIR:-./workledger-backups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="${1:-$backup_root/workledger-$timestamp}"
data_dir="${WORKLEDGER_DATA_DIR:-./workledger-data}"
mkdir -p "$destination"
[[ -d "$data_dir" ]] || { echo "Data directory not found: $data_dir" >&2; exit 1; }
echo "Pausing application writers for a consistent database/file snapshot..."
docker compose stop web worker beat
restart_writers() { docker compose up -d web worker beat >/dev/null; }
trap restart_writers EXIT
echo "Creating PostgreSQL dump..."
docker compose exec -T postgres pg_dump \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom \
  > "$destination/database.dump"
echo "Archiving originals, previews, exports, and package files..."
tar -C "$data_dir" -czf "$destination/data.tar.gz" .
tar -czf "$destination/config.tar.gz" compose.yaml compose.review.yaml .env.example README.md
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1
  fi
}
cat > "$destination/manifest.txt" <<EOF
format=workledger-backup-v2
created_at=$timestamp
database_sha256=$(sha256 "$destination/database.dump")
data_sha256=$(sha256 "$destination/data.tar.gz")
config_sha256=$(sha256 "$destination/config.tar.gz")
EOF
chmod 600 "$destination/database.dump" "$destination/data.tar.gz" \
  "$destination/config.tar.gz" "$destination/manifest.txt"
restart_writers
trap - EXIT
echo "Backup complete: $destination"
