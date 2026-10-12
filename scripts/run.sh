#!/usr/bin/env bash
# Start the Apartment Expense Manager: migrate the database, then serve the API
# and the built frontend on http://127.0.0.1:8787.
#
#   ./scripts/run.sh                 serve
#   ./scripts/run.sh --reload        serve, reloading on code changes
#
# This connects to the PostgreSQL database named by AEM_DATABASE_URL or the
# AEM_DB_* settings; it does not start or stop a database server. That makes it
# the right entry point for a deployment, where the database is managed
# elsewhere. To use the project's local cluster, bring it up first with
# ./scripts/local_postgres.sh start -- or use ./scripts/dev.sh, which does both.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x .venv/bin/python ]]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
fi

if ! "$PYTHON" -c 'import fastapi, uvicorn, psycopg, psycopg_pool' >/dev/null 2>&1; then
  echo "FastAPI, uvicorn and psycopg are not installed for $PYTHON." >&2
  echo "Install them first, for example:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

# Migrating takes the migration role, which owns the schema. Where this host
# has been given that role's password, migrate first; where it has not (a host
# that only serves), skip it -- the app reports a schema that is behind.
if "$PYTHON" -c 'import sys; from aem.config import get_settings
sys.exit(0 if get_settings().migration_database else 1)'; then
  "$PYTHON" scripts/manage.py init
else
  echo "No migration role configured here; not migrating. (Run 'scripts/manage.py init'"
  echo "wherever AEM_DB_MIGRATION_PASSWORD is available.)"
fi
# Serve as the runtime role only.
export AEM_DB_MIGRATION_PASSWORD="" AEM_MIGRATION_DATABASE_URL=""
if [[ ! -d frontend/dist ]]; then
  echo
  echo "The frontend is not built yet; the API will serve without a UI."
  echo "Build it once with:  cd frontend && npm install && npm run build"
  echo
fi
exec "$PYTHON" scripts/manage.py serve "$@"
