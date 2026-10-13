---
name: macro-shock-response
description: Use when markets move sharply or a macro event hits ("markets are tanking", "rates spiked", "what does this tariff news mean for me", "should I do something?"). Measures the book's actual exposure with the quant tools before suggesting anything, and defaults to calm, small, reversible moves.
---
# React to a macro shock

The worst reactions are big and fast. Measure what the shock does to *this* book, check the plays' kill rules, and only then consider small, reversible changes.

## Steps
1. **What happened.** `get_macro_snapshot` and `get_market_news` (search for the intel tools if they aren't loaded). One line on the move (index, rates, FX, VIX).
2. **Exposure.** `analyse_risk(scope="book", view="beta")` for market beta and the tech / small-cap / rates tilts; `view="cvar"` for the worst-5%-day loss in £; `view="drawdown"` for where the book sits vs its peak. Map the shock to the tilts: a rates spike hits a high-tech-tilt book; a dollar fall cuts GBP returns on US holdings.
3. **Plays.** `get_book_by_play` — any kill rule near or tripped? A tripped rule is the plan working; say which.
4. **Options**, smallest first:
   - Do nothing (often right; say why with the numbers).
   - Pause pending tranches (`update_play`) rather than selling.
   - Trim the highest risk-contribution name back inside its band — `rebalance_plan(trade_to="edge", turnover_budget=0.05)`.
   - Hedge only if the user already uses inverse ETPs (ISA-only) — size with `size_position(method="budget")` and `what_if`.
   Run `what_if` on any option you put forward.
5. **Propose** only what the user agrees to.

## Output
- Headline: the shock's estimated effect on the book in £ and %, from the tool's beta/tail numbers.
- What's exposed (top 3 by risk share), what's protected.
- Options with before → after numbers; your recommendation.

## Rules
- No predictions dressed up as facts; give ranges and what would change your view.
- Never suggest loosening rails; never chase the move with large trades.
