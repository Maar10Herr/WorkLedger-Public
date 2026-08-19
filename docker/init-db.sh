#!/usr/bin/env bash
set -Eeuo pipefail
: "${APP_DATABASE_PASSWORD:?APP_DATABASE_PASSWORD is required}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 <<'SQL'
\getenv app_password APP_DATABASE_PASSWORD
\getenv database_name POSTGRES_DB
SELECT format('CREATE ROLE workledger_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'workledger_app')
\gexec
ALTER ROLE workledger_app PASSWORD :'app_password';
GRANT CONNECT ON DATABASE :"database_name" TO workledger_app;
GRANT USAGE ON SCHEMA public TO workledger_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO workledger_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO workledger_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO workledger_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO workledger_app;
SQL
