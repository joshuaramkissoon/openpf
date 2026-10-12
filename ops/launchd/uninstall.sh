#!/usr/bin/env bash
#
# Stop + unload the OpenPF backend launchd agent (e.g. to switch back to a manual
# `uvicorn --reload` dev workflow). Leaves the rendered plist in place unless
# REMOVE_PLIST=1.
set -euo pipefail

LABEL="com.openpf.backend"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
echo "Unloaded $LABEL (backend no longer supervised)."

if [ "${REMOVE_PLIST:-0}" = "1" ] && [ -f "$PLIST" ]; then
  rm -f "$PLIST"
  echo "Removed $PLIST"
fi
