#!/bin/sh
# Usage: sh run.sh [dev|check|build|start]
# Change PORT to choose a different port. dev listens on your local network.
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
case "${1:-dev}" in
  dev) echo "Fido → http://localhost:$PORT (Ctrl+C to stop)"; exec npm run dev ;;
  check) npm run typecheck; npm test ;;
  build) exec npm run build ;;
  start) exec npm start ;;
  *) echo "Usage: sh run.sh [dev|check|build|start]" >&2; exit 1 ;;
esac
