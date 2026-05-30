#!/usr/bin/env bash
#
# Stop the long-running MyPF servers started by scripts/serve.sh.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/.run"

for name in backend frontend; do
  pidfile="$RUN/$name.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      # caffeinate spawns the real server as a child; kill the process group.
      kill "$pid" 2>/dev/null || true
      pkill -P "$pid" 2>/dev/null || true
      echo "✓ $name stopped (pid $pid)"
    else
      echo "• $name not running"
    fi
    rm -f "$pidfile"
  else
    echo "• $name has no pidfile"
  fi
done
