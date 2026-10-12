#!/usr/bin/env bash
#
# Try OpenPF in under a minute — no broker account, no real money.
#
# Runs the app against the built-in sandbox broker: a mock Trading 212 with a
# realistic two-account (Invest + Stocks ISA) portfolio, priced with real market
# data. Orders you approve fill against the mock. Archie uses your Claude login
# (`claude` CLI) or ANTHROPIC_API_KEY.
#
#   ./scripts/demo.sh            # http://localhost:5173
#   ./scripts/demo.sh --reset    # start again from a fresh sandbox portfolio
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="$ROOT/.demo"
API_PORT="${API_PORT:-8010}"
WEB_PORT="${WEB_PORT:-5173}"

if [ "${1:-}" = "--reset" ]; then
  rm -rf "$DEMO_DIR"
  echo "Sandbox reset."
fi
mkdir -p "$DEMO_DIR"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "→ creating Python venv"
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"
fi
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "→ installing frontend dependencies"
  (cd "$ROOT/frontend" && npm install --silent)
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "→ starting API (sandbox broker) on :$API_PORT"
(
  cd "$ROOT/backend"
  OPENPF_SANDBOX=1 \
  PORT="$API_PORT" \
  INPROC_SCHEDULER_ENABLED=false \
  DATABASE_URL="sqlite:///$DEMO_DIR/openpf-demo.db" \
  OPENPF_SANDBOX_STATE="$DEMO_DIR/sandbox_state.json" \
  VIEWS_DATA_DIR="$DEMO_DIR/views" \
  "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" --log-level warning
) &

echo "→ starting dashboard on :$WEB_PORT"
(
  cd "$ROOT/frontend"
  API_PROXY_TARGET="http://127.0.0.1:$API_PORT" npx vite --port "$WEB_PORT" --strictPort --logLevel warn
) &

sleep 4
echo
echo "  OpenPF demo → http://localhost:$WEB_PORT   (SANDBOX — not real money)"
echo "  Ctrl+C to stop."
wait
