#!/usr/bin/env bash
# Development mode: the project's PostgreSQL cluster on 127.0.0.1:55432, the
# API with reload on 127.0.0.1:8787, and the Vite dev server on 127.0.0.1:5173
# proxying /api and /health to it. Open the Vite URL.
#
# Order matters. PostgreSQL starts first and must be accepting connections
# before the schema is migrated or anything is served. On Ctrl-C or exit the
# API and Vite stop first, then PostgreSQL -- and PostgreSQL is stopped only if
# this run started it. A cluster that was already running is left running.
#
# The database's files stay in ./data/postgres between runs.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x .venv/bin/python ]]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
fi

# shellcheck source=scripts/local_postgres.sh
source scripts/local_postgres.sh

BACKEND_PID=""
FRONTEND_PID=""
CLEANED_UP=0

# Every descendant of a process, deepest first. "npm run dev" starts a shell
# that starts Vite, and uvicorn's reloader starts the real server; signalling
# only the process this script launched leaves those running as orphans.
descendants() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do
    descendants "$child"
    echo "$child"
  done
}

stop_process() {  # ask the whole tree, wait, and insist if anything has not gone
  local pid="$1" waited=0 member
  local -a tree=()
  [[ -n "$pid" ]] || return 0
  mapfile -t tree < <(descendants "$pid"; echo "$pid")
  for member in "${tree[@]}"; do kill "$member" 2>/dev/null || true; done
  while (( waited < 50 )); do
    local alive=0
    for member in "${tree[@]}"; do kill -0 "$member" 2>/dev/null && alive=1; done
    (( alive )) || break
    sleep 0.1; waited=$((waited + 1))
  done
  for member in "${tree[@]}"; do
    kill -0 "$member" 2>/dev/null && kill -9 "$member" 2>/dev/null || true
  done
  wait "$pid" 2>/dev/null || true
}

cleanup() {
  [[ "$CLEANED_UP" == 1 ]] && return 0
  CLEANED_UP=1
  trap '' INT TERM
  stop_process "$BACKEND_PID"
  stop_process "$FRONTEND_PID"
  # After the app, so no request is cut off mid-transaction by the database going away.
  aem_pg_stop_if_started || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ ! -d frontend/node_modules ]]; then
  echo "Installing frontend dependencies..."
  (cd frontend && npm install)
fi

aem_pg_start

# Development must always use the project-local cluster, even if .env.local or
# the invoking shell contains production/AWS connection settings. In
# particular, never let startup migrations target AEM_DATABASE_URL by accident.
export AEM_DATABASE_URL=""
export AEM_DB_HOST=127.0.0.1
export AEM_DB_PORT="$AEM_PG_PORT"
export AEM_DB_NAME="$AEM_PG_NAME"
export AEM_DB_USER="$AEM_PG_USER"
export AEM_DB_PASSWORD
export AEM_DB_MIGRATION_USER="$AEM_PG_OWNER"
export AEM_MIGRATION_DATABASE_URL=""
export AEM_DB_SSLMODE=""
export AEM_DB_SSLROOTCERT=""
export AEM_DB_SCHEMA=""

# Migrate as the migration role, which owns the schema. Then serve as the
# runtime role only: the serving process is started with the migration
# password blanked, so it could not change the schema even if it tried.
MIGRATION_PASSWORD="$AEM_DB_MIGRATION_PASSWORD"
AEM_DB_MIGRATION_PASSWORD="$MIGRATION_PASSWORD" "$PYTHON" scripts/manage.py init
unset MIGRATION_PASSWORD
export AEM_DB_MIGRATION_PASSWORD=""

"$PYTHON" scripts/manage.py serve --reload &
BACKEND_PID=$!

(cd frontend && exec npm run dev) &
FRONTEND_PID=$!

echo
echo "Database: postgresql://127.0.0.1:${AEM_PG_PORT}/${AEM_PG_NAME}  (files in ./data/postgres)"
echo "Backend:  http://127.0.0.1:8787  (API docs at /docs)"
echo "Frontend: http://127.0.0.1:5173  <- open this one"
echo
wait -n "$BACKEND_PID" "$FRONTEND_PID" || true
