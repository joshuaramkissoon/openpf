# Watchlist — an *active* tracked-ideas board

**Date:** 2026-06-02
**Status:** Approved (full design, daily LLM review)

## Problem

The idea funnel in MyPF is: passing interest → ⟨gap⟩ → Thesis (conviction writeup) →
Execution Queue (orders). The "watchlist" today is just `{symbols: [...]}` in app-config,
edited as a comma-separated textarea buried in Settings and fed into agent runs. It holds
tickers but no *why*. So when Archie recommends something in chat (e.g. "I like KEYS here"),
there is nowhere at the right altitude to park that rec with its rationale: the Execution
Queue is too concrete (a real order) and a Thesis is too heavyweight (catalysts/invalidation
/confidence).

Two requirements from Josh:
1. A watchlist both Josh and Archie can CRUD, holding the *reason* an item is on the list.
2. **Not a graveyard** — items must be actively monitored and resurfaced when something is
   worth noticing, reusing the news / fundamentals / market-data / Kronos we already have.

## Solution overview

A dedicated **Watchlist** board: each entry is a ticker + the reason it's there + light live
enrichment (price, sparkline, on-expand chart + Kronos cone). Entries are **first-class
watched entities** alongside holdings, so the existing Attention/alert rails resurface them
automatically. Archie CRUDs the board via a new stdio MCP server.

### Where it fits with existing systems

- `watch_service` already runs materiality-gated checks (`big_move`, `earnings`,
  `thesis_invalidation`, `concentration`) that raise **deduped `Alert`s into the Attention
  feed**, and runs **hourly via the existing `watch_cycle` scheduled task**. We extend its
  `_WATCHES` list with watchlist-scoped checks — no new cron for the deterministic layer.
- `intel_service` already provides `get_company_news`, `get_earnings`, `get_market_news`,
  `get_macro_snapshot`. Kronos (`/charts/forecast`) and marketdata exist.
- Archie's CRUD powers come from stdio MCP servers (`backend/mcp_servers/scheduler.py` is the
  template). We add `backend/mcp_servers/watchlist.py`.

## Data model — `WatchlistItem`

New SQLAlchemy table (mirrors `Thesis`/`Alert` modelling):

| field | type | notes |
|-------|------|-------|
| `id` | str (uuid) | pk |
| `symbol` | str | upper-cased, indexed; unique per active item |
| `name` | str | resolved instrument name (best-effort) |
| `note` | text | the *why* — also the natural-language watch condition |
| `source` | str | `manual` / `archie` / `agent_run` |
| `conviction` | str \| null | `low` / `medium` / `high` |
| `status` | str | `watching` / `acted` / `archived` (indexed) |
| `target_price` | float \| null | optional alert level |
| `target_direction` | str \| null | `above` / `below` |
| `monitor` | bool | default `true`; lets Josh keep an item without it pinging him |
| `last_reviewed_at` | datetime \| null | set by the LLM review |
| `created_at` / `updated_at` | datetime | |
| `meta` | JSON | extensibility |

`active_symbols(db)` returns upper-cased symbols of items with `status == "watching"` — this
is what the agent run + leveraged scan consume instead of the config list.

## Monitoring — two layers, high signal / no noise

### 1. Deterministic watches (always-on, free, hourly)

New functions appended to `watch_service._WATCHES`, scoped to `monitor=true`, `status=watching`
items. Category `watchlist`, deduped by stable key:

- **target hit** — price crosses `target_price` in `target_direction` (only if set).
- **big move** — ±5% / ±8% on the day (reuse existing thresholds).
- **earnings soon** — within 3 days (`get_earnings`).
- **material news** — a relevant headline since last cycle (`get_company_news`, deduped by
  article URL; capped to 1 per item per cycle to avoid spam).

These raise `Alert`s into Attention exactly like holding alerts, and run inside the existing
hourly `watch_cycle` — no new infrastructure.

### 2. Daily LLM "watchlist review" (reasoned)

A new seeded `ScheduledTask` (`watchlist_review`, weekday mornings, Sonnet). Archie reads each
active item's `note` — its stated reason — pulls fresh news/fundamentals/technicals/Kronos via
MCP, and judges whether that reason is *playing out* or *breaking*. When material, it flags via
a controlled `flag_watchlist_item` MCP tool (writes a `watchlist`-category `Alert` with a
`consider:` action, deduped) and may update the item's note/conviction and `last_reviewed_at`.
The prompt enforces materiality (no manufactured noise) and produces a short artifact.

### The board stays alive

Each row surfaces recent flags inline ("⚠ 2 new") and `last_reviewed_at`; items with fresh
open alerts sort to the top. The board resurfaces activity even before Attention is opened.

## Archie's CRUD — `watchlist` MCP server

`backend/mcp_servers/watchlist.py` (mirrors `scheduler.py`), backed by `watchlist_service.py`:

- `list_watchlist(status="watching")`
- `add_to_watchlist(symbol, note="", conviction="", target_price=None, target_direction="", source="archie")`
- `update_watchlist_item(item_id, ...)`
- `remove_from_watchlist(item_id)` (archive or hard-delete)
- `flag_watchlist_item(symbol, title, detail, consider="", severity="info")` — raise an
  Attention alert tied to the item (used by the review task; materiality-gated by prompt).

Registered alongside the other MCP servers in `claude_agent_runtime.py` and
`claude_sdk_config.py` (tool allow-list).

## REST API — `/watchlist` (for the UI)

`backend/app/api/watchlist.py`:

- `GET /watchlist` → items + per-item live enrichment (price, day %, sparkline points, open
  alert count, last_reviewed_at). Enrichment computed live; Kronos NOT included here.
- `POST /watchlist` → add `{symbol, note?, conviction?, target_price?, target_direction?}`
- `PATCH /watchlist/{id}` → edit any mutable field (note, conviction, status, target, monitor)
- `DELETE /watchlist/{id}` → archive/remove
- `GET /watchlist/{id}/forecast` → on-demand Kronos forecast for the expanded row (proxies the
  existing forecast pool; not auto-run for the whole list because it is worker-process /
  seconds-latency).

## Frontend

- New `SectionKey` `"watchlist"`, nav item (icon `Star`) in the **top group right after
  Attention**. Badge with open-flag count.
- `WatchlistBoard.tsx`: compact rows — symbol, name, price + day %, sparkline, source badge,
  conviction, note, inline flag count + last-reviewed. Expand → `StockChart` with Kronos
  forecast cone (`forecast` prop) + one-line p50/cone summary + "Ask Archie about this"
  deep-link to chat. Inline add (symbol + note), edit, archive/remove.
- API client methods + `WatchlistItem` type.

## Clean removal of the old watchlist (careful)

New table = single source of truth. Steps:
1. **Seed**: on first boot, copy the *current* config `watchlist.symbols` into the table
   (fallback to the previous defaults) so Josh's existing watchlist carries over.
2. **Repoint consumers**: `agent_service.run_agent` / `_watchlist_intents` and
   `run_claude_analyst_cycle(... watchlist ...)` read `watchlist_service.active_symbols(db)`.
3. **Delete dead plumbing**: `WATCHLIST_DEFAULT`, `ConfigStore.get_watchlist/set_watchlist`,
   the `watchlist` key in `assembled_public`, `WatchlistConfig` schema, the `watchlist` field
   on `AppConfigResponse`, `PUT /config/watchlist`, the Settings textarea + its handlers, and
   the watchlist row in `help-guide.tsx`. The Settings card becomes a one-line pointer to the
   new view. Update `frontend/src/types/index.ts` (`AppConfig.watchlist` removed).
4. `leveraged_universe.py` only mentions "watchlist" in a docstring — leave it.

## Testing

- Backend unit tests (`backend/tests/test_watchlist.py`): service CRUD + `active_symbols`,
  seed/migration, deterministic watchlist watches raising deduped alerts (target/big-move/
  earnings/news) with `intel`/price calls stubbed.
- Browser test via agent-browser: add an item, see enrichment, expand for Kronos, edit/remove,
  confirm Settings no longer shows the textarea and points to the view.

## Out of scope (v1)

- Live streaming prices (enrichment is request-time).
- Richer alert routing to Telegram beyond what watch_service already does.

## Sequencing

1. Model + migration/seed + `watchlist_service`.
2. REST API + frontend board (replaces old watchlist; clean removal).
3. `watchlist` MCP server + runtime registration.
4. Deterministic watches in `watch_service`.
5. `watchlist_review` seeded task + `flag_watchlist_item`.
6. Tests + browser verification + PR.
