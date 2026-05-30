#!/usr/bin/env bash
#
# Long-running launcher for MyPF on the Mac mini.
#
# Starts the FastAPI backend (:8000) and the Vite dev server (:5173) detached,
# so they keep running after your SSH/VNC session ends. Logs + PIDs go to
# ./.run/. Re-running is safe — it skips anything already up.
#
#   ./scripts/serve.sh          # start both
#   ./scripts/stop.sh           # stop both
#   tail -f .run/backend.log    # watch logs
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/.run"
mkdir -p "$RUN"
PY="$ROOT/.venv/bin/python"

start() {
  local name="$1" dir="$2"; shift 2
  if [ -f "$RUN/$name.pid" ] && kill -0 "$(cat "$RUN/$name.pid" 2>/dev/null)" 2>/dev/null; then
    echo "• $name already running (pid $(cat "$RUN/$name.pid"))"
    return
  fi
  # caffeinate -i keeps the Mac awake while the server runs (no sudo needed).
  ( cd "$dir" && nohup caffeinate -i "$@" > "$RUN/$name.log" 2>&1 & echo $! > "$RUN/$name.pid" )
  sleep 1
  echo "✓ $name started (pid $(cat "$RUN/$name.pid")) → $RUN/$name.log"
}

# Backend MUST run from backend/ so pydantic loads backend/.env.
start backend  "$ROOT/backend"  "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Frontend dev server, exposed on LAN/Tailscale/ngrok (vite.config: host + allowedHosts).
start frontend "$ROOT/frontend" npx vite --host --port 5173

echo
echo "Local:     http://127.0.0.1:5173"
echo "Tailscale: http://100.86.213.17:5173   (open from your phone if it's on the tailnet)"
echo "Public:    run  'ngrok http 5173'  for a shareable URL (see docs/running-on-mac-mini.md)"
