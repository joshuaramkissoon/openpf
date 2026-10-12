# Running OpenPF on an always-on Mac (long-running + remote access)

Goal: keep the app running after your SSH/VNC session ends, and reach it from
your phone when you're out.

## Quick start

```bash
cd /path/to/openpf
./scripts/serve.sh     # starts backend (:8000) + frontend (:5173), detached
./scripts/stop.sh      # stops them
tail -f .run/backend.log .run/frontend.log
```

`serve.sh` uses `nohup` + `caffeinate -i`, so the servers survive your session
ending and the Mac won't idle-sleep while they run. Re-running it is safe.

To be sure the machine never sleeps when headless:

```bash
sudo pmset -a sleep 0 disablesleep 1   # optional, persists across reboots
```

## Remote access

Everything binds to localhost by default. Before exposing it anywhere, set
`OPENPF_API_TOKEN` in `backend/.env` — the dashboard will prompt for it once per
browser.

### Option A — Tailscale (recommended: private, no warning page)
Put the Mac on your tailnet, install the **Tailscale app on your phone**, then
start the dev server on all interfaces and allow your tailnet hostname:

```bash
VITE_HOST=0.0.0.0 VITE_ALLOWED_HOSTS=<your-mac>.<tailnet>.ts.net PUBLIC_HOST=<your-mac-tailscale-ip> ./scripts/serve.sh
```

Open `http://<your-mac-tailscale-ip>:5173` (or the MagicDNS name) from your phone.

### Option B — ngrok (public URL, shareable)
For a URL that works without Tailscale (e.g. to share):

```bash
ngrok http 5173          # one-time: ngrok config add-authtoken <token>
```

Open the `https://<random>.ngrok-free.app` URL it prints. This works as-is:
- Allow the ngrok hostname: `VITE_ALLOWED_HOSTS=<random>.ngrok-free.app ./scripts/serve.sh`
  (or `VITE_ALLOWED_HOSTS=all`). Set `OPENPF_API_TOKEN` first — this URL is public.
- Vite proxies `/api` → backend (`ws: true`), so REST **and** the Archie chat WebSocket tunnel through the single port.

Caveats: the free tier shows a one-time browser interstitial (click through),
and the URL changes each run (a paid static domain or Tailscale avoids both).

## Survive reboots (optional) — launchd

For the servers to come back automatically after a reboot, install a LaunchAgent:

`~/Library/LaunchAgents/com.openpf.serve.plist`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.openpf.serve</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>cd /path/to/openpf && ./scripts/serve.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>StandardOutPath</key><string>/path/to/openpf/.run/launchd.log</string>
  <key>StandardErrorPath</key><string>/path/to/openpf/.run/launchd.err</string>
</dict>
</plist>
```
```bash
launchctl load  ~/Library/LaunchAgents/com.openpf.serve.plist   # enable
launchctl unload ~/Library/LaunchAgents/com.openpf.serve.plist  # disable
```

## tmux alternative (manual, interactive)

If you prefer to watch them live and reattach later:

```bash
tmux new -s openpf
#   pane 1:  cd backend  && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
#   pane 2:  cd frontend && npx vite
# detach with: Ctrl-b d   •   reattach with: tmux attach -t openpf
```

## Notes
- Backend must launch from `backend/` (pydantic reads `backend/.env`).
- Logs + PIDs live in `.run/` (gitignored).
- To enable the automated daily jobs, set `INPROC_SCHEDULER_ENABLED=true` in `backend/.env`.
