#!/usr/bin/env bash
# Start the Apartment Expense Manager: migrate the database, then serve the API
# and the built frontend on http://127.0.0.1:8787.
#
#   ./scripts/run.sh                 serve
#   ./scripts/run.sh --reload        serve, reloading on code changes
#
# Set AEM_DB_PATH to use a database somewhere other than ./data/apartment.sqlite.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x .venv/bin/python ]]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
fi

if ! "$PYTHON" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  echo "FastAPI and uvicorn are not installed for $PYTHON." >&2
  echo "Install them first, for example:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

"$PYTHON" scripts/manage.py init
if [[ ! -d frontend/dist ]]; then
  echo
  echo "The frontend is not built yet; the API will serve without a UI."
  echo "Build it once with:  cd frontend && npm install && npm run build"
  echo
fi
exec "$PYTHON" scripts/manage.py serve "$@"
