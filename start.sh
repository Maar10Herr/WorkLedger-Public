#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  secret="$(openssl rand -hex 32)"
  db_password="$(openssl rand -hex 24)"
  app_db_password="$(openssl rand -hex 24)"
  sed -i.bak "s/CHANGE_ME_generate_a_long_random_value/$secret/; s/CHANGE_ME_database_password/$db_password/g; s/CHANGE_ME_application_database_password/$app_db_password/g" .env
  rm -f .env.bak
  printf '%s\n' "Created .env with generated secrets."
fi

dotenv_value() {
  key="$1"
  value="$(sed -n "s/^${key}=//p" .env | tail -n 1)"
  case "$value" in
    \"*\") value="${value#\"}"; value="${value%\"}" ;;
    \'*\') value="${value#\'}"; value="${value%\'}" ;;
  esac
  printf '%s' "$value"
}

setting_value() {
  name="$1"
  value="$(printenv "$name" 2>/dev/null || true)"
  if [ -z "$value" ] && [ -f .env ]; then
    value="$(dotenv_value "$name")"
  fi
  printf '%s' "$value"
}

data_dir="$(setting_value WORKLEDGER_DATA_DIR)"
backup_dir="$(setting_value WORKLEDGER_BACKUP_DIR)"
data_dir="${data_dir:-./workledger-data}"
backup_dir="${backup_dir:-./workledger-backups}"
case "$data_dir:$backup_dir" in
  /:*|*:/|.:*|*:.)
    printf '%s\n' "WORKLEDGER_DATA_DIR and WORKLEDGER_BACKUP_DIR must not be filesystem roots." >&2
    exit 2
    ;;
esac
port="$(setting_value WORKLEDGER_PORT)"
if [ -z "$port" ]; then
  port=8787
fi
case "$port" in
  *[!0-9]*)
    printf '%s\n' "WORKLEDGER_PORT must be a decimal port number." >&2
    exit 2
    ;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
  printf '%s\n' "WORKLEDGER_PORT must be between 1 and 65535." >&2
  exit 2
fi
mkdir -p "$data_dir/attachments" "$data_dir/previews" "$data_dir/exports" "$data_dir/backups" "$backup_dir"
docker compose up --build --detach
docker compose ps
printf '%s\n' "WorkLedger: http://127.0.0.1:$port"
