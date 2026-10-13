---
name: deploy-idle-cash
description: Spots cash sitting idle in either account and proposes how to put it to work — next play tranches first, then underweight holdings — sized within your rails, for you to approve.
---
Cash drag is a quiet cost. Check for idle cash and, if there's enough to matter, propose a sensible way to deploy it. The user approves every trade.

## Steps
1. Read `memory/preferences.md`, `memory/goals.md` and `memory/constraints.md` — note any cash buffer the user wants to keep, names to avoid, and their style.
2. `get_account_summary` for Invest and Stocks ISA. Idle cash = free cash minus any stated buffer (default: keep 2% of the account or £250, whichever is larger).
3. If idle cash is under £200 in both accounts, stop and report "Nothing to deploy" in one line.
4. Candidates, in priority order:
   a. **Active plays with a tranche due** (`list_plays`, `get_play`) — the plan already exists.
   b. **Underweight core holdings** versus the user's targets or their own recent weights (`get_positions`).
   c. **Watchlist names whose setup has triggered** (`list_watchlist`, `get_price_snapshot`, `get_technical_snapshot`).
5. Check each candidate's price, trend and any earnings in the next 5 days (`get_earnings`) — don't add size right before a print unless the user prefers to.
6. Size with the quant tools, not by hand: `deploy_cash` (allocation, lump sum vs staged) for underweights, `what_if` to check the trades against the rails, `placement` if either account could take it.
7. Propose at most **three** trades with `propose_trade`, each with an explicit account (prefer the ISA for long-term holdings while allowance remains), a size in GBP, and a rationale that says what would invalidate it. If `propose_trade` reports a rail violation, resize within the cap.

## Output
- **Summary headline** (goes in the closing `summary` block, not at the top) — e.g. "£1,840 idle in the ISA — 2 proposals waiting for you."
- Table: account · idle cash · deployed by proposals · left after.
- Each proposal: ticker, account, size, why now, invalidation.
- Anything you deliberately skipped and why (earnings, concentration, preference).

## Rules
- Proposals only; the user approves each one in the app.
- Never push any single name above ~20% of the whole book.
- If the market is in a sharp drawdown (VIX above 30 in `get_macro_snapshot`), propose smaller, staged sizes and say why.
- Follow the app's report format: open with `## Key numbers`, end with the `summary` block (signal `none` on a quiet day).
- In the `summary` block, add `stats`: idle cash (money, with a `bar` of cash vs account value), the amount proposed (money), the proposals count (count). Exact numbers only; leave a tile out rather than guess.
