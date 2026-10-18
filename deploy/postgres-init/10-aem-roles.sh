#!/usr/bin/env bash
# Runs once, when the PostgreSQL container starts on an empty data volume.
#
# Sets the database up the way scripts/local_postgres.sh does on a laptop:
#   * POSTGRES_USER is the bootstrap administrator, a superuser used only here;
#   * the migration role owns the schema and runs 'manage.py init', but is an
#     ordinary role that cannot switch off the integrity triggers;
#   * the runtime role is what the app serves requests as: it owns nothing and
#     cannot change the schema;
#   * the trigger guard is installed, so not even the owner can leave a trigger
#     disabled.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  --set=owner="$AEM_DB_MIGRATION_USER" --set=owner_password="$AEM_DB_MIGRATION_PASSWORD" \
  --set=runtime="$AEM_DB_USER" --set=runtime_password="$AEM_DB_PASSWORD" \
  --set=name="$AEM_DB_NAME" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
              :'owner', :'owner_password') \gexec
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
              :'runtime', :'runtime_password') \gexec
SELECT format('CREATE DATABASE %I OWNER %I TEMPLATE template0 ENCODING %L LC_COLLATE %L LC_CTYPE %L',
              :'name', :'owner', 'UTF8', 'C', 'C') \gexec
-- The runtime role connects through PUBLIC's CONNECT; temporary tables are DDL too.
SELECT format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', :'name') \gexec
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$AEM_DB_NAME" \
  --file /aem/trigger_guard.sql

echo "AEM: created $AEM_DB_NAME owned by $AEM_DB_MIGRATION_USER, runtime role $AEM_DB_USER, trigger guard installed."
