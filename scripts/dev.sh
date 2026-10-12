#!/usr/bin/env bash
# Development mode: the API with reload on 127.0.0.1:8787, and the Vite dev
# server on 127.0.0.1:5173 proxying /api and /health to it. Open the Vite URL.
#
# Both stop together on Ctrl-C.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x .venv/bin/python ]]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
fi

if [[ ! -d frontend/node_modules ]]; then
  echo "Installing frontend dependencies..."
  (cd frontend && npm install)
fi

"$PYTHON" scripts/manage.py init

cleanup() {
  [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$PYTHON" scripts/manage.py serve --reload &
BACKEND_PID=$!

(cd frontend && npm run dev) &
FRONTEND_PID=$!

echo
echo "Backend:  http://127.0.0.1:8787  (API docs at /docs)"
echo "Frontend: http://127.0.0.1:5173  <- open this one"
echo
wait -n "$BACKEND_PID" "$FRONTEND_PID"
