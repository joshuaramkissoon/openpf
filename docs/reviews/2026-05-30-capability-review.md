# OpenPF Capability & Vision Review — 2026-05-30

Reviewer: background Opus agent. Anchored to the vision: a personal finance agent
with read/write T212 access that reviews markets/news/events, does quant analysis,
optimises the portfolio long-term, and captures consistent short-term alpha —
including **regime-aware leveraged long/short** selection driven by market moves.

## Verdict
Strong as an "advisor you ask." As the **autonomous, regime-aware long/short alpha
engine** the vision describes, it's ~half-built. Biggest lever: the leveraged engine
(`leveraged_service.py`) is a **fixed-watchlist, long-biased momentum scanner with no
market-regime input and no outcome learning**, and its daily P&L/loss/trade rails are
**not enforced in code**.

## Scorecard
| Capability | Rating |
|---|---|
| Live market/news/events ingestion | Partial (market data real; news/X key-gated → silently `[]`; no macro calendar) |
| Quant analysis depth | Strong (`app/quant`, 7 marketdata tools, Kronos, fundamentals, Opus quant subagent) |
| Long-term portfolio optimisation | Partial (has beta/HHI/corr; no optimiser; concentration caps only in prose) |
| Consistent short-term alpha | Partial (loop exists but prompt-only, disabled, stateless, no backtest/attribution) |
| Regime-aware leveraged long/short | Missing→Partial (fixed symbol list; regime computed in legacy code but never used; can short but tied to inverse-ETP's own chart, not the underlying) |
| Read/write execution safety | Strong-ish (intent→approve→execute, guards, ISA-only; but daily rails unenforced, auto-execute ungated) |
| Memory / learning over time | Partial (good static memory; manual; partly stale; no outcome→signal feedback) |

## Prioritized roadmap
1. **[S] Fix silent synthetic-data fallback** — `market_data.fetch_history` returns a random-walk series when yfinance is empty/throttled, silently feeding Kronos forecasts, beta/correlation, the backtest, and paper fills. `leveraged_market._download_history_frame` correctly *raises* — the two paths disagree. Flag (`synthetic: true`) or refuse. **Highest correctness risk.**
2. **[S] Enforce daily rails in code** — `daily_profit_target_gbp`/`daily_loss_limit_gbp`/`max_daily_trades` are schema + prose only; `scan_signals`/`execute_signal` ignore them and `_normalize_policy` silently drops the three fields. Enforce realized-P&L/loss/trade-count hard stops (template: `execution_service._daily_executed_notional`); add the fields to `_normalize_policy`.
3. **[M] Market-driven universe + regime gate** — add `build_universe(db)` ranking underlyings' moves (compare_assets/risk_metrics) → map top movers to the correct long/short ETP via `memory/instruments/leveraged-products.md`; promote `agent_service._market_regime` into a shared `regime_service` (add VIX + SMA50/200 + breadth) and gate long vs short in `_build_signal` + inject regime into `daily_alpha_goal`. **Core vision gap.**
4. **[S] Enforce concentration limits** from live weights via `analytics` (PLTR 29%/NVDA 25% caps live only in `constraints.md`).
5. **[M] Signal-attribution loop** — join closed `LeveragedTrade` rows back to originating `LeveragedSignal` to report predicted vs realized edge; make `expected_edge`/confidence data-driven (currently hand-tuned constants).
6. **[M] Real news + macro events** — lean on the SDK `WebSearch` tool (already granted) instead of the empty `research_service` stubs; add an FOMC/CPI/NFP calendar.
7. **[S] Refresh stale memory** — `market_views.md` expired ~10 weeks ago (review-by 2026-03-16); `goals.md`/`context.md`/`feature-backlog.md` dated/shipped-but-"Proposed"; Archie's CLAUDE.md tooling list omits fundamentals + forecast.
8. **[L] Portfolio optimiser/rebalancer** (vol-target or risk-parity over core holdings).

## Agent-instruction & memory audit (highlights)
- **Best in repo:** `run_research_request` prompt, `_DEFAULT_TASKS.weekly_review`, `memory/lessons.md`, `memory/preferences.md`, `memory/instruments/leveraged-products.md` — keep.
- **Low signal / fix:**
  - `run_claude_analyst_cycle` system prompt — bland, names no tools; rewrite to require a regime read + name marketdata/fundamentals/forecast/subagents.
  - `_RESEARCHER_PROMPT` / `_QUANT_PROMPT` — don't mention the fundamentals/forecast/risk tools now granted, or data-quality caution; add tool inventory + "flag synthetic-looking data."
  - `lev_morning_scan` — deterministic `leveraged_cycle` ignores its prompt text (the `lessons.md` 2026-02-18 complaint resurfacing); convert to `claude_with_goal` or render a markdown summary for `leveraged_*` kinds.
  - `market_views.md` **expired** — purge/refresh (would mislead if read as current).
  - `goals.md` Feb tranche block, `context.md` Feb weights, `feature-backlog.md` (mostly shipped) — refresh.
  - Archie `runtime/.claude/CLAUDE.md` tooling list — add fundamentals + forecast.

## Risks / red flags
- **Fabricated data presented as real** (synthetic fallback → Kronos/backtest/paper-fill).
- **Unenforced "hard" limits** + ungated auto-execute (memory shows a short-Netflix held to **−45.5%** that "should have been cut at −5%").
- **3x ETP decay** on multi-day holds (`allow_overnight`) not modelled.
- **Currency/units:** `leveraged_market.get_price` assumes USD even for GBX LSE ETPs (e.g. `3PLT.L`) → possible P&L unit mismatch in the ISA.
- **Heuristic edge:** `_build_signal` confidence/`expected_edge` are arbitrary constants; MA backtest ignores fees/spreads (0.3–0.5%/round trip).
- **Over-trading:** daily loop + open-slot filling with no enforced trade cap.
