---
name: earnings-prep
description: When a holding reports this week, prepares a short brief per name — expectations, what to watch, the usual size of the move and your exposure — so the print isn't a surprise.
---
Earnings are scheduled volatility. Prepare for each print rather than react to it.

## Steps
1. "Why it ran" lists holdings reporting in the next 7 days. If it's missing, find them: `get_positions`, then `get_earnings` for the top 15 holdings.
2. For each reporting holding:
   - Date and timing (before open / after close).
   - Exposure: value, % of the book, which account.
   - Expectations and history: `get_earnings_calendar` (past surprises), `get_fundamentals`, `get_valuation`.
   - Typical reaction: from `get_price_history_rows`, estimate the average absolute move on recent report days.
   - What to watch: 2–3 specific metrics or guidance items, from `get_company_news` (since_days=14).
   - Setup: trend into the print (`get_technical_snapshot`).
3. Decide per name: **Hold through**, **Consider trimming before** (position large relative to the usual move), or **Opportunity** (a play or watchlist target wants a post-print entry).

## Output
- **Summary headline** (goes in the closing `summary` block, not at the top) — e.g. "This week: NVDA (Wed, after close) and SHEL (Thu, pre-open) — 18% of your book."
- One `## TICKER` section per name with short bullets: `Exposure: …`, `Timing: …`, `Valuation: …`, `What to watch: …`, `Verdict: Hold through | Consider trimming | Opportunity` (the app turns these into holding cards).
- If a trim is warranted you may `propose_trade` it with a rationale; otherwise just advise.

## Rules
- Don't predict the result. Frame the risk and what would matter.
- A few lines per name.
- Follow the app's report format: open with `## Key numbers`, end with the `summary` block (signal `none` on a quiet day).
- In the `summary` block, add `stats`: names reporting (count, with `tickers`), exposure (money, sub '% of book', `bar`), next report (date, with a `timeline` of each date), gain on cost (signedPct); plus `holdings` with each name's date, timing, value, weight, gain_pct, forward_pe, verdict and 2-3 short `watch` items. (In digest mode the app computes these itself.) Exact numbers only; leave a tile out rather than guess.
