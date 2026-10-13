---
name: macro-on-my-book
description: Reads today's macro and market news and translates it into what it means for your holdings — rates, currency, sector and factor exposure — and stays quiet when nothing matters.
---
Macro headlines are only useful once mapped onto the user's actual exposures. Do that mapping; skip the commentary.

## Steps
1. Read `memory/preferences.md` and `memory/market_views.md`.
2. `get_macro_snapshot` and `get_market_news`. Identify at most three genuinely market-moving developments (central banks and rates, inflation prints, FX, energy, geopolitics, regulation).
3. `get_positions` for both accounts. Work out the exposures that matter: sector mix, USD vs GBP assets, rate-sensitive names (long-duration growth, REITs, banks), commodity-linked names.
4. For each development, decide which holdings it actually touches and in which direction. Use `get_price_snapshot` to see whether the market is already reacting.

## Output
- **Summary headline** (goes in the closing `summary` block, not at the top) — "Macro: nothing material for your book today", or the one development that matters.
- For each material development: what happened → which holdings are exposed (rough % of book) → direction → "consider:" (hold / watch a level / trim / hedge idea).
- **Currency line** — how GBP/USD moved and what that did to US holdings in GBP terms.

## Rules
- Silence is a valid output. Don't stretch a weak link between a headline and a holding.
- If a hedge or trim is clearly warranted, describe it; use `propose_trade` only when the case is strong, and never more than once.
- Never present a macro view as certain.
- Follow the app's report format: open with `## Key numbers`, end with the `summary` block (signal `none` on a quiet day).
- In the `summary` block, add `stats`: only numbers that moved your book — e.g. the rate or index move (signedPct) and your exposed value (money, sub '% of book'). None on a quiet day. Exact numbers only; leave a tile out rather than guess.
