---
name: play-check-in
description: Checks each active play against its kill rules and thesis, reports what moved and why, and tells you whether the next tranche is due.
---
Plays are theme bets with written kill rules. Hold them to their own rules so conviction doesn't drift into hope.

## Steps
1. `list_plays` (status=active). If there are none, say "No active plays" and stop.
2. For each: `get_play` with performance — legs, P&L, kill-rule status (ok / near / tripped), drift, staged tranches.
3. For each leg that moved more than ~5% since the last check, `get_company_news` (since_days=7) to find out why.
4. Re-read the play's "what would change my mind" line against this week's news. Has any of it happened?
5. Decide per play: **On track**, **Watch** (a kill rule is near or the thesis is wobbling), **Kill rule tripped**, or **Tranche due** (the next tranche's trigger is met).

## Output
- **Headline** — e.g. "3 plays: 2 on track, Nuclear near its drawdown rule."
- One block per play: verdict · P&L since start · what moved and why (a line per mover) · kill rules (only near/tripped ones) · next tranche status.
- If a tranche is due, say which and suggest staging it from the play page. Only call `stage_play_tranche` if `memory/preferences.md` says the user wants due tranches staged as proposals.

## Rules
- A tripped kill rule goes first and is stated plainly — don't argue the user out of their own rule.
- Proposals only; nothing happens without the user.
