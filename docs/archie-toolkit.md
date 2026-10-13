# Archie's quant toolkit and how he navigates his tools

Status: shipped on the `v2-integrate` line (October 2026). Code: `backend/app/quant/` (maths),
`backend/app/services/quant_service.py` (book, prices, rails), `backend/mcp_servers/quant.py`
(tools), `.claude/runtime/.claude/skills/*` (workflows), `backend/evals/archie_tools/` (evals).

## The problem

Archie gave sizing, risk and allocation advice by doing the arithmetic in his head: reading
prices, estimating volatility, multiplying by a conviction. That is slow, unrepeatable and
sometimes wrong, and it is invisible to the user. Josh's brief: "maths as tools, a well
thought out, sufficient tool kit", and a considered answer to "how does Archie navigate a
growing toolset".

The design in one line: **a small number of broad, typed tools that compute every number on
the user's real book, plus skills that say which tools to call in which order, plus tool
search so the rest of the toolset stops crowding the context.**

---

## 1. What the guidance says

### Tool design for LLM agents

Sources: Anthropic, *Writing effective tools for agents — with agents* (2025); *Introducing
advanced tool use* (tool search, programmatic tool calling, tool use examples, 2025); Claude
Code MCP docs (tool search, `alwaysLoad`, description limits); the MCP specification (tool
annotations, server instructions).

| Principle | What it means here |
|---|---|
| Few tools for high-impact workflows, not one per endpoint | Six quant tools, each answering a whole question ("how big?", "what if?"), not twenty formula wrappers. Chained steps (load book → fetch history → FX → covariance → rails) happen inside one call. |
| Namespacing | Server `quant` → `mcp__quant__size_position` etc.; verbs that read as the user's question. |
| Return high-signal, natural-language context | Every output leads with `summary`: 3–6 plain-English lines that already contain the numbers to quote ("Size: £2,481 (3.0% of book)…"). Structured fields follow for follow-ups. Tickers and £, not ids. |
| Token efficiency | Compact JSON (no indentation), top-N rows, rounded figures; one call returns several views (`view="overview"`). |
| Actionable errors | `{"ok": false, "error": "only 34 overlapping days… shortest history is NXE", "hint": "Try lookback='2y'…"}`, never a traceback; Archie is told not to estimate instead. |
| Descriptions as onboarding docs, with examples | Each docstring says when to use the tool, what each enum value means, and gives a call example (the "tool use examples" idea, inline). Kept under Claude Code's 2,048-char limit (tested). |
| Typed, unambiguous parameters | Enums (`Literal`) for `method`, `weighting`, `view`, `scope`, `target`, `lookback`, `tax_band`; a typed `TradeIn` model for trades. |
| Annotations | `readOnlyHint=True, destructiveHint=False` on every quant tool. |
| Tool search / deferred loading | Load only the tools needed on most turns; defer the rest behind `ToolSearch` (Anthropic reports large context savings and better tool-selection accuracy with big toolsets). Server `instructions` say when to search for a server. |
| Evaluate with realistic tasks and verifiable outcomes | `backend/evals/archie_tools/`: 11 real questions, expected tool calls, numeric checks on tool outputs *and* on whether the reply quotes them. |

### Quant literature encoded

| Topic | Encoded as | Default and why |
|---|---|---|
| Volatility targeting (Moreira & Muir 2017; Harvey et al. 2018) | `sizing.vol_target_weight`, `sizing.vol_scaled_weight` | Size by risk, not notional. Plays keep their calibrated rule: conviction % × (25% / basket vol), clamped 0.5–1.25×. |
| Inverse-vol / risk parity (Maillard, Roncalli & Teiletche 2010) | `sizing.inverse_vol_weights`, `sizing.risk_parity_weights` (ERC by cyclical coordinate descent; risk budgets supported) | Inverse-vol is the default basket split: equal to ERC when correlations are equal and robust to estimation error. |
| Fractional Kelly with caps (Kelly 1956; Thorp 2006; MacLean, Thorp & Ziemba 2010) | `sizing.kelly_weight` | Half Kelly, always capped; a negative edge sizes to 0. Without a user-supplied return, a conservative assumed Sharpe by conviction (0.15 / 0.3 / 0.45). Presented as a ceiling, never a target: for any realistic edge the cap binds. |
| Ledoit-Wolf shrinkage covariance (2003/2004) | `covariance.covariance` (constant-correlation target by default; identity target and sample also available) | Shrink whenever a matrix is inverted or optimised over (risk parity, HRP, risk contribution). For the realised vol of a fixed basket, sample statistics are right and are used. |
| Hierarchical risk parity / correlation clustering (López de Prado 2016; Mantegna 1999) | `clustering.hrp_weights`, `clustering.correlation_clusters` | Correlation distance √((1−ρ)/2); clusters cut at ρ ≥ 0.6 (average linkage) to show "these are one bet". |
| Marginal and component risk contribution (Litterman 1996; Qian 2006) | `risk.risk_contributions` (Euler; components sum exactly to portfolio vol) | Reported as share of risk vs share of weight, the most useful single concentration lens. |
| CVaR / expected shortfall (Acerbi & Tasche 2002) | `risk.expected_shortfall`, `risk.tail_contributions` (exact decomposition on the worst-k days) | Historical 95%, today's weights replayed; quoted in % and £. |
| Max-drawdown statistics | `risk.drawdown_stats` (max, dates, recovery, longest underwater, current, Calmar), `risk.ewma_volatility` (RiskMetrics λ = 0.94) | |
| Beta / factor exposure | `exposure.ols_exposure` | Market beta to SPY plus tilts: tech (QQQ−SPY), small-cap (IWM−SPY), rates (TLT), all in GBP. |
| Concentration | `exposure.concentration` (HHI, effective N = 1/HHI, top-k), `exposure.diversification_ratio` | |
| Rebalancing bands with a turnover budget (Masters 2003; Vanguard 2010/2022; Swedroe's 5/25) | `rebalance.rebalance_plan` | Band = min(5pp, 25% of target); trade to target or band edge; largest drifts first until the turnover budget runs out; buys funded by sells (plus cash if allowed). |
| DCA vs lump sum (Vanguard 2012 and 2023) | `deploy.compare_lump_vs_staged` (seeded Monte Carlo on the basket's own vol) | Reports P(lump sum ends ahead), expected cost of waiting and the £ hit from a 20% fall right after investing. Staging is recommended when the amount is ≥10% of the book or the basket vol is ≥35%. Test: with a historical-like premium P ≈ 2/3, matching Vanguard. |
| UK wrappers (ISA £20k; CGT exempt £3k; 18/24% on shares; dividend allowance £500 and 10.75/35.75/39.35% from April 2026) | `uk_tax` (tax year, headroom, CGT, tax-free sale size, Bed & ISA, placement) | The ISA goes to the highest expected tax drag (growth via vol as a proxy, plus dividends). Speculative names stay in Invest for loss relief. ISA-only ETPs go in the ISA. Withdrawals net off because T212's ISA is flexible. |

Robustness defaults: minimum history of 60 overlapping days (120 for 1y/2y windows) with a
named error otherwise; names without history are **dropped and listed**, rather than
shortening everyone's window; all returns are converted to GBP (`(1+r)(1+r_fx)−1`, using
GBPUSD=X/EURGBP=X history, GBX untouched); FX outages are flagged, not hidden.

---

## 2. The tool surface

`quant` MCP server, read-only, registered for CHAT, RESEARCH and SCHEDULED (not the
analyst cycle). It never proposes or places anything; its outputs feed `propose_trade` /
`draft_play`.

| Tool | Answers | Key inputs | Output highlights |
|---|---|---|---|
| `size_position` | "How big?" (one name or a basket) | `tickers`, `method` = conviction / vol_target / kelly / budget, `weighting` = inverse_vol / risk_parity / hrp / equal, `conviction`, `target_risk`, `expected_return`, `max_weight`, `max_loss_pct` | £ and % of book; new money after holdings; per-leg £, vol and risk share; basket vol; 40% stress loss; ES; tranches; rails check |
| `analyse_risk` | "How risky / concentrated am I?" | `scope` = book / play / holdings, `view` = overview / concentration / contribution / drawdown / cvar / beta / correlation_clusters | Effective N, cap and theme breaches, risk share vs weight, book vol, VaR/ES (£), drawdown, beta and tilts, clusters |
| `what_if` | "What if I trade X?" | `trades` (ticker, side, amount_gbp or fraction_of_holding, account) | Weights before → after, book vol and ES before → after, effective N, cash per account, realised gains, rails |
| `rebalance_plan` | "Rebalance / de-risk with little turnover" | `target` = caps / equal / inverse_vol / risk_parity / hrp / play_legs or explicit `targets`, bands, `trade_to`, `turnover_budget`, `use_cash` | Trades, turnover vs budget, deferred names, rails |
| `deploy_cash` | "Where does idle cash go, and now or staged?" | `amount_gbp`, `account`, `tickers`, `weighting`, `months` | Allocation within cap room, lump vs staged odds and £, recommendation and schedule, rails |
| `placement` | "ISA or Invest? Which lot do I sell?" | `trades`, `tax_band`, `isa_subscribed_gbp`, `gains_realised_gbp` | Account per buy with the reason, CGT per sell, allowance left, days to 5 April, Bed & ISA room |

Why these six: they are the decisions Archie is asked to make (size, assess, test, rebalance,
deploy, place), so each maps onto one user question. Lower-level maths stays in the library,
where the quant subagent's code can import it. `rails`, `load_book` and `gbp_returns` are
shared inside every call, so they don't need tools of their own.

**Overlap with `marketdata`.** `marketdata` stays the source for a *single instrument's*
facts: price, candles, technicals, and its own standalone risk metrics in local currency.
`quant` handles anything about *the user's book or a decision*. CLAUDE.md states this split,
and both server descriptions say it. No tool was merged away: the two overlap only in name
("risk"), not in inputs or answers.

**Plays use the library.** `plays_service.basket_volatility` now calls
`app.quant.returns` + `risk.annualized_volatility`, the conviction × vol factor calls
`sizing.vol_scaled_weight`, and the stress loss calls `sizing.stress_loss`. The results are
bit-for-bit identical to before (verified on random data), and `size_position(method=
"conviction")` gives the same size as `draft_play`, so chat and plays never disagree.
`book_service` only sums values and compares them to thresholds, so it had no sizing
arithmetic to move.

---

## 3. Navigating a growing toolset

Archie now has about 70 MCP tools across 13 servers, plus built-ins, skills and two
subagents. Several layers keep this manageable.

1. **Tool search (deferred loading).** `CLAUDE_TOOL_SEARCH` (default `true`) is passed to the
   CLI as `ENABLE_TOOL_SEARCH`. The bundled CLI (2.1.150, SDK 0.2.87) supports it, along
   with `alwaysLoad` on stdio servers and `_meta["anthropic/alwaysLoad"]` on tools; both
   were checked in the binary. Servers Archie uses on most turns load upfront:
   **quant, trading212, marketdata, proposals and plays** (`always_load=True` in the
   registry). The other servers (fundamentals, intel, forecast, watchlist, views, recipes,
   scheduler) are found with `ToolSearch`, which is added to `allowed_tools`. CLAUDE.md tells
   Archie to search before he concludes a capability is missing. Set the variable to `false`
   to load everything upfront, or `auto:N` for threshold mode.
2. **Per-context tool sets.** The registry already scopes servers by context. It can now
   also hide individual tools per context (`McpServerSpec.hidden`): they are left out of
   `allowed_tools`, and the server unregisters them, so the model never sees them. The first
   use is the scheduler's maintenance tools (`run_due_scheduled_tasks`,
   `seed_default_scheduled_tasks`), which are hidden from chat and scheduled runs.
3. **Workflows as skills.** These procedures say which tools to call, in what order, and
   what the output should look like:
   - `size-theme-bet`
   - `derisk-concentrated-book`
   - `deploy-cash-plan`
   - `macro-shock-response`
   - `isa-year-end`

   *Runtime skills or built-in recipes?* Recipes are triggered routines (a schedule or an
   event) with versioning and a delivery channel, and that machinery adds nothing to a
   procedure the user starts by asking a question. Skills load on demand from a precise
   `description`, which costs no context until a question matches, and the eval showed
   Archie picking `derisk-concentrated-book` unprompted. The two scheduled routines that
   overlap (`deploy-idle-cash`, `isa-allowance`) stay recipes and now call the quant tools.
4. **Routing rule in CLAUDE.md.** "Numbers come from tools, not from you": questions about
   the book or a decision go to quant, and single-instrument facts go to marketdata.
5. **A narrow quant subagent.** The existing `quant` subagent now has the quant tools and is
   told to start with them. Its description tells Archie *not* to delegate routine
   sizing/risk/what-if questions, because a direct call is faster and cheaper. It remains
   for bespoke work such as backtests and custom statistics.

Considered and not done:
- **Programmatic tool calling**, where the model writes code that orchestrates tools. The
  CLI doesn't expose it to MCP tools, and Bash is off for Archie by default.
- **Splitting `analyse_risk` into one tool per view.** Fewer tools with an enum tested
  better in Anthropic's guidance and kept the always-loaded set small.
- **A router model in front of Archie.** Tool search plus skills cover the same need without
  an extra hop.

---

## 4. Evals

`backend/evals/archie_tools/` contains `cases.py` (11 questions) and `run.py`. It is opt-in
and not collected by pytest. Each case runs a fresh chat session through the real Claude CLI
with the chat runtime's own options. The runner refuses to start unless `OPENPF_SANDBOX=1`
is set and the sandbox isolation checks pass.

Checks per case:
- the expected tool groups were called;
- there were no unrequested side effects (no proposals, drafts, views or watchlist edits);
- the quant calls succeeded;
- numeric sanity on the tool outputs, e.g. the size stays within the 10% play cap, NVDA's
  weight halves after selling half, and turnover stays within budget;
- the reply quotes the tool's number, i.e. a £ or % figure within tolerance of the tool
  output.

```
# with a sandbox backend running on $PORT and the same sandbox env vars:
python -m evals.archie_tools.run [--only id1,id2] [--model …] [--budget 1.5]
```

Results of the first pass are below in **Eval results**.

## Eval results

First pass, 13 Oct 2026: sandbox book (£82.7k, 20 names), chat model `claude-opus-4-8`, tool
search on. **11/11 cases passed, with 65/65 checks. Total cost $2.21, and each case took 19–81 s.**

| Case | Tools Archie called | Notes |
|---|---|---|
| deploy_5k | skill `deploy-cash-plan` → get_book_by_play → deploy_cash ×2 | Re-ran deploy_cash net of a £250 buffer from preferences |
| nuclear_high_conviction | size_position | £2,480 (3.0%), new money £1,594, quoted exactly |
| too_concentrated | skill `derisk-concentrated-book` → analyse_risk → what_if | Found MU and AMD at ~2× their weight in risk; tested halving them |
| sell_half_nvda | what_if | |
| risk_drivers | analyse_risk | |
| selloff_loss | analyse_risk ×3 (cvar, drawdown, beta) | |
| isa_or_invest | placement | |
| rebalance_low_turnover | analyse_risk → rebalance_plan ×2 → what_if | |
| kelly_pltr | size_position(kelly) → get_price_snapshot | Explained that the cap binds and advised against using Kelly |
| same_bet | analyse_risk + marketdata get_correlation_matrix | |
| rates_shock | skill `macro-shock-response` → **ToolSearch** → get_macro_snapshot → analyse_risk ×4 | Deferred loading worked: intel was found by search |

What the run showed:
- Skills fired unprompted on matching questions.
- Tool search found a deferred server when it was needed.
- No case had side effects.
- Every quant call succeeded.
- Replies quoted the tool numbers. The one lapse was in kelly_pltr, where Archie added a little
  arithmetic of his own on top ("nudge the return to 8% and Kelly collapses to ~11%"; "28 shares
  × $209"). A next step is a sensitivity field in the Kelly output, so that this number also
  comes from the tool.

---

## Deferred / next

- `analyse_risk` view `stress`: replay named historical windows (2020, 2022) on today's
  weights. Today, `drawdown` replays the lookback window and `beta` maps the shock.
- Liquidity (ADV-based days-to-exit) in `size_position` for small caps.
- Lot-level CGT (Section 104 pooling, the 30-day bed-and-breakfast rule) from order history.
  Today it uses the position-level average-cost gain, which pooling makes correct for a
  whole holding.
- Track the eval pass rate over time, and add cases as new tools land.
