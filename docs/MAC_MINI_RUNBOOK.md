# Mac Mini Runbook (MyPF V2)

## 1) Required Secrets

Set these in a private env file outside workspace (recommended):

```env
# Trading 212
T212_BASE_ENV=live
T212_INVEST_API_KEY=...
T212_INVEST_API_SECRET=...
T212_STOCKS_ISA_API_KEY=...
T212_STOCKS_ISA_API_SECRET=...

# Claude runtime
AGENT_PROVIDER=claude
ANTHROPIC_API_KEY=...
CLAUDE_MODEL=claude-sonnet-4-20250514
AGENT_WORKSPACE=./agent_workspace
AGENT_MAX_TURNS=6
AGENT_ALLOW_BASH=false

# Optional connectors
NEWSAPI_API_KEY=...
X_API_BEARER_TOKEN=...

# Telegram
# configure in UI or via API config endpoint
```

## 2) Recommended Secret Handling

Store env file outside project, e.g.:
- `~/.config/mypf/backend.env`

Permissions:

```bash
chmod 600 ~/.config/mypf/backend.env
```

Run backend:

```bash
set -a
source ~/.config/mypf/backend.env
set +a
cd /Users/joshuaramkissoon/dev/mypf/backend
/Users/joshuaramkissoon/dev/mypf/.venv/bin/uvicorn app.main:app --reload --port 8000
```

## 3) First Boot Checklist

1. Start backend and frontend.
2. Open dashboard and verify config status bars.
3. Call auth checks:
   - `/api/broker/auth-check`
   - `/api/broker/auth-check/invest`
   - `/api/broker/auth-check/stocks_isa`
4. Run `Refresh Portfolio`.
5. Verify account totals in top account breakdown row.

## 4) Trading 212 IP Restriction

Use your public egress IP (not local LAN IP):

```bash
curl -4 https://api.ipify.org; echo
curl -6 https://api64.ipify.org; echo
```

If ISP IP changes, update Trading 212 allowlist.

## 5) Telegram Operator Setup

1. Create bot with `@BotFather`.
2. Send `/start` to bot.
3. Configure token + enable Telegram in Control Tower.
4. Send test ping.
5. Use commands:
   - `/status`
   - `/accounts`
   - `/run`
   - `/theses`
   - `/intents`

## 6) Operational Safety Defaults

- Start with `BROKER_MODE=paper` even in live env.
- Keep `AUTOPILOT_ENABLED=false` until intent quality is validated.
- Set conservative risk rails in Control Tower.

## 7) Failure Triage

1. Cash/weights wrong:
- hit `/api/portfolio/refresh` then `/api/portfolio/snapshot?account_kind=all`
- verify account rows include both `invest` and `stocks_isa`

2. 401 auth:
- re-save credentials per account kind
- verify correct env (`demo` vs `live`)
- verify IP allowlist

3. Empty research:
- set optional connector keys
- without keys, only lightweight web fallback is used

4. Claude fallback active:
- ensure `ANTHROPIC_API_KEY` is set
- ensure `claude-agent-sdk` installed in backend venv
