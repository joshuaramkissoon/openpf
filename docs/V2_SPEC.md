# MyPF V2 Spec: Financial Second Brain (Build-Today)

## 1) Product Goal
Build a single operator system that:
- Aggregates all portfolio accounts (Trading 212 Invest + Trading 212 Stocks ISA now, crypto adapters later).
- Produces actionable, auditable investment intelligence with an LLM agent runtime.
- Executes broker actions safely through explicit risk policy and approval controls.
- Supports async operator workflow through Telegram + web dashboard.

This is not a passive tracker. It is an active portfolio operations system.

## 2) Hard Constraints (From Primary Sources)
- Trading 212 account support for API is Invest and Stocks ISA only.
- In official T212 agent-skill setup, Invest and ISA require separate key/secret pairs.
- T212 API order endpoints are non-idempotent in beta; app-level dedupe is required.
- T212 Labs repo includes MiFID II caution around algorithmic trading usage.
- Claude Agent SDK supports configurable tools and permission modes. We will use strict policy controls.

## 3) V2 Scope (Today)

### In Scope
1. Multi-account portfolio truth layer:
- Separate ingest for `invest` and `stocks_isa` credentials.
- Unified combined view plus per-account drilldown.
- Correct cash fields + value/weight math.

2. Claude-driven analyst runtime:
- Dedicated `agent-runtime` service for research + analysis + recommendations.
- Uses Claude Agent SDK as primary reasoning engine.
- Tooling sandbox with constrained FS access and bounded code execution.

3. Research + thesis pipeline:
- Web/news/X research ingestion tools.
- Thesis records with confidence, catalysts, invalidation, expected edge.
- Intent generation with risk checks and execution recommendations.

4. Operator control plane:
- Web dashboard for portfolio health, intents, thesis board, approvals.
- Telegram command + notification interface.

### Out of Scope (for today)
- Fully unattended live execution by default.
- Complex derivatives/options support.
- Full tax optimization engine.

## 4) Target Architecture

## 4.1 Services
1. `api-service` (FastAPI):
- Main REST API.
- Persisted store and domain logic.
- Schedules portfolio sync and agent runs.

2. `agent-runtime` (Python worker process):
- Claude Agent SDK runner.
- Invokes tools via policy gateway.
- Produces structured outputs (`research`, `thesis`, `intent`, `risk_check`, `ops_note`).

3. `execution-gateway` (within API service initially):
- Converts approved intents to broker API actions.
- Enforces dedupe + position/cash/risk limits.

4. `telegram-bridge` (in API scheduler loop):
- Sends notifications.
- Handles command/query interactions.

## 4.2 Data Flow
1. Sync cycle:
- Pull account summary + positions for each configured T212 account key.
- Normalize to `position_snapshot` + `cash_snapshot` tables.
- Compute aggregate NAV and cross-account exposures.

2. Agent cycle:
- Build context package (portfolio state + goals + watchlist + recent events).
- Agent runs research tools and writes structured recommendations.
- Persist theses and intents.

3. Execution cycle:
- Human approves (web/telegram) or policy allows auto-approve.
- Execution gateway validates risk + dedupe then submits broker order.
- Persist execution outcomes and audit logs.

## 5) Agent Runtime Design (Claude Agent SDK)

## 5.1 Runtime Mode
- Run agent in a dedicated process/container with:
  - CPU/memory limits.
  - timeout ceilings per run.
  - explicit tool allowlist.
  - filesystem mount limited to `/app/agent_workspace` and `/tmp`.

## 5.2 Tool Categories
1. Portfolio tools:
- `get_unified_portfolio_state`
- `get_account_breakdown`
- `list_open_intents`
- `submit_trade_intent`
- `approve_or_reject_intent`

2. Research tools:
- `web_search`
- `fetch_news`
- `fetch_x_posts`
- `fetch_filings` (phase extension)

3. Quant tools:
- `run_python_analysis` (sandboxed notebook-like execution on controlled inputs)
- `load_market_series` (cached historical prices)

4. Memory tools:
- `read_investment_goals`
- `write_thesis`
- `append_operator_journal`

## 5.3 Safety Policy
- Live execution requires explicit approval unless strict policy enabled.
- Tool calls logged with args/result hash.
- Policy blocks:
  - unknown symbols
  - order notional over thresholds
  - duplicate order window
  - unsupported account or currency mismatch

## 6) Data Model (V2)

Add/extend tables:
1. `broker_accounts`
- `id`, `provider`, `account_kind` (`invest`, `stocks_isa`), `enabled`, `env`, `label`

2. `broker_credentials` (encrypted at rest)
- `broker_account_id`, `api_key`, `api_secret`

3. `cash_snapshots`
- `broker_account_id`, `currency`, `available_to_trade`, `reserved`, `total_cash`, `fetched_at`

4. `position_snapshots`
- `broker_account_id`, `ticker`, `instrument_code`, `quantity`, `avg_price`, `market_price`, `market_value`, `ppl`, `fetched_at`

5. `portfolio_snapshots`
- precomputed unified metrics for dashboard speed

6. `investment_goals`
- target return, drawdown tolerance, sector caps, liquidity preferences

7. `theses`
- symbol/theme thesis, catalysts, invalidation conditions, horizon, confidence

8. `agent_runs` + `agent_events` + `trade_intents` + `execution_events`
- extend current tables with account targeting and thesis linkage

## 7) Unified Portfolio Math

Given all enabled accounts:
- `total_equity = sum(account.total_equity)`
- `total_available_cash = sum(account.available_to_trade)`
- `position_weight = position.market_value / total_equity`
- Cross-account symbol merge by canonical `instrument_code`.

If account summary fields are missing:
- derive from position sums + available cash.

## 8) External API Integrations

Required:
1. Trading 212 API (two keypairs if both accounts used).
2. Anthropic API key (Claude Agent SDK runtime).

Optional but recommended for stronger research:
1. Web search API (Tavily/SerpAPI/Brave Search).
2. News API (NewsAPI/Finnhub/MarketAux).
3. X API bearer token (recent search endpoint) if direct Twitter analysis is required.

## 9) Dashboard V2 UX

Views:
1. `Overview`:
- unified NAV, available cash, account split cards, exposures, risk radar.

2. `Accounts`:
- Invest vs ISA side-by-side balances and holdings.

3. `Thesis Board`:
- active theses with confidence, catalysts, invalidations.

4. `Intent Console`:
- proposed -> approved -> executed lifecycle with rationale and risk checks.

5. `Agent Ops`:
- run history, tool traces, policy decisions.

6. `Settings`:
- broker credentials, account toggles, Telegram, research providers, policy limits.

## 10) Telegram Operator Design

Commands:
- `/status`
- `/accounts`
- `/theses`
- `/intents`
- `/approve <id>`
- `/reject <id>`
- `/execute <id>`
- `/ask <question>`

Push notifications:
- high conviction theses/intents above threshold
- risk anomalies (concentration spike, cash drift, thesis invalidation)
- daily and weekly portfolio ops digest

## 11) Security + Ops

- Local SQLite for state; secrets in env or encrypted table.
- No full filesystem access for agent runtime.
- Domain allowlist for outbound research requests.
- Structured audit log for every order and agent action.
- Optional Docker compose profile for strict sandbox runtime.

## 12) Build Order (Today)

1. Correctness foundation:
- multi-account credential model + ingestion.
- fix cash and weight math end-to-end.

2. Runtime integration:
- Claude Agent SDK wrapper with strict tool registry.
- structured agent outputs persisted.

3. Research tools:
- web/news/X tool adapters with provider fallbacks.

4. UX completion:
- unified account view + thesis and intent surfaces.

5. Operator channel:
- Telegram command upgrades + alerts.

6. Hardening:
- fixtures/tests for payload parsing and unified metric math.

## 13) What Requires User Help

1. Credentials:
- Trading 212 Invest key/secret.
- Trading 212 Stocks ISA key/secret.
- Anthropic API key.
- (Optional) news/search/X provider keys.

2. Policy decisions:
- default live execution mode (manual approval strongly recommended).
- max daily risk and per-order limits.
- notification cadence and noise thresholds.

3. Infra choice:
- run everything directly on Mac mini or isolate `agent-runtime` in Docker.

## 14) Acceptance Criteria

1. Dashboard shows correct:
- unified NAV and cash across Invest + ISA,
- per-account breakdown,
- non-zero weights summing ~100%.

2. Agent run produces:
- at least one thesis + intent with explicit rationale and risk note.

3. Telegram flow:
- can query status and approve/reject/execute intents.

4. Auditability:
- every agent/tool/execution action stored and visible.
