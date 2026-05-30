# Running MyPF on the Mac mini (long-running + remote access)

Goal: keep the app running after your SSH/VNC session ends, and reach it from
your phone when you're out.

## Quick start

```bash
cd ~/dev/mypf
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

### Option A — Tailscale (recommended: private, zero setup, no warning page)
The Mac mini is already on your tailnet at `100.86.213.17`. Install the
**Tailscale app on your phone**, sign in, and open:

```
http://100.86.213.17:5173
```

That's it — encrypted, no public exposure, works anywhere.

### Option B — ngrok (public URL, shareable)
For a URL that works without Tailscale (e.g. to share):

```bash
ngrok http 5173          # one-time: ngrok config add-authtoken <token>
```

Open the `https://<random>.ngrok-free.app` URL it prints. This works as-is:
- `vite.config.ts` sets `host: true` + `allowedHosts: true`, so Vite accepts the ngrok hostname.
- Vite proxies `/api` → backend (`ws: true`), so REST **and** the Archie chat WebSocket tunnel through the single port.

Caveats: the free tier shows a one-time browser interstitial (click through),
and the URL changes each run (a paid static domain or Tailscale avoids both).

## Survive reboots (optional) — launchd

For the servers to come back automatically after a reboot, install a LaunchAgent:

`~/Library/LaunchAgents/com.mypf.serve.plist`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.mypf.serve</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>cd /Users/joshuaramkissoon/dev/mypf && ./scripts/serve.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>StandardOutPath</key><string>/Users/joshuaramkissoon/dev/mypf/.run/launchd.log</string>
  <key>StandardErrorPath</key><string>/Users/joshuaramkissoon/dev/mypf/.run/launchd.err</string>
</dict>
</plist>
```
```bash
launchctl load  ~/Library/LaunchAgents/com.mypf.serve.plist   # enable
launchctl unload ~/Library/LaunchAgents/com.mypf.serve.plist  # disable
```

## tmux alternative (manual, interactive)

If you prefer to watch them live and reattach later:

```bash
tmux new -s mypf
#   pane 1:  cd backend  && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
#   pane 2:  cd frontend && npx vite --host
# detach with: Ctrl-b d   •   reattach with: tmux attach -t mypf
```

## Notes
- Backend must launch from `backend/` (pydantic reads `backend/.env`).
- Logs + PIDs live in `.run/` (gitignored).
- To enable the automated daily jobs, set `INPROC_SCHEDULER_ENABLED=true` in `backend/.env`.
