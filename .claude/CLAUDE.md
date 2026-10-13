# OpenPF — Development Context

## Architecture
- **Frontend**: React (Vite) — `frontend/src/`
- **Backend**: FastAPI + SQLite — `backend/app/`
- **Chat runtime**: Claude Agent SDK with project setting source enabled
- **Archie's runtime memory**: `.claude/runtime/memory/` (managed by Archie, not dev tooling)

## Key Directories
| Path | Purpose |
|------|---------|
| `frontend/src/components/` | React UI components |
| `frontend/src/api/` | API client layer |
| `backend/app/services/` | Core backend services (chat runtime, memory, portfolio) |
| `backend/app/api/` | FastAPI route handlers |
| `.claude/runtime/.claude/CLAUDE.md` | Archie's identity + memory map (SDK reads this) |
| `.claude/runtime/memory/` | Archie's persistent memory files |

## Conventions
- Chat UI should be clean and information-dense
- Assistant responses render markdown clearly (tables, lists, headers)
- Tool activity visible when relevant
- Presentation mode obfuscates sensitive numeric portfolio values
- Default display currency: GBP

## Agent Capabilities (for prompt/context work)
- **MCP tool servers**: `marketdata` (price/history/technicals/risk/correlation/compare),
  `fundamentals` (fundamentals/valuation/statements/earnings), `forecast` (Kronos p10/p50/p90),
  `scheduler` (cron tasks), `trading212` (read-only account data), `proposals` (`propose_trade`),
  `recipes` (Archie-written routines: skills + triggers, versioned; see `recipes_service.py`),
  `smart_money` (insider Form 4 / 13F / Congress disclosures + trend detector; `app/services/smart_money/`).
  `crypto` (read-only, keyless view of the connected crypto exchange + the Starter crypto play draft).
  All servers are declared once in `backend/app/services/mcp_registry.py`.
- **Subagents**: researcher (Sonnet — web + marketdata + fundamentals), quant (Opus — Bash +
  `app.quant` + marketdata + forecast). There is no execution subagent.
- **Execution gate**: the agent can only propose trades (`proposed` TradeIntents). A human approves,
  or a user-granted standing permission does (`permissions_service`, `approved_by="permission:<id>"`,
  applied by a backend sweeper — never from an agent/MCP process);
  `execution_service.execute_intent` is the single path to a live order and re-checks every rail.
  No MCP tool may create or loosen a permission (Archie can only `suggest_permission`).
  The leveraged engine places orders itself only under its own rails and the user's
  `auto_execute_enabled` flag.
- **Track record**: `track_record_service` marks every proposal at 1d/1w/1m/3m/close vs a benchmark
  (`proposal_outcomes`); `/api/track-record`, MCP `get_track_record`.
- **Crypto**: exchange-agnostic via CCXT (`crypto_client.py`, `crypto_service.py`); account_kind
  `crypto` in snapshots/book/intents. Keys must be trade-only — verified on connect, refused if they
  can withdraw — and never reach an agent process. `broker_router.py` is the one routing point in
  `execute_intent`; the crypto cap is a rail in `rail_violations`.
- **Leveraged engine**: regime-aware 3x ISA ETP scan/monitor/execute under hard daily rails.
  T212 has **no short selling** — downside is via **INVERSE ETPs (ISA-only)**.
- **Autonomous loop**: scheduled morning cycle, midday/EOD monitors, weekly review, daily alpha goal.

## Risk Guardrails
- Never suggest bypassing configured risk rails (daily profit/loss/trade caps are hard-enforced)
- Always call out concentration, liquidity, and downside risk
- Distinguish analysis from execution; never imply trades are executed unless confirmed

## User Context
- The owner's name comes from `OPENPF_USER_NAME`; personal facts live in Archie's
  gitignored memory (`.claude/runtime/memory/user-profile.md`), never in tracked files.
- Accounts: Trading 212 Invest + Stocks ISA, optionally a crypto exchange (Kraken / Coinbase / Binance)
