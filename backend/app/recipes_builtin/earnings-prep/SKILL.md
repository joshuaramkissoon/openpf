---
name: earnings-prep
description: When a holding reports this week, prepares a short brief per name — expectations, what to watch, the usual size of the move and your exposure — so the print isn't a surprise.
---
Earnings are scheduled volatility. Prepare for each print rather than react to it.

## Steps
1. The trigger context lists holdings reporting in the next 7 days. If it's missing, find them: `get_positions`, then `get_earnings` for the top 15 holdings.
2. For each reporting holding:
   - Date and timing (before open / after close).
   - Exposure: value, % of the book, which account.
   - Expectations and history: `get_earnings_calendar` (past surprises), `get_fundamentals`, `get_valuation`.
   - Typical reaction: from `get_price_history_rows`, estimate the average absolute move on recent report days.
   - What to watch: 2–3 specific metrics or guidance items, from `get_company_news` (since_days=14).
   - Setup: trend into the print (`get_technical_snapshot`).
3. Decide per name: **Hold through**, **Consider trimming before** (position large relative to the usual move), or **Opportunity** (a play or watchlist target wants a post-print entry).

## Output
- **Headline** — e.g. "This week: NVDA (Wed, after close) and SHEL (Thu, pre-open) — 18% of your book."
- One compact block per name: date · exposure · usual move (±x%) · what to watch · verdict.
- If a trim is warranted you may `propose_trade` it with a rationale; otherwise just advise.

## Rules
- Don't predict the result. Frame the risk and what would matter.
- A few lines per name.
