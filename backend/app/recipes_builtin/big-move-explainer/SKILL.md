---
name: big-move-explainer
description: When a holding moves sharply in a day, finds out why, judges whether the story changed, and suggests whether to hold, add or trim.
---
A big move is a question: noise, news, or a changed story? Answer it quickly and calmly.

## Steps
1. The trigger context names the holding(s) and the move. For each:
2. Confirm the move with `get_price_snapshot` and check `get_technical_snapshot` (is it breaking a key level?).
3. Find the cause: `get_company_news` (since_days=2), then `get_market_news` — company-specific, sector-wide or market-wide? If unclear, a quick web search.
4. Exposure (`get_positions`): value, % of book, account, gain or loss on the position.
5. The user's own reasons: a watchlist note, a play leg (`list_plays`) or `memory/market_views.md`. Did this move break any of them?

## Output
- **Summary headline** (goes in the closing `summary` block, not at the top) — e.g. "ASML −7.4%: guidance cut, not market noise."
- Per name: cause (one line) · company-specific or market-wide · thesis intact or changed · exposure · suggestion (hold / add / trim) and the level that would change your mind.
- Only `propose_trade` if the thesis clearly changed or a play's kill rule tripped; otherwise advise.

## Rules
- Don't chase. A big up-move alone is not a reason to add.
- Be explicit about uncertainty when the cause isn't clear.
- Follow the app's report format: open with `## Key numbers`, end with the `summary` block (signal `none` on a quiet day).
