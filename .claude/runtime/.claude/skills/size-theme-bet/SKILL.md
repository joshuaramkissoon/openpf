---
name: size-theme-bet
description: Use when the user wants to bet on a theme or asks how big a position should be ("how big should a nuclear bet be", "I have high conviction in X", "size this basket"). Sizes it with the quant tools against their real book and rails, then drafts a play — never sizes by mental arithmetic.
---
# Size a theme bet

Every number in the answer comes from a tool. You choose the names and the conviction; the tools choose the size.

## Steps
1. **Context.** Read `memory/preferences.md` and `memory/constraints.md` (caps, names to avoid, cash buffer). If conviction isn't stated, ask once or default to medium and say so.
2. **Candidates.** Pick 3–6 liquid Trading 212 names that express the theme (core + satellites). Check overlap with what they already own: `get_book_by_play` (or `analyse_risk(scope="holdings", tickers=[...], view="concentration")` if they hold some).
3. **Size.** `size_position(tickers=[...], method="conviction", conviction=<level>)` — the same rule `draft_play` uses (2/4/6% of book × 25%/basket-vol, capped at 10%). If the user gave a loss limit ("I can stomach losing £2k"), pass `max_loss_pct`. For a second opinion, `method="vol_target"` (default 1% of book volatility) or `method="kelly"` — quote Kelly only as "even half-Kelly says more than the cap, so the cap binds", never as the size.
4. **Split.** Default `weighting="inverse_vol"`; use `"risk_parity"` when legs have very different correlations, `"hrp"` for 6+ names.
5. **Check it lands well.** `what_if` with the new-money legs: book vol before → after, single-name cap, cash. If a rail breaches, resize — don't argue with the rail.
6. **Where.** If both accounts have cash, `placement` for the buys (ISA vs Invest).
7. **Draft.** `draft_play` with the legs, weights from step 4, and at least two machine-checkable kill rules. Only `stage_play_tranche` when the user asks.

## Output
- One line: "£X (Y% of book) — new money £Z after what you already hold", then why (conviction, basket vol, cap).
- A small table of legs: ticker · weight · £ · vol · share of risk.
- Downside in £: the 40% stress loss and the worst-5%-day figure from the tool.
- Tranche plan and any rail that forces staging.

## Rules
- Quote the tool's numbers; don't recompute or round differently.
- If `size_position` returns `ok: false`, say what data was missing and offer a shorter lookback or dropping the young listing — don't estimate.
- Kelly needs a return forecast we don't have; present it as a ceiling, not a target.
