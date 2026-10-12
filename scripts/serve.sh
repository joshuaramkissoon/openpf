#!/usr/bin/env bash
#
# Long-running launcher for OpenPF on an always-on machine (e.g. a Mac mini).
#
# Starts the FastAPI backend (:8000) and the Vite dev server (:5173) detached,
# so they keep running after your SSH/VNC session ends. Logs + PIDs go to
# ./.run/. Re-running is safe — it skips anything already up.
#
#   ./scripts/serve.sh          # start both
#   ./scripts/stop.sh           # stop both
#   tail -f .run/backend.log    # watch logs
#
# Binds to localhost only by default. To reach it from other devices (LAN /
# Tailscale), set OPENPF_API_TOKEN in backend/.env first, then e.g.:
#
#   VITE_HOST=0.0.0.0 VITE_ALLOWED_HOSTS=my-mac.tailnet.ts.net ./scripts/serve.sh
#
#   BACKEND_HOST   uvicorn bind host     (default 127.0.0.1 — the Vite proxy reaches it locally)
#   BACKEND_PORT   uvicorn port          (default 8000)
#   VITE_HOST      Vite dev-server host  (default localhost)
#   VITE_PORT      Vite dev-server port  (default 5173)
#   PUBLIC_HOST    hostname/IP to print as the remote URL (optional)
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

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
VITE_PORT="${VITE_PORT:-5173}"
export VITE_HOST="${VITE_HOST:-localhost}"
export API_PROXY_TARGET="${API_PROXY_TARGET:-http://127.0.0.1:$BACKEND_PORT}"

# Backend MUST run from backend/ so pydantic loads backend/.env.
start backend  "$ROOT/backend"  "$PY" -m uvicorn app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
# Frontend dev server (vite.config.ts reads VITE_HOST / VITE_ALLOWED_HOSTS).
start frontend "$ROOT/frontend" npx vite --port "$VITE_PORT"

echo
echo "Local:     http://localhost:$VITE_PORT"
if [ -n "${PUBLIC_HOST:-}" ]; then
  echo "Remote:    http://$PUBLIC_HOST:$VITE_PORT"
fi
echo "Public:    run  'ngrok http $VITE_PORT'  for a shareable URL (see docs/running-on-mac-mini.md)"
