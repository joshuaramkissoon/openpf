# launchd supervision for the MyPF backend

The backend used to run as a bare `uvicorn … --reload` in a terminal. `--reload`
only restarts on **file changes**, never on a crash — so when a native
`SIGSEGV` (torch/MPS, see the forecast fix) killed the process, the app stayed
down with no recovery ("no self-healing").

This installs the backend as a **launchd user agent** with `KeepAlive`, so any
exit (crash included) is restarted automatically, and `RunAtLoad` keeps it
running across login/reboot.

No `caffeinate` wrapper: this Mac mini already has system sleep disabled on AC
power (`pmset -g` shows `sleep 0`), so the box never idle-sleeps and launchd
only needs to handle crash-restart + start-at-login. This **replaces** the old
`caffeinate`-based serve script — don't run both (they collide on :8000). If the
machine's power policy ever changes to allow sleep, re-add a `caffeinate -i`
wrapper to `ProgramArguments`.

## Use

```bash
# install + start (also brings a down backend back up):
ops/launchd/install.sh

# stop + unload (e.g. to develop with hot-reload again):
ops/launchd/uninstall.sh

# status / logs:
launchctl print gui/$(id -u)/com.mypf.backend | head
tail -f .run/backend.log

# after pulling new backend code, restart in place:
launchctl kickstart -k gui/$(id -u)/com.mypf.backend
```

## Notes

- Runs uvicorn **without `--reload`** (required for KeepAlive to see a crash as
  an exit). For active development with hot-reload, `uninstall.sh` first.
- `install.sh` derives paths from the repo root (override with `MYPF_REPO_ROOT`)
  and bakes a `PATH` that includes the venv, `node`, and the `claude` CLI, which
  the Agent SDK launches as subprocesses.
- stdout/stderr are captured to `.run/backend.log`, so a future native crash is
  actually recorded (the original incident logged only to a terminal).
- This supervises only the backend. The Vite frontend is unaffected; add a
  sibling agent if you want it supervised too.
