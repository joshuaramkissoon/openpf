---
name: deploy-cash-plan
description: Use when the user asks what to do with idle or new cash ("deploy £5k", "I've got cash sitting there", "should I invest it all now or drip it in"). Allocates it with the quant tools, answers lump-sum vs staged with numbers, and picks the account.
---
# Deploy cash: where, and lump sum vs staged

The evidence: lump sum beats averaging in about two thirds of the time historically (Vanguard 2012/2023) because markets drift up; staging buys peace of mind and limits regret. Size the trade-off for *this* amount and *this* basket rather than reciting it.

## Steps
1. **Context.** `memory/preferences.md` / `goals.md`: cash buffer, names to avoid, staged-entry preference. `get_account_summary` for free cash per account.
2. **Where it should go,** in priority order:
   a. Active plays with a tranche due (`get_book_by_play`, `get_play`) — the plan exists.
   b. Otherwise `deploy_cash(amount_gbp=..., account=...)` — tops up holdings with room under the cap, inverse-vol weighted; pass `tickers=[...]` if the user named ideas.
3. **Lump vs staged.** Read `lump_vs_staged` and `recommendation` from `deploy_cash`: probability lump sum ends ahead, the expected cost of waiting, and the £ hit if the market fell 20% right after. Staging is recommended when the amount is ≥10% of the book or the basket is very volatile.
4. **Account.** If the money could go either way, `placement` for the buys.
5. **Check.** `what_if` with the first tranche (or the lump) for rails and post-trade weights.
6. **Propose** the first tranche (or the lump) with `propose_trade` — at most three trades; tell the user when the next tranches are due.

## Output
- Headline: "£X into A, B, C — lump sum ends ahead N% of the time, but staging over M months caps a bad-timing loss at £Y."
- Allocation table: ticker · £ · account · why.
- The schedule if staged.

## Rules
- Respect the stated cash buffer; never deploy into a name already over the cap.
- Earnings within 5 days for a candidate → stage it or skip it and say why.
- Numbers come from the tools.
