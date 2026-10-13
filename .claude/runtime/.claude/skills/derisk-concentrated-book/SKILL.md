---
name: derisk-concentrated-book
description: Use when the user asks "am I too concentrated", "how risky is my portfolio", "what's driving my risk", "should I trim X", or wants to reduce risk. Measures concentration and risk contribution with the quant tools, then plans the smallest set of trims that fixes it within a turnover budget.
---
# De-risk a concentrated book

Concentration is about *risk*, not just weight: a 9% position in a 60%-vol stock can drive more risk than a 15% position in a steady one. Measure first, trim second, with the fewest trades.

## Steps
1. **Measure.** `analyse_risk(scope="book", view="overview")`. Pull out: top weights vs the single-name cap, effective number of names, theme concentration, which holdings drive the most risk relative to their weight, the correlation clusters (names that are really one bet), worst-5%-day loss, and beta.
2. **Diagnose in one sentence.** E.g. "Half your risk is three semiconductor names that move together." Use the tool's figures.
3. **Plan.** Pick the least-opinionated fix:
   - Over the cap → `rebalance_plan(scope="book", target="caps")`.
   - Risk lopsided but no cap breach → `rebalance_plan(target="risk_parity", trade_to="edge", turnover_budget=0.1)` or name-specific `targets`.
   - One theme too big → `rebalance_plan(scope="play", play_id=..., target="play_legs")` or trim that play's largest leg.
   Respect `memory/preferences.md` (names to keep, a preferred turnover).
4. **Verify.** `what_if` with the planned trades: weights, book vol and tail loss before → after, rails. If the improvement is marginal (< ~1pt of book vol), say so — doing nothing is a valid answer.
5. **Tax.** If trims are in Invest, `placement` with the sells for CGT and whether to trim the ISA lot instead.
6. **Propose** only if the user wants to act: one `propose_trade` per trim, with `play_id` and the rationale from steps 2–4.

## Output
- Headline: the diagnosis with its key number.
- Table: holding · weight · share of risk (top 5).
- The plan: trades, turnover, before → after (vol, top weight, worst-5%-day).
- Tax note if relevant; what you'd watch instead if no trade is worth it.

## Rules
- Numbers come from the tools; don't do the arithmetic yourself.
- Prefer few, larger trims over many small ones; never trim below what a play's thesis needs without saying so.
