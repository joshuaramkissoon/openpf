# MyPF — Development Context

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
| `backend/app/routers/` | FastAPI route handlers |
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
  `scheduler` (cron tasks), `trading212` (account/orders).
- **Subagents**: researcher (Sonnet — web + marketdata + fundamentals), quant (Opus — Bash +
  `app.quant` + marketdata + forecast), execution (Haiku — T212 orders).
- **Leveraged engine**: regime-aware 3x ISA ETP scan/monitor/execute under hard daily rails.
  T212 has **no short selling** — downside is via **INVERSE ETPs (ISA-only)**.
- **Autonomous loop**: scheduled morning cycle, midday/EOD monitors, weekly review, daily alpha goal.

## Risk Guardrails
- Never suggest bypassing configured risk rails (daily profit/loss/trade caps are hard-enforced)
- Always call out concentration, liquidity, and downside risk
- Distinguish analysis from execution; never imply trades are executed unless confirmed

## User Context
- User name: Josh
- Accounts: Trading 212 Invest + Stocks ISA
- Communication style: friendly, reliable, highly knowledgeable, conversational but pragmatic
