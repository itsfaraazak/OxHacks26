#!/bin/sh
# End-to-end localhost demo with no hardware:
#   backend + simulated robot (heartbeat, NFC dock, good-boy card) + detector agent.
#
# Usage (from backend/):  sh scripts/demo.sh [camera]
#   camera: webcam index (default 0) or a video file to loop, e.g. clip.mp4
# Open the frontend (or http://localhost:5000/api/state) while it runs. Ctrl+C stops everything.
set -eu
cd "$(dirname "$0")/.."
PY=.venv/bin/python
API=http://localhost:${PORT:-5000}
JSON='Content-Type: application/json'
CAMERA=${1:-0}

PIDS=""
cleanup() { trap - EXIT INT TERM; kill $PIDS 2>/dev/null || true; }
trap cleanup EXIT INT TERM

if curl -sf "$API/api/health" >/dev/null; then
  echo "Something is already serving $API; stop it or set PORT." >&2
  exit 1
fi
$PY -m fido --port "${PORT:-5000}" &
PIDS="$PIDS $!"
until curl -sf "$API/api/health" >/dev/null; do sleep 0.2; done
echo "Backend up at $API"

# Short escalation so the demo is quick.
curl -s -X PATCH "$API/api/settings" -H "$JSON" -d '{"bark_after_seconds":3,"escape_after_seconds":3}' >/dev/null

# Simulated robot: heartbeat every second, like real hardware would.
(while true; do
  curl -s -X POST "$API/api/robot/events" -H "$JSON" -d '{"event":"heartbeat","info":{"board":"simulated","battery":87}}' >/dev/null || true
  sleep 1
done) &
PIDS="$PIDS $!"

# The hardware-free demo must not implicitly spend Presage credits.
$PY -m detector.agent --source laptop --camera "$CAMERA" --no-presage &
PIDS="$PIDS $!"

echo
echo "Robot simulator commands (run in another terminal):"
echo "  dock phone : curl -X POST $API/api/robot/events -H '$JSON' -d '{\"event\":\"docked\"}'"
echo "  good boy   : curl -X POST $API/api/robot/events -H '$JSON' -d '{\"event\":\"caught\"}'"
echo "  start      : curl -X POST $API/api/session -H '$JSON' -d '{\"task\":\"Demo\",\"duration_minutes\":25}'"
wait
