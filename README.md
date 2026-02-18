# MyPF Portfolio Operator

A full-stack portfolio operations system for Trading 212 users.

## Core Capabilities

- Unified portfolio view across Trading 212 `invest` and `stocks_isa` accounts.
- Corrected cash and equity math with per-account breakdown + combined NAV.
- Agent run pipeline:
  - Claude Agent SDK path (primary when configured),
  - rule-based fallback when Claude is unavailable.
- Guarded execution flow: `proposed -> approved/rejected -> executed`.
- Streaming Archie chat with persistent session context, markdown rendering, and live tool/thinking status.
- Leveraged trading desk:
  - editable risk rails
  - scan/cycle workflows
  - signal queue and open-trade management
  - policy-respecting auto-execute option
- Separate execution queue with scheduled tasks, run-now controls, and task logs.
- Telegram operator channel for updates + commands.
- Local persistence with SQLite.

## Stack

- Frontend: React + TypeScript + Vite
- Backend: FastAPI + SQLAlchemy + APScheduler
- Worker: separate Python process for scheduled tasks + Telegram polling
- Data: SQLite (`backend/.env`: `DATABASE_URL=sqlite:///./mypf.db`)
- Agent runtime: Claude Agent SDK (`claude-agent-sdk`)

## Workspace Surface

- `Overview`: aggregate portfolio metrics, allocation map, position intelligence, brief.
- `Research`: theses board, backtest lab, events, run history.
- `Execution`: intent queue + execution audit trail.
- `Leveraged`: rails editor, signal queue, open trades, scheduler execution queue.
- `Archie`: full-screen streaming chat with persistent conversations.
- `Settings`: control tower modal for broker/config/telegram/presentation mode.

## Quick Start

### 1) Backend

```bash
cd /Users/joshuaramkissoon/dev/mypf/backend
cp .env.example .env
python3 -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
../.venv/bin/uvicorn app.main:app --reload --port 8000
```

API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

In a second terminal run the worker:

```bash
cd /Users/joshuaramkissoon/dev/mypf/backend
../.venv/bin/python worker.py
```

### 2) Frontend

```bash
cd /Users/joshuaramkissoon/dev/mypf/frontend
cp .env.example .env
npm install
npm run dev
```

Dashboard: [http://localhost:5173](http://localhost:5173)

## Docker Compose Modes

From `/Users/joshuaramkissoon/dev/mypf`:

```bash
# Hot reload mode (default `make up`)
make up-hot

# Stable mode (no code hot-reload; safer for uninterrupted testing)
make up-stable

# Same behavior via mode flag
make up MODE=hot
make up MODE=stable
```

Useful companion commands:

```bash
# Follow logs
make logs-hot
make logs-stable

# Restart without switching mode
make restart MODE=hot
make restart MODE=stable

# Stop
make down-hot
make down-stable
```

Notes:
- `docker-compose.yml` is the stable baseline.
- `docker-compose.hot.yml` adds source mounts + reload/dev server behavior.
- `make up` and `make restart` keep hot mode as the default for backward compatibility.

## Trading 212 Setup (Dual Account)

1. Create separate API keys for both account types:
   - Invest
   - Stocks ISA
2. In Control Tower:
   - Save Invest credentials
   - Save Stocks ISA credentials
   - If `.env` account-specific credentials are set, backend treats them as source-of-truth over stale DB values.
3. Set environment:
   - `t212_base_env=demo` for dry runs
   - switch to `live` after validation
4. Use account selector (`All Accounts`, `Invest`, `Stocks ISA`) in UI.

Supported env names for account credentials:
- `T212_INVEST_API_KEY`, `T212_INVEST_API_SECRET`
- `T212_STOCKS_ISA_API_KEY`, `T212_STOCKS_ISA_API_SECRET`
- `T212_API_KEY_INVEST`, `T212_API_SECRET_INVEST`
- `T212_API_KEY_STOCKS_ISA`, `T212_API_SECRET_STOCKS_ISA`

## Claude Agent Runtime Setup

Set in `backend/.env`:

```env
AGENT_PROVIDER=claude
ANTHROPIC_API_KEY=...
CLAUDE_MODEL=claude-sonnet-4-20250514
CLAUDE_MEMORY_MODEL=claude-haiku-4-5
CLAUDE_SETTING_SOURCES=project
CLAUDE_PROJECT_CWD=.claude/runtime
CLAUDE_CHAT_ALLOW_WRITES=true
CLAUDE_MEMORY_STRATEGY=self_managed
CLAUDE_MEMORY_ENABLED=true
CLAUDE_MEMORY_MAX_FACTS=80
AGENT_WORKSPACE=./.claude/agent_workspace
AGENT_MAX_TURNS=6
AGENT_ALLOW_BASH=false
PORTFOLIO_DISPLAY_CURRENCY=GBP
INPROC_SCHEDULER_ENABLED=false
```

Notes:
- If Claude SDK/path is unavailable, system falls back to rule-based analyst logic.
- Trading 212 skill is loaded from project skills: `/Users/joshuaramkissoon/dev/mypf/.claude/skills/trading212-api`.
- Backend SDK runtime uses `setting_sources` from `CLAUDE_SETTING_SOURCES` (default `project`) so only project-scoped skills are loaded unless you opt into `user`.
- SDK `cwd` is controlled by `CLAUDE_PROJECT_CWD` (default `.claude/runtime`) so Claude can write artifacts in a scoped subdirectory.
- Agent workspace defaults to `AGENT_WORKSPACE=./.claude/agent_workspace`.
- Memory strategy:
  - `CLAUDE_MEMORY_STRATEGY=self_managed` (default): Claude manages durable memory updates directly using `Write/Edit`.
  - `CLAUDE_MEMORY_STRATEGY=distill`: background distiller (Haiku) updates memory only when memory cues are detected.
  - `CLAUDE_MEMORY_STRATEGY=off`: disable automated memory updates.

## Optional Research Connectors

```env
NEWSAPI_API_KEY=...
X_API_BEARER_TOKEN=...
```

Without these keys:
- news and X tools return empty results,
- web research falls back to DuckDuckGo instant API.

## Telegram Setup

1. Create a bot with [@BotFather](https://t.me/BotFather).
2. Send `/start` to the bot from your Telegram account.
3. In Control Tower -> Telegram Ops:
   - enable bot
   - set token
   - set chat id (or auto-bind on first inbound message)
   - optionally set allowed user IDs
4. Click `Send test ping`.

Commands:

- `/status`
- `/accounts`
- `/run`
- `/theses`
- `/intents`
- `/approve <intent_prefix>`
- `/reject <intent_prefix>`
- `/execute <intent_prefix>`
- `/help`
- `/lev status`
- `/lev scan`
- `/lev cycle`
- `/lev policy`
- `/lev auto on|off`
- `/lev close <trade_id_prefix>`

## Risk Rails

Defaults (editable in UI):

- max single order notional
- max daily notional
- max position weight cap
- duplicate order suppression window

## Key Endpoints

Portfolio + broker:
- `POST /api/portfolio/refresh`
- `GET /api/portfolio/snapshot?account_kind=all|invest|stocks_isa&display_currency=GBP|USD`
- `GET /api/broker/auth-check`
- `GET /api/broker/auth-check/{account_kind}`

Agent + chat:
- `POST /api/agent/run`
- `GET /api/agent/runs`
- `GET /api/agent/runs/{run_id}`
- `GET /api/agent/intents`
- `POST /api/agent/intents/{intent_id}/approve`
- `POST /api/agent/intents/{intent_id}/reject`
- `POST /api/agent/intents/{intent_id}/execute`
- `GET /api/agent/events`
- `GET /api/agent/chat/runtime`
- `GET /api/agent/chat/sessions`
- `POST /api/agent/chat/sessions`
- `DELETE /api/agent/chat/sessions/{session_id}`
- `GET /api/agent/chat/sessions/{session_id}/messages`
- `POST /api/agent/chat/sessions/{session_id}/messages`
- `WS /api/agent/chat/sessions/{session_id}/stream`

Config + telegram:
- `GET /api/config`
- `PUT /api/config/risk`
- `PUT /api/config/broker`
- `PUT /api/config/watchlist`
- `PUT /api/config/leveraged`
- `GET /api/config/credentials`
- `PUT /api/config/credentials`
- `PUT /api/config/credentials/{account_kind}`
- `GET /api/telegram/config`
- `PUT /api/telegram/config`
- `POST /api/telegram/test`
- `POST /api/telegram/poll`
- `POST /api/telegram/ask`

Leveraged:
- `GET /api/leveraged/snapshot`
- `GET /api/leveraged/policy`
- `PATCH /api/leveraged/policy`
- `POST /api/leveraged/scan`
- `POST /api/leveraged/cycle`
- `POST /api/leveraged/signals/{signal_id}/execute`
- `POST /api/leveraged/trades/{trade_id}/close`
- `POST /api/leveraged/cache/instruments`

Scheduler:
- `GET /api/scheduler/tasks`
- `POST /api/scheduler/tasks`
- `PATCH /api/scheduler/tasks/{task_id}`
- `DELETE /api/scheduler/tasks/{task_id}`
- `GET /api/scheduler/tasks/{task_id}/logs`
- `POST /api/scheduler/tasks/{task_id}/run`
- `POST /api/scheduler/run-due`
- `POST /api/scheduler/seed-defaults`

Strategy + theses:
- `POST /api/strategy/backtest`
- `GET /api/theses`
- `POST /api/theses`
- `PATCH /api/theses/{thesis_id}/status`
- `DELETE /api/theses/{thesis_id}`

## Notes and Constraints

- Trading 212 API is beta and rate-limited.
- API supports Invest and Stocks ISA account types.
- Order submission is non-idempotent in beta; duplicate guards are enforced app-side.
- Sell orders are submitted with negative quantity.
- IP allowlisting uses your public egress IP, not local LAN IP.

## Testing

```bash
cd /Users/joshuaramkissoon/dev/mypf
python3 -m compileall backend/app backend/tests
```

If dependencies are installed:

```bash
cd /Users/joshuaramkissoon/dev/mypf
.venv/bin/pytest backend/tests -q
```

## Documentation Hygiene

When changing product surface, keep docs in the same PR:
- Update this README sections: `Core Capabilities`, `Workspace Surface`, `Key Endpoints`, env vars.
- If Archie behavior changes, update `/Users/joshuaramkissoon/dev/mypf/.claude/runtime/.claude/CLAUDE.md`.
- If strategy/process changes, update `/Users/joshuaramkissoon/dev/mypf/.claude/runtime/memory/specs/leveraged-trading-system.md`.

## Disclaimer

For tooling and research workflows only. You are responsible for strategy validation, regulatory obligations, and all live-trading risk.
