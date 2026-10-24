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
if ! command -v npm >/dev/null 2>&1; then
  echo "Install npm (included with Node.js) before running Fido." >&2
  exit 1
fi
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 20 || (major === 20 && minor < 9)) { console.error("Fido requires Node.js 20.9 or newer; found " + process.versions.node); process.exit(1); }' || exit 1
# A previous or interrupted install can leave node_modules present without
# installing Next.js. Check the local executable, not just the directory.
if [ ! -x node_modules/.bin/next ]; then
  echo "Installing project dependencies (including Next.js)…"
  if [ -f package-lock.json ]; then npm ci; else npm install; fi
fi
case "${1:-dev}" in
  dev) echo "Fido → http://localhost:$PORT (Ctrl+C to stop)"; exec npm run dev ;;
  check) npm run typecheck; npm test ;;
  build) exec npm run build ;;
  start) exec npm start ;;
  *) echo "Usage: sh run.sh [dev|check|build|start]" >&2; exit 1 ;;
esac
