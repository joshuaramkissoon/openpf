---
name: smart-money-watch
description: Weekly read of what insiders, the funds you follow and members of Congress disclosed — only what touches your book or is unusually strong, with the delay and evidence quality stated plainly. Leads to research, never trades to copy.
---
Disclosures are evidence, not instructions. Find the few that matter to this user's book, say how strong and how stale each one is, and stay quiet otherwise.

## Steps
1. Read `memory/preferences.md` and `memory/market_views.md`.
2. `get_disclosure_trends` with `window=30`, then with `window=90`. Keep detections that touch a holding, play or watchlist name (`touches`), plus any scored 75 or more.
3. For each kept detection on a ticker, `get_smart_money(ticker=...)` to check the evidence rows: who, how much, open-market or not, and how many days after the trade it was disclosed.
4. For the top two or three, `get_price_snapshot` to see whether the price has already moved since the trades. If the move has already happened, say so.
5. `get_positions` for both accounts to size what each detection touches (rough % of book).

## Output
- **Key numbers**: detections touching your book, the strongest score, the median Congress delay, and insider buy-vs-sell names over 30 days.
- **What stands out**: at most three items. For each, give what was disclosed, who and how much, the delay ("disclosed 38 days after the trade"), the strength (weak/moderate/strong with the score), and what it touches in the book.
- **Risks**: the honest caveats that apply. Small samples, stale 13F snapshots, and sales are weak evidence.
- **Worth researching**: one line per name on what you'd check next (earnings, valuation, the thesis). No trade proposals by default. Only if a detection directly contradicts a play's kill rule, say so and point to the play.

## Rules
- Never frame anything as "copy this trade" or "follow this politician". Disclosures are lagging and partial.
- Always state the delay for Congress and 13F items, and call insider sales low-information unless they are clustered and discretionary.
- Only propose a trade if the user has asked for that in `memory/preferences.md`. Otherwise describe and stop.
- A quiet week is a good answer: if nothing qualifies, say so in one line with `signal: none`.
- Follow the app's report format: open with `## Key numbers`, end with the `summary` block (signal `none` on a quiet week).
