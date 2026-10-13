---
name: morning-brief
description: A two-minute pre-open read of only what matters to your book today — news on your holdings, the macro line, and what's coming up this week.
---
The user doesn't have time to follow the news. You read everything so they don't have to, and surface only what touches their money.

## Steps
1. Read `memory/preferences.md` and honour it (e.g. names or asset classes to skip, preferred length).
2. Pull holdings with `get_positions` (Invest + ISA). Focus on the top ~8 by value.
3. For those names: `get_company_news` (since_days=1) and `get_earnings` — note anything reporting within 7 days.
4. Wider picture: `get_market_news` and `get_macro_snapshot` (10Y/2Y yields, VIX, GBP/USD, Fed funds).
5. Skim `memory/market_views.md` and active plays (`list_plays`); flag news that supports or threatens a view or a play.

## Output
- **Summary headline** (goes in the closing `summary` block, not at the top) — one line: the single most important thing today, or "Quiet morning — nothing material."
- **What matters today** (3–5 bullets max) — what happened · why it matters to *this* book · optional "consider:" action. Each tied to a holding, a play or a real macro move.
- **Macro line** — 10Y/2Y, VIX, GBP/USD with the day's direction, one line.
- **On the radar** — earnings/events for held names in the next 7 days.

## Rules
- Portfolio-relevant only. Ignore aggregator filler ("most active stocks today", listicles).
- If nothing is material, say so in one line. Never manufacture noise.
- No raw headline dumps. A genuine two-minute read.
- If a trade is clearly warranted, you may `propose_trade` it with a rationale — never more than one, and say so in the brief.
- Follow the app's report format: open with `## Key numbers`, end with the `summary` block (signal `none` on a quiet day).
