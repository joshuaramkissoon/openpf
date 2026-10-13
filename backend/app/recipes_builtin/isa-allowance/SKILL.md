---
name: isa-allowance
description: Tracks how much of this tax year's £20,000 ISA allowance you've used and, as 5 April approaches, nudges you to top up and proposes what to buy with it.
---
The UK ISA allowance is £20,000 per tax year (6 April – 5 April) and unused allowance is lost. Make sure that never happens by accident.

## Steps
1. Work out where we are in the tax year: days until the next 5 April. If more than 75 days remain and this isn't the first fortnight of a new tax year, keep the whole output to a single status line.
2. Estimate contributions to the Stocks ISA since the last 6 April using `get_transaction_history` (deposits and transfers in). If the data is incomplete, say so and give a best estimate with the caveat — never invent a number.
3. Remaining allowance = £20,000 − contributions. Check `get_account_summary` for the ISA's and the Invest account's free cash.
4. If allowance remains and the deadline is within 75 days:
   - Suggest moving cash into the ISA (the user makes the transfer — you can't move money).
   - If the ISA already holds idle cash, propose up to two purchases with `propose_trade` (account_kind=stocks_isa), preferring existing core holdings or an active play's next tranche.
   - Point out Invest-account holdings that could be sold and rebought inside the ISA ("Bed & ISA"), noting this crystallises any gain; mention the annual capital gains exemption without giving tax advice.
5. In the first fortnight of a new tax year, remind the user the allowance has reset to £20,000.

## Output
- **Summary headline** (goes in the closing `summary` block, not at the top) — e.g. "£6,400 of ISA allowance left · 38 days to 5 April."
- Contributions this tax year (and how they were estimated), remaining allowance, days left.
- Suggested actions, most valuable first.

## Rules
- Information, not tax advice; flag uncertainty clearly.
- You can't move money between accounts — tell the user exactly what to do.
- Follow the app's report format: open with `## Key numbers`, end with the `summary` block (signal `none` on a quiet day).
