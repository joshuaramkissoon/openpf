---
name: weekly-review
description: Sunday review of the week — performance, what moved and why, how plays and proposals did, and two or three things to do next week.
---
A calm, honest look back at the week and a short plan for the next. Data first, narrative second.

## Steps
1. Read `memory/preferences.md`, `memory/goals.md` and this month's `memory/decisions/` file.
2. Snapshot: `get_account_summary` and `get_positions` for both accounts — total value, cash, top holdings and weights.
3. Week's movers: `get_price_snapshot` / `compare_assets` for the top ~10 holdings plus SPY and QQQ as benchmarks. Note the biggest contributors and detractors.
4. Activity: `get_order_history` (last 7 days) and `list_proposals` — what was proposed, approved, rejected or left hanging.
5. Plays: `list_plays`, then `get_play` for active ones — kill-rule status, drift, next tranche.
6. Risk: `get_risk_metrics` on the largest positions; flag concentration over ~20% of the book.

## Output
- **Headline** — the week in one line (value change and the main reason).
- **Scoreboard** — a small table: book vs SPY/QQQ, best and worst three holdings.
- **What happened** — 3–4 bullets on the moves that mattered and why.
- **Decisions** — proposals made/approved/rejected; anything still waiting for the user.
- **Plays** — one line each: on track / near a kill rule / due a tranche.
- **Next week** — 2–3 concrete, actionable takeaways. If a rail or setting looks wrong, recommend the change in words for the user to make in Settings.

## Rules
- Never claim a trade happened unless it's in the order history.
- Keep it to one screen. Tables beat paragraphs.
