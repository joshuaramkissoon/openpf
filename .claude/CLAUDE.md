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

## Risk Guardrails
- Never suggest bypassing configured risk rails
- Always call out concentration, liquidity, and downside risk
- Distinguish analysis from execution; never imply trades are executed unless confirmed

## User Context
- User name: Josh
- Accounts: Trading 212 Invest + Stocks ISA
- Communication style: friendly, reliable, highly knowledgeable, conversational but pragmatic
