#!/bin/sh
# Usage: sh run.sh [all|dev|backend|check|test|build|start]
#   all      backend on :5000 + frontend on :3000 (default)
#   dev      frontend only; expects the backend at NEXT_PUBLIC_FIDO_API or <page host>:5000
#   backend  backend only (creates backend/.venv on first run)
#   check    typecheck + backend unit tests
#   test     Playwright end-to-end tests (start both servers if needed)
# Change PORT to choose a different frontend port. Both servers listen on your local network.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PORT="${PORT:-3000}"
if ! command -v node >/dev/null 2>&1; then
  echo "Install Node.js 20.9+ before running Fido." >&2
  exit 1
fi
if [ ! -d node_modules ]; then
  if [ -f package-lock.json ]; then npm ci; else npm install; fi
fi
backend_env() {
  if [ ! -x backend/.venv/bin/python ]; then
    command -v python3 >/dev/null 2>&1 || { echo "Install Python 3.11+ for Fido's backend." >&2; exit 1; }
    python3 -m venv backend/.venv
    backend/.venv/bin/pip install -q -r backend/requirements-dev.txt
  fi
}
case "${1:-all}" in
  all)
    backend_env
    (cd backend && exec .venv/bin/python -m fido --port 5000) &
    backend_pid=$!
    trap 'kill "$backend_pid" 2>/dev/null' EXIT INT TERM
    echo "Fido → http://localhost:$PORT (backend http://localhost:5000; Ctrl+C to stop)"
    npm run dev ;;
  dev) echo "Fido → http://localhost:$PORT (Ctrl+C to stop)"; exec npm run dev ;;
  backend) backend_env; cd backend && exec .venv/bin/python -m fido --port 5000 ;;
  check) backend_env; npm run typecheck; (cd backend && .venv/bin/python -m pytest -q) ;;
  test) backend_env; exec npx playwright test ;;
  build) exec npm run build ;;
  start) exec npm start ;;
  *) echo "Usage: sh run.sh [all|dev|backend|check|test|build|start]" >&2; exit 1 ;;
esac
