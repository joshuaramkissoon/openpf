# Daily Alpha Loop — Correctness, Continuity & Position Management

**Date:** 2026-06-02
**Status:** Approved (design)
**Author:** Archie dev session

## Problem

The daily leveraged "alpha loop" (scheduled `claude_with_goal` passes: `alpha_loop_open`,
`alpha_loop_midday`, `alpha_loop_eod`) is well-architected on the analysis side but has
correctness and continuity defects that stop it doing its job — capture small, consistent
leveraged alpha each day, holding a position a couple of days when warranted.

Defects found during audit (2026-06-02):

1. **Wrong P&L scope.** The goal prompt tells the agent to "use your tools to determine TODAY's
   realized P&L." The agent reads this account-wide from T212, so a manual **core-equity** sale
   (e.g. L3Harris +£71.25 on 2026-06-02) trips the **leveraged** £50/day target and the loop
   stands down without taking any leveraged trade. The deterministic engine
   (`_daily_realized_pnl`) correctly counts leveraged-only — the two halves disagree.
2. **Continuity is effectively broken.** The prompt asks the agent to append a continuity line to
   `memory/leveraged/daily-goal.md`, but (a) the scheduled `ClaudeAgentOptions` sets no
   `permission_mode`, so headless `Write` silently defers ("pending approval"); (b) the file/dir
   has never been created; (c) `_build_goal_context` never reads prior state back in. Each run is
   effectively stateless about its own history.
3. **No record of the real leveraged book.** `leveraged_trades` is empty; the 6 standing ETPs live
   only in T212. The engine rediscovers them by live-reading T212 each session (blind when T212 is
   down) and cannot manage their stops/TP/age.
4. **Same-day force-close.** `allow_overnight=false` + 15:30 close means an engine-opened position
   can never be held multi-day — opposite of the stated intent.
5. **Silent failures.** The 07:49 T212-offline open run was recorded `last_status=ok`; the
   scheduler treats "agent returned text" as success regardless of content. No detection, no retry.

## Goal

The loop measures the right (leveraged-only) P&L, carries state across days, never fails silently,
can hold multi-day within a soft cap, and surfaces the real held positions in the UI so they can be
viewed and acted on.

## Non-goals (YAGNI)

No new order types; no partial-fill modeling beyond market sells; no auto-adoption of held
positions; no changes to the core-equity rebalancer; **no Telegram** (operator not connected).

---

## Workstream A — Loop correctness & continuity (backend)

### A1 — Leveraged-only P&L scope
`task_scheduler_service._build_goal_context` computes leveraged-only realized P&L for today via
`leveraged_service._daily_realized_pnl(db)` and injects it as a **hard number** with explicit
framing: *"LEVERAGED realized P&L today: £X — this is the figure the £{target} target measures.
Core-equity P&L does NOT count."* The "use your tools to determine today's realized P&L" sentence
is replaced so the agent no longer infers it account-wide.

### A2 — Continuity
- Set `permission_mode="acceptEdits"` on the scheduled `ClaudeAgentOptions` (trusted headless
  automation; existing secret-blocking PreToolUse hooks remain).
- **Service-side guarantee:** after each `claude_with_goal` run, the service deterministically
  appends one structured line to `.claude/runtime/memory/leveraged/daily-goal.md` (creating the
  dir/file if absent):
  `YYYY-MM-DD HH:MM | task | lev_realized=£X | trades_today=N | open_exposure=£Y | held=[...] | action`.
  Continuity no longer depends on the model remembering to write.
- `_build_goal_context` reads back the **last ~5 continuity lines** and a **current open-leveraged
  positions summary** (from `list_held_leveraged_positions`) and injects both, so each run opens
  already knowing what is held and what it recently did — and is not blind if T212 momentarily drops
  (falls back to the last known snapshot lines).

### A3 — Multi-day holds
- Add `max_hold_days: int = 3` to the leveraged policy schema/config; default `allow_overnight=true`.
- `leveraged_service._should_force_close_for_age(trade, policy, now_uk)` returns true when
  `allow_overnight` and the trade's `days_held >= max_hold_days`.
- The monitor (`run_leveraged_cycle` / EOD close) closes positions failing the age check. When
  `allow_overnight=false`, the existing same-day `_should_force_close_for_time` behaviour is kept.
- `max_hold_days` is editable in the Risk Rails UI.

### A4 — Blocked-run detection + auto-retry (no Telegram)
- The task runner inspects each outcome for "blocked" signals: parsed JSON `status` beginning with
  `blocked`, text markers (`MCP server is offline`, `cannot verify`, `trading212 ... offline`), or a
  `T212Error` from deterministic tasks.
- On detection: set `last_status="error"` (not `ok`), persist the reason in the task log, and surface
  it on the schedule timeline.
- **Auto-retry once:** set `next_run_at = now + ~8min` and increment `meta.retry_count` (max 1) so a
  transient T212/MCP outage self-heals without a duplicate-retry loop. Successful retry clears the
  counter.

---

## Workstream B — Held-position management (backend)

- `list_held_leveraged_positions(db)`: read T212 positions (ISA + Invest), classify each via
  `leveraged_registry.classify_leveraged()` on the instrument name, merge with engine-open
  `LeveragedTrade` rows (dedupe by `instrument_code`), tag each `tracked: bool`. Each row:
  `{instrument_code, symbol, name, underlying, direction, factor, account_kind, quantity,
  avg_price/entry_price, current_price, notional, unrealized_pnl_value, unrealized_pnl_pct,
  days_held|null, tracked, trade_id|null}`.
- `close_position(db, instrument_code, quantity=None, reason="manual")`: full or **partial** close.
  If it maps to an engine `LeveragedTrade` → existing `close_trade`; otherwise `close_external_position`
  (T212 market sell of `quantity` and record a closed `LeveragedTrade` row so A1's realized-P&L count
  includes it).
- `adopt_position(db, instrument_code, stop_loss_pct=None, take_profit_pct=None)`: **opt-in** — create a
  `LeveragedTrade(status="open", meta.source="adopted")` from the held position (entry = T212 avg price,
  `entered_at` = T212 first-fill if available else now), so the engine manages its stop/TP/age. Nothing
  is auto-adopted.
- Routes: `GET /api/leveraged/positions`, `POST /api/leveraged/positions/close`,
  `POST /api/leveraged/positions/adopt`. Snapshot response gains a `held_positions` field.

## Workstream C — Frontend (`LeveragedWorkspace.tsx`, shadcn)

A new **Positions** section (within/above the existing Open Trades table) listing live held
leveraged ETPs: name, underlying, direction badge, qty, entry, current, notional, unrealized P&L %,
days-held, and a `tracked`/`adopted` badge. Row actions:
- **Close** — dialog supporting full or partial quantity.
- **Adopt** — sets stop/TP from policy defaults (editable), brings the position under engine
  management.

New api client functions in `frontend/src/api/client.ts`: `getLeveragedPositions`,
`closeLeveragedPosition`, `adoptLeveragedPosition`, plus a `HeldPosition` type and `held_positions`
on the snapshot type.

---

## Data model changes

- `leveraged` policy/config: new `max_hold_days` (int, default 3); `allow_overnight` default → true.
- `ScheduledTask.meta`: `retry_count` (int) for A4.
- `LeveragedTrade.meta.source`: `"adopted"` | `"external"` in addition to existing `auto`/`manual`.
- No new tables; reuse `LeveragedTrade` for adopted/external records.

## Testing

- Backend `pytest` (run from `backend/`, repo-root `.venv`):
  - A1: goal context contains the leveraged-only figure and the "core-equity does NOT count" framing;
    a core-equity sale does not change the injected number.
  - A3: `_should_force_close_for_age` boundary (2 vs 3 days), and monitor closes an aged position.
  - A4: blocked output → `last_status=error`, `next_run_at` bumped, `retry_count` capped at 1.
  - B: classify+merge produces tracked/untracked rows; `close_position` partial vs full; `adopt_position`
    creates an open trade with policy stops.
- Frontend: `tsc` typecheck; manual smoke on an alt port (worktree has no node_modules — install or
  symlink as needed). Live :8000/:5173 stack untouched.

## Rollout

Delivered as a **PR, not merged**; reviewed before going live. Re-sync onto latest `origin/main`
before opening the PR (parallel sessions push to main). `auto_execute=false` remains, so even after
merge nothing auto-trades.
