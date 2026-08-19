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

mkdir -p workledger-data/attachments workledger-data/previews workledger-data/exports workledger-data/backups
docker compose up --build --detach
docker compose ps
printf '%s\n' "WorkLedger: http://127.0.0.1:${WORKLEDGER_PORT:-8787}"
