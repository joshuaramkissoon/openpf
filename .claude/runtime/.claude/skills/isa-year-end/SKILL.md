---
name: isa-year-end
description: Use for UK tax-wrapper questions — ISA allowance left, "which account should I buy this in", Bed & ISA, using the CGT allowance, or anything before 5 April. Gets the numbers from the quant placement tool; information, not tax advice.
---
# ISA year-end and account placement

ISA allowance (£20k) is use-it-or-lose-it; the CGT exemption (£3k) resets each 6 April. The ISA should hold what would cost the most tax outside it: high-growth and high-yield holdings. Losses are only useful in Invest.

## Steps
1. **Status.** `placement(trades=[])` (or with the trades being considered): allowance left, days to 5 April, ISA free cash, CGT exemption left, Bed & ISA room. If the allowance used is unknown, ask the user for their ISA deposits this tax year and pass `isa_subscribed_gbp`. Ask their tax band if not in `memory/user-profile.md` (default higher).
2. **New buys.** `placement` with the proposed buys → which account each goes in and why. ISA-only products (leveraged / inverse ETPs) always go in the ISA.
3. **Bed & ISA.** From `bed_and_isa`: the Invest holdings that can be sold within the CGT exemption and rebought in the ISA. Check the rebuy with `what_if` (it's a round trip — weights shouldn't change). The user moves the cash between accounts themselves.
4. **Sells.** For trims, `placement` with the sells — Invest losses first (harvest), gains within the exemption next, else sell the ISA lot.
5. **Act** only on request: `propose_trade` with explicit `account_kind` for each leg.

## Output
- Headline: "£X ISA allowance left · N days to 5 April" (+ CGT room).
- Table: action · ticker · account · £ · tax effect.
- What the user must do in the Trading 212 app (deposit / transfer) before any ISA purchase.

## Rules
- You can't move money; say exactly what the user needs to do.
- Rates are 2026/27 planning figures (CGT 18/24%, dividend allowance £500); not tax advice.
- Bed & ISA: sell and rebuy are separate trades on different days' prices — mention the gap risk.
