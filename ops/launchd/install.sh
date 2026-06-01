#!/usr/bin/env bash
#
# Install + load the MyPF backend as a self-healing launchd user agent.
#
# launchd's KeepAlive restarts the backend on crash (the original incident was
# a torch/MPS SIGSEGV that left the app dead with no recovery). RunAtLoad
# starts it immediately, so running this also brings a down backend back up.
#
# Repo root defaults to two levels up from this script. Override with
# MYPF_REPO_ROOT to point the agent at a specific checkout (e.g. the main repo
# rather than a git worktree).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${MYPF_REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

VENV="$REPO_ROOT/.venv"
UVICORN="$VENV/bin/uvicorn"
WORKDIR="$REPO_ROOT/backend"
LOG="$REPO_ROOT/.run/backend.log"
LABEL="com.mypf.backend"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[ -x "$UVICORN" ] || { echo "ERROR: uvicorn not found at $UVICORN — create the venv + install backend/requirements.txt first." >&2; exit 1; }
[ -d "$WORKDIR" ] || { echo "ERROR: backend dir not found at $WORKDIR" >&2; exit 1; }

# Build a PATH that includes the venv, node, and the `claude` CLI — the Agent
# SDK launches `claude`/node as subprocesses and a bare launchd PATH lacks them.
EXTRA="$VENV/bin"
NODE_BIN="$(command -v node || true)"; [ -n "$NODE_BIN" ] && EXTRA="$EXTRA:$(dirname "$NODE_BIN")"
CLAUDE_BIN="$(command -v claude || true)"; [ -n "$CLAUDE_BIN" ] && EXTRA="$EXTRA:$(dirname "$CLAUDE_BIN")"
PATH_VALUE="$EXTRA:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$HOME/Library/LaunchAgents" "$REPO_ROOT/.run"

sed \
  -e "s|@LABEL@|$LABEL|g" \
  -e "s|@UVICORN@|$UVICORN|g" \
  -e "s|@WORKDIR@|$WORKDIR|g" \
  -e "s|@PATH@|$PATH_VALUE|g" \
  -e "s|@LOG@|$LOG|g" \
  "$SCRIPT_DIR/com.mypf.backend.plist.template" > "$PLIST"

DOMAIN="gui/$(id -u)"
# Reload idempotently.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true

echo "Loaded $LABEL"
echo "  repo:  $REPO_ROOT"
echo "  serve: http://127.0.0.1:8000  (KeepAlive on — restarts on crash)"
echo "  logs:  $LOG"
echo
echo "NOTE: this runs uvicorn WITHOUT --reload. For active development with"
echo "hot-reload, run ops/launchd/uninstall.sh first, then 'uvicorn ... --reload'."
