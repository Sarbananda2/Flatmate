#!/usr/bin/env bash
# The project's own PostgreSQL 18 cluster, for local development.
#
#   ./scripts/local_postgres.sh start     initialise on first use, then start
#   ./scripts/local_postgres.sh stop      stop this project's cluster
#   ./scripts/local_postgres.sh status    is it running, and where
#
# The cluster lives in ./data/postgres (ignored by Git) and keeps its data
# between runs. It listens on 127.0.0.1 only, on a port of its own (55432 by
# default) so it never collides with another PostgreSQL on this machine, and
# every connection needs a password from .env.local.
#
# Three roles, on purpose:
#   * a bootstrap administrator (aem_admin), the cluster's superuser. Only this
#     script uses it, to create the other two roles and the databases and to
#     install the trigger guard. The application is never given its password.
#   * the migration role (aem_owner): no superuser, no CREATEDB, no CREATEROLE,
#     no REPLICATION, no BYPASSRLS. It owns the application database and the
#     test database, and so the tables created in them. Only "manage.py init",
#     "import-sqlite", "restore" and the test setup connect as it.
#   * the runtime role (aem): the same absence of attributes, and it owns
#     nothing. The migration role grants it row-level access to the tables
#     each time it migrates; it cannot create, alter, drop, or truncate.
#
# This script only ever touches the cluster in its own data directory. It will
# not initialise over an existing directory, will not start a cluster created
# by a different PostgreSQL major version, and cannot stop any other server.
#
# ./scripts/dev.sh sources this file to start the cluster and stop it again on
# exit; run it directly when you want the database up without the app.
#
# Settings (environment, or .env.local at the project root):
#   AEM_PG_BIN_DIR    directory holding PostgreSQL 18's initdb, pg_ctl, psql
#   AEM_PG_DATA_DIR   cluster data directory        (default ./data/postgres)
#   AEM_PG_PORT       local cluster port            (default 55432)
#   AEM_DB_NAME       database name                 (default aem)
#   AEM_DB_USER       the runtime role              (default aem)
#   AEM_DB_PASSWORD   its password; generated into .env.local on first start
#   AEM_DB_MIGRATION_USER      the migration role   (default aem_owner)
#   AEM_DB_MIGRATION_PASSWORD  its password; generated into .env.local on first start
#   AEM_TEST_DB_NAME  database for the test suite   (default <AEM_DB_NAME>_test)
#   AEM_PG_ADMIN_USER      bootstrap administrator  (default aem_admin)
#   AEM_PG_ADMIN_PASSWORD  its password; generated into .env.local on first start

AEM_PG_REQUIRED_MAJOR=18
AEM_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEM_ENV_FILE_PATH="${AEM_ENV_FILE:-$AEM_PROJECT_ROOT/.env.local}"

aem_pg_say()  { echo "[postgres] $*"; }
aem_pg_fail() { echo "[postgres] $*" >&2; return 1; }

# Read KEY=value lines from .env.local without overriding the environment and
# without executing the file.
aem_pg_load_env() {
  [[ -f "$AEM_ENV_FILE_PATH" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    line="${line#export }"
    key="${line%%=*}"; value="${line#*=}"
    key="${key//[[:space:]]/}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ ${#value} -ge 2 && ( "$value" == \"*\" || "$value" == \'*\' ) ]]; then
      value="${value:1:${#value}-2}"
    fi
    if [[ -z "${!key+x}" ]]; then export "$key=$value"; fi
  done < "$AEM_ENV_FILE_PATH"
}

aem_pg_settings() {
  aem_pg_load_env
  # Keep the local cluster port independent from AEM_DB_PORT, which may be
  # configured for a remote AWS database in the same environment file.
  AEM_PG_PORT="${AEM_PG_PORT:-55432}"
  AEM_PG_NAME="${AEM_DB_NAME:-aem}"
  AEM_PG_USER="${AEM_DB_USER:-aem}"
  AEM_PG_OWNER="${AEM_DB_MIGRATION_USER:-aem_owner}"
  AEM_PG_TEST_NAME="${AEM_TEST_DB_NAME:-${AEM_PG_NAME}_test}"
  AEM_PG_ADMIN="${AEM_PG_ADMIN_USER:-aem_admin}"
  AEM_PG_GUARD_SQL="$AEM_PROJECT_ROOT/scripts/sql/trigger_guard.sql"
  AEM_PG_DATA="${AEM_PG_DATA_DIR:-$AEM_PROJECT_ROOT/data/postgres}"
  [[ "$AEM_PG_DATA" == /* ]] || AEM_PG_DATA="$AEM_PROJECT_ROOT/$AEM_PG_DATA"
  AEM_PG_LOG="$AEM_PG_DATA/server.log"
}

aem_pg_major_of() {  # prints the major version of the postgres binary in $1
  local version
  version="$("$1/postgres" --version 2>/dev/null)" || return 1
  [[ "$version" =~ ([0-9]+)(\.|beta|rc|devel) ]] || return 1
  echo "${BASH_REMATCH[1]}"
}

# Find PostgreSQL 18's bin directory. A wrong major version is never used.
aem_pg_find_bin() {
  local candidate major found_other=""
  local -a candidates=()
  if [[ -n "${AEM_PG_BIN_DIR:-}" ]]; then
    candidates=("$AEM_PG_BIN_DIR")
  else
    command -v pg_config >/dev/null 2>&1 && candidates+=("$(pg_config --bindir 2>/dev/null)")
    command -v postgres >/dev/null 2>&1 && candidates+=("$(dirname "$(command -v postgres)")")
    command -v pg_ctl >/dev/null 2>&1 && candidates+=("$(dirname "$(command -v pg_ctl)")")
    candidates+=(
      "/usr/lib/postgresql/$AEM_PG_REQUIRED_MAJOR/bin"
      "/usr/pgsql-$AEM_PG_REQUIRED_MAJOR/bin"
      "/opt/homebrew/opt/postgresql@$AEM_PG_REQUIRED_MAJOR/bin"
      "/usr/local/opt/postgresql@$AEM_PG_REQUIRED_MAJOR/bin"
      "/usr/local/pgsql/bin"
    )
  fi
  for candidate in "${candidates[@]}"; do
    [[ -n "$candidate" && -x "$candidate/postgres" && -x "$candidate/pg_ctl" \
       && -x "$candidate/initdb" && -x "$candidate/psql" ]] || continue
    major="$(aem_pg_major_of "$candidate")" || continue
    if [[ "$major" == "$AEM_PG_REQUIRED_MAJOR" ]]; then
      AEM_PG_BIN="$candidate"
      return 0
    fi
    [[ "$found_other" == *" in $candidate"* ]] \
      || found_other="${found_other:+$found_other, }PostgreSQL $major in $candidate"
  done
  {
    echo "[postgres] PostgreSQL $AEM_PG_REQUIRED_MAJOR is required, and was not found."
    if [[ -n "${AEM_PG_BIN_DIR:-}" ]]; then
      echo "[postgres] AEM_PG_BIN_DIR=$AEM_PG_BIN_DIR does not hold PostgreSQL" \
           "$AEM_PG_REQUIRED_MAJOR's initdb, pg_ctl, postgres and psql."
    fi
    [[ -n "$found_other" ]] && echo "[postgres] Found, and not used: $found_other."
    cat <<HELP
[postgres]
[postgres] Install PostgreSQL $AEM_PG_REQUIRED_MAJOR (nothing is installed for you), for example:
[postgres]   Debian/Ubuntu  add the PGDG apt repository (https://www.postgresql.org/download/linux/),
[postgres]                  then: sudo apt install postgresql-$AEM_PG_REQUIRED_MAJOR
[postgres]   macOS          brew install postgresql@$AEM_PG_REQUIRED_MAJOR
[postgres]   from source    https://www.postgresql.org/ftp/source/  (./configure --prefix=\$HOME/.local/pgsql$AEM_PG_REQUIRED_MAJOR)
[postgres]
[postgres] Then tell this project where its bin directory is, in .env.local:
[postgres]   AEM_PG_BIN_DIR=/usr/lib/postgresql/$AEM_PG_REQUIRED_MAJOR/bin
[postgres]
[postgres] A system package may start its own server on port 5432. This project does
[postgres] not use that server; it runs its own cluster from ./data/postgres.
HELP
  } >&2
  return 1
}

# Generate a password for the variable named $1 and append it to .env.local.
# The password is never printed.
aem_pg_generate_password() {
  local variable="$1" generated
  generated="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32)" || true
  [[ ${#generated} -eq 32 ]] || { aem_pg_fail "Could not generate a password."; return 1; }
  ( umask 077
    [[ -f "$AEM_ENV_FILE_PATH" ]] || : > "$AEM_ENV_FILE_PATH"
    printf '\n# Generated by scripts/local_postgres.sh for the local cluster. Do not commit.\n%s=%s\n' \
      "$variable" "$generated" >> "$AEM_ENV_FILE_PATH" )
  chmod 600 "$AEM_ENV_FILE_PATH"
  export "$variable=$generated"
  aem_pg_say "Generated $variable and saved it in ${AEM_ENV_FILE_PATH#"$AEM_PROJECT_ROOT/"}."
}

# Make sure both roles have a password, generating them on first use. Once the
# cluster exists its administrator's password cannot be regenerated.
aem_pg_ensure_passwords() {
  if [[ "$AEM_PG_ADMIN" == "$AEM_PG_USER" || "$AEM_PG_ADMIN" == "$AEM_PG_OWNER" \
        || "$AEM_PG_OWNER" == "$AEM_PG_USER" ]]; then
    aem_pg_fail "AEM_DB_USER ($AEM_PG_USER), AEM_DB_MIGRATION_USER ($AEM_PG_OWNER) and" \
                "AEM_PG_ADMIN_USER ($AEM_PG_ADMIN) must be three different roles."
    return 1
  fi
  local variable
  for variable in AEM_PG_ADMIN_PASSWORD AEM_DB_MIGRATION_PASSWORD AEM_DB_PASSWORD; do
    [[ -n "${!variable:-}" ]] && continue
    if [[ -f "$AEM_PG_DATA/PG_VERSION" ]]; then
      aem_pg_fail "The cluster in $AEM_PG_DATA already exists, but $variable is not set." \
                  "Put it back in $AEM_ENV_FILE_PATH (see .env.example)."
      return 1
    fi
    aem_pg_generate_password "$variable" || return 1
  done
  # Later commands in this shell (manage.py init, the tests) need them too.
  export AEM_DB_PASSWORD AEM_DB_MIGRATION_PASSWORD
}

aem_pg_is_running() {
  [[ -f "$AEM_PG_DATA/PG_VERSION" ]] && "$AEM_PG_BIN/pg_ctl" -D "$AEM_PG_DATA" status >/dev/null 2>&1
}

aem_pg_init_cluster() {
  if [[ -f "$AEM_PG_DATA/PG_VERSION" ]]; then
    local existing
    existing="$(tr -d '[:space:]' < "$AEM_PG_DATA/PG_VERSION")"
    if [[ "$existing" != "$AEM_PG_REQUIRED_MAJOR" ]]; then
      aem_pg_fail "The cluster in $AEM_PG_DATA was created by PostgreSQL $existing, not" \
                  "$AEM_PG_REQUIRED_MAJOR. It has been left untouched. Upgrade it with pg_upgrade," \
                  "or point AEM_PG_DATA_DIR at a different directory."
      return 1
    fi
    return 0   # already initialised: never reinitialise
  fi
  if [[ -e "$AEM_PG_DATA" && -n "$(ls -A "$AEM_PG_DATA" 2>/dev/null)" ]]; then
    aem_pg_fail "$AEM_PG_DATA exists and is not empty, but is not a PostgreSQL cluster." \
                "It has been left untouched; move it away or set AEM_PG_DATA_DIR."
    return 1
  fi

  aem_pg_say "Creating the project's cluster in ${AEM_PG_DATA#"$AEM_PROJECT_ROOT/"} (first run only)..."
  mkdir -p "$AEM_PG_DATA" && chmod 700 "$AEM_PG_DATA" || return 1
  local pwfile
  pwfile="$(mktemp)" || return 1
  chmod 600 "$pwfile"
  printf '%s\n' "$AEM_PG_ADMIN_PASSWORD" > "$pwfile"
  # The cluster's superuser is the bootstrap administrator, not the
  # application's role. C locale: text sorts by byte value, as the app expects.
  if ! "$AEM_PG_BIN/initdb" -D "$AEM_PG_DATA" --username="$AEM_PG_ADMIN" --pwfile="$pwfile" \
        --auth-local=scram-sha-256 --auth-host=scram-sha-256 \
        --encoding=UTF8 --locale=C >/dev/null; then
    rm -f "$pwfile"
    aem_pg_fail "initdb failed. $AEM_PG_DATA may be partly written; inspect it before retrying."
    return 1
  fi
  rm -f "$pwfile"
  cat >> "$AEM_PG_DATA/postgresql.conf" <<CONF

# --- Apartment Expense Manager local development cluster ---
# Loopback TCP only, and no Unix socket, so nothing else on this machine
# reaches it by accident. The port is passed on the command line by
# scripts/local_postgres.sh so that it always matches AEM_PG_PORT.
listen_addresses = '127.0.0.1'
unix_socket_directories = ''
CONF
}

# psql as the bootstrap administrator against database $1. SQL is fed on
# standard input: psql only substitutes :'name' and runs \gexec there.
aem_pg_admin_psql() {
  local database="$1"; shift
  PGPASSWORD="$AEM_PG_ADMIN_PASSWORD" PGCONNECT_TIMEOUT=10 "$AEM_PG_BIN/psql" \
    --host=127.0.0.1 --port="$AEM_PG_PORT" --username="$AEM_PG_ADMIN" \
    --dbname="$database" --no-psqlrc --quiet --tuples-only --no-align \
    --set=ON_ERROR_STOP=1 "$@"
}

# Create role $1 if it is missing, with the password in the environment
# variable named $2, and insist that it has no cluster-wide privilege.
aem_pg_ensure_role() {
  local role="$1" password_variable="$2" present privileged
  present="$(aem_pg_admin_psql postgres --set=role="$role" \
              <<< "SELECT 1 FROM pg_roles WHERE rolname = :'role';")" || {
    aem_pg_fail "Could not sign in to the cluster on port $AEM_PG_PORT as its administrator" \
                "($AEM_PG_ADMIN). Does AEM_PG_ADMIN_PASSWORD in .env.local match this cluster?"
    return 1
  }
  if [[ "$present" != "1" ]]; then
    # The password reaches psql through the environment (\getenv), never the
    # command line, and psql's own messages are discarded so that no error can
    # echo the statement that contains it.
    if ! AEM_PG_NEW_ROLE_PASSWORD="${!password_variable}" \
         aem_pg_admin_psql postgres --set=role="$role" >/dev/null 2>&1 <<'SQL'
\getenv role_password AEM_PG_NEW_ROLE_PASSWORD
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
              :'role', :'role_password') \gexec
SQL
    then
      aem_pg_fail "Could not create the role $role."
      return 1
    fi
    aem_pg_say "Created the role $role (no superuser, no CREATEDB, no CREATEROLE)."
  fi
  privileged="$(aem_pg_admin_psql postgres --set=role="$role" <<< \
    "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
       FROM pg_roles WHERE rolname = :'role';")" || return 1
  if [[ "$privileged" != "f" ]]; then
    aem_pg_fail "The role $role has superuser, CREATEDB, CREATEROLE, REPLICATION or" \
                "BYPASSRLS in this cluster. The application must not use such a role;" \
                "nothing was changed. Remove those attributes (ALTER ROLE) or use another role."
    return 1
  fi
}

# Create database $1, owned by the migration role, if it is missing; then make
# sure nobody but its owner can create anything in it, and install the trigger
# guard. An existing database's ownership is never changed.
aem_pg_ensure_database() {
  local name="$1" owner
  owner="$(aem_pg_admin_psql postgres --set=name="$name" <<< \
    "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :'name';")" || return 1
  if [[ -z "$owner" ]]; then
    if ! aem_pg_admin_psql postgres --set=name="$name" --set=owner="$AEM_PG_OWNER" >/dev/null <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I TEMPLATE template0 ENCODING %L LC_COLLATE %L LC_CTYPE %L',
              :'name', :'owner', 'UTF8', 'C', 'C') \gexec
SQL
    then
      aem_pg_fail "Could not create database $name."
      return 1
    fi
    aem_pg_say "Created database $name, owned by $AEM_PG_OWNER."
  elif [[ "$owner" != "$AEM_PG_OWNER" ]]; then
    aem_pg_fail "Database $name exists but is owned by $owner, not the migration role" \
                "$AEM_PG_OWNER. It has been left untouched. To hand it over, as the" \
                "administrator: ALTER DATABASE $name OWNER TO $AEM_PG_OWNER; and" \
                "REASSIGN OWNED BY $owner TO $AEM_PG_OWNER; inside it."
    return 1
  fi
  # The runtime role reaches the database through PUBLIC's CONNECT. Take away
  # what else PUBLIC is given by default: temporary tables here are DDL too.
  if ! aem_pg_admin_psql postgres --set=name="$name" >/dev/null <<'SQL'
SELECT format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', :'name') \gexec
SQL
  then
    aem_pg_fail "Could not restrict database $name."
    return 1
  fi
  if ! aem_pg_admin_psql "$name" --file="$AEM_PG_GUARD_SQL" >/dev/null; then
    aem_pg_fail "Could not install the trigger guard in database $name."
    return 1
  fi
}

aem_pg_bootstrap() {
  aem_pg_ensure_role "$AEM_PG_OWNER" AEM_DB_MIGRATION_PASSWORD || return 1
  aem_pg_ensure_role "$AEM_PG_USER" AEM_DB_PASSWORD || return 1
  aem_pg_ensure_database "$AEM_PG_NAME" || return 1
  # A separate database for the test suite, so tests can never touch the
  # household's records.
  aem_pg_ensure_database "$AEM_PG_TEST_NAME" || return 1
}

# Start the cluster if it is not running. Sets AEM_PG_STARTED_HERE=1 only when
# this call started it, which is what entitles the caller to stop it later.
aem_pg_start() {
  AEM_PG_STARTED_HERE=0
  aem_pg_settings
  aem_pg_find_bin || return 1
  aem_pg_ensure_passwords || return 1
  aem_pg_init_cluster || return 1

  if aem_pg_is_running; then
    aem_pg_say "Already running on 127.0.0.1:$AEM_PG_PORT; it will be left running."
  else
    if ! "$AEM_PG_BIN/pg_ctl" -D "$AEM_PG_DATA" -l "$AEM_PG_LOG" -w -t 60 \
          -o "-p $AEM_PG_PORT" start >/dev/null; then
      aem_pg_fail "The cluster did not start. Is port $AEM_PG_PORT already in use?" \
                  "See $AEM_PG_LOG, or set AEM_PG_PORT to another port."
      return 1
    fi
    AEM_PG_STARTED_HERE=1
    aem_pg_say "Started PostgreSQL $AEM_PG_REQUIRED_MAJOR on 127.0.0.1:$AEM_PG_PORT."
  fi

  local attempt
  for attempt in $(seq 1 60); do
    "$AEM_PG_BIN/pg_isready" --host=127.0.0.1 --port="$AEM_PG_PORT" --quiet 2>/dev/null && break
    if [[ "$attempt" == 60 ]]; then
      aem_pg_fail "PostgreSQL is running but not accepting connections on port $AEM_PG_PORT."
      aem_pg_stop_if_started
      return 1
    fi
    sleep 0.5
  done
  if ! aem_pg_bootstrap; then
    aem_pg_stop_if_started
    return 1
  fi
}

# Stop the cluster, but only if aem_pg_start started it in this shell.
aem_pg_stop_if_started() {
  [[ "${AEM_PG_STARTED_HERE:-0}" == 1 ]] || return 0
  AEM_PG_STARTED_HERE=0
  "$AEM_PG_BIN/pg_ctl" -D "$AEM_PG_DATA" -m fast -w -t 60 stop >/dev/null \
    && aem_pg_say "Stopped." \
    || aem_pg_fail "Could not stop the cluster cleanly; see $AEM_PG_LOG."
}

aem_pg_stop() {  # explicit stop of this project's cluster, whoever started it
  aem_pg_settings
  aem_pg_find_bin || return 1
  if ! aem_pg_is_running; then
    aem_pg_say "Not running."
    return 0
  fi
  "$AEM_PG_BIN/pg_ctl" -D "$AEM_PG_DATA" -m fast -w -t 60 stop >/dev/null && aem_pg_say "Stopped."
}

aem_pg_status() {
  aem_pg_settings
  aem_pg_find_bin || return 1
  if [[ ! -f "$AEM_PG_DATA/PG_VERSION" ]]; then
    aem_pg_say "No cluster yet in $AEM_PG_DATA. './scripts/local_postgres.sh start' creates it."
    return 3
  fi
  if aem_pg_is_running; then
    aem_pg_say "Running: postgresql://$AEM_PG_USER@127.0.0.1:$AEM_PG_PORT/$AEM_PG_NAME (data in $AEM_PG_DATA)"
  else
    aem_pg_say "Stopped (data in $AEM_PG_DATA)."
    return 3
  fi
}

# Run as a command only when executed, not when sourced by dev.sh.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -uo pipefail
  case "${1:-}" in
    start)  aem_pg_start ;;
    stop)   aem_pg_stop ;;
    status) aem_pg_status ;;
    *) echo "usage: $0 {start|stop|status}" >&2; exit 2 ;;
  esac
fi
