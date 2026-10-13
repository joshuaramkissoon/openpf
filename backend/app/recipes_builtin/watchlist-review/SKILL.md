---
name: watchlist-review
description: Re-reads every watchlist note against fresh data and flags only the ideas that are playing out or breaking — so the watchlist never becomes a graveyard.
---
Each watchlist item's note is the *reason* it's being watched. That note is your watch condition.

## Steps
1. `list_watchlist` (status=watching). If it's empty, say so in one line and stop.
2. For each item gather fresh context: `get_company_news` (since_days=1), `get_earnings`, `get_price_snapshot`, `get_technical_snapshot`.
3. Judge each against its note: **PLAYING OUT** (setup triggered, catalyst hit, target approached), **BREAKING** (reason invalidated) or **UNCHANGED**.
4. Only when something is material relative to the note, `flag_watchlist_item` with a tight title, a detail grounded in the data you just pulled, an optional "consider:" action, and a severity (info/warning/critical).
5. If a name has clearly graduated to a real setup, you may `update_watchlist_item` (conviction, target price/direction, a dated note).

## Output
- **Summary headline** (goes in the closing `summary` block, not at the top) — e.g. "2 of 9 ideas moved: NVDA setup triggered, SHEL thesis breaking."
- One line per item: PLAYING OUT / BREAKING / UNCHANGED + a few words.
- What you flagged, and why.

## Rules
- Don't flag routine noise. If nothing is material, flag nothing and say so.
- A one-minute read.
- Follow the app's report format: open with `## Key numbers`, end with the `summary` block (signal `none` on a quiet day).
