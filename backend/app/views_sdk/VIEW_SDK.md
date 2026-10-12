# OpenPF View SDK

A **view** is one self-contained HTML fragment (markup + inline `<style>` + inline
`<script>`) rendered inside a locked-down sandbox in the OpenPF app. It reads the
user's portfolio through `openpf.data()` and draws with the built-in kit. It has
**no network** (no fetch, no external URLs, no CDNs, no imports), **no browser
storage**, and **cannot trade**. Everything must be inline.

## Document shape

```html
<!-- openpf-view {"title": "Dividend income", "description": "Monthly dividends received and who pays them", "icon": "coins"} -->
<div class="op-stack">
  <div class="op-grid op-grid-3" id="stats"></div>
  <section class="op-card">
    <div class="op-card-header"><div><div class="op-card-title">By month</div><div class="op-card-desc">Last 12 months</div></div></div>
    <div id="chart"></div>
  </section>
</div>
<style>/* optional, small, use theme vars only */</style>
<script>
openpf.ready(async () => {
  const p = await openpf.data('portfolio')
  // build DOM…
})
</script>
```

- Line 1 MUST be the `openpf-view` header comment (JSON: title ≤ 40 chars, description ≤ 120 chars, icon = one of: `coins`, `target`, `pie-chart`, `chart-line`, `chart-column`, `chart-area`, `scale`, `radar`, `calendar`, `wallet`, `layers`, `flame`, `shield`, `gauge`, `trending-up`, `trending-down`, `activity`, `banknote`, `percent`, `globe`, `landmark`, `briefcase`, `bell`, `eye`, `list`, `table`, `layout-grid`, `sparkles`, `zap`, `clock`, `piggy-bank`, `receipt`, `crosshair`, `compass`).
- No `<html>/<head>/<body>`. The fragment is placed in the body; the page header (title, description) is rendered by the app — do not repeat the title inside the view.
- Put all code in `openpf.ready(async () => { … })`. Thrown errors show a friendly error panel with a "Fix it" button.
- Show skeletons while loading (`<div class="op-skeleton" style="height:240px"></div>`), then replace. Handle empty data with an `.op-empty` message (one calm sentence saying what will appear and when).
- Size limit 200KB. Forbidden (rejected on save): `fetch`, `XMLHttpRequest`, `WebSocket`, `import`, `eval`, `new Function`, `<iframe>`, `<form>`, `<link>`, `<script src>`, any `http(s)://` URL, `localStorage`, `window.parent/top`, `window.open`, CSS `url()` other than `data:`.

## Design rules (it must look native)

Calm, precise, premium. Dark theme. Use the kit classes and theme variables only — never raw hex colours, never gradients, shadows or emoji. Gains `op-positive`, losses `op-negative`. Every number is mono (`op-num`) and formatted with `openpf.money/pct/num`. Section titles are short and sentence-case. Prefer one clear hero stat row + 1–3 cards; no walls of cards. Charts go inside `.op-card`. Explain non-obvious metrics in one short `.op-card-desc` line.

## Runtime API — `window.openpf`

| API | Notes |
| --- | --- |
| `openpf.ready(fn)` | Runs `fn` (may be async) once the DOM is ready; catches errors. |
| `await openpf.data(source, params?)` | Read-only data. Sources below. Rejects with an Error on bad params. |
| `openpf.state.get(key, fallback)` | Per-view saved user input (sync). |
| `await openpf.state.set(key, value)` | Persist JSON-serialisable value (≤64KB total). |
| `openpf.ask(prompt)` | Opens Archie (the AI assistant) with the prompt pre-filled; the user presses send. Use for "Ask Archie to rebalance"-style buttons. |
| `openpf.openTicker(ticker)` | Opens the instrument sheet (price chart, position). |
| `openpf.navigate(path)` | App routes: `/home`, `/archie`, `/plays`, `/activity`, `/views/<id>`. |
| `openpf.money(v, {compact, decimals, signed, currency})` | Display currency; honours privacy mode. ALWAYS use this for money. |
| `openpf.pct(fraction, {decimals, signed})` | `0.0523` → `5.23%`. `openpf.signedPct(x)` adds `+`. |
| `openpf.num(v, {decimals, compact})`, `openpf.date(v, 'short'|'long'|'month'|'monthShort'|'relative')` | |
| `openpf.h(tag, attrs, ...children)` | Tiny DOM builder. attrs: `class`, `style` (object), `onclick` etc., `html` (raw). Text children are escaped. |
| `openpf.esc(s)` | HTML-escape for template strings. Always escape names/notes you put in `innerHTML`. |
| `openpf.tone(v)` | `'op-positive' | 'op-negative' | 'op-muted'` by sign. |
| `openpf.currency`, `openpf.privacy` | `'GBP'|'USD'`, `'off'|'blur'|'scramble'`. |
| `openpf.colors` | 8 chart colours (`var(--chart-1)`…) for categorical series. |

### Charts — `openpf.charts.*(target, options)`

`target` is an element or selector; charts fill its width and redraw on resize. `format` is `'money' | 'moneyCompact' | 'pct' | 'signedPct' | 'num'` or a function `(v, datum) => string`. All have hover tooltips.

- `line(t, {series: [{name, points: [{x, y}], color?, dashed?}], format, height=240, zero?})` — `x` is a date string (`'2026-03-01'`, `'2026-03'`), Date or number. Single series shorthand: `{data: [{x, y}]}`.
- `area(t, …same as line)` — filled, good for portfolio value.
- `bar(t, {data: [{label, value, short?, color?, note?, muted?}], format, height=240, onClick?})` — vertical; negatives drawn below zero in red. `horizontal: true` gives a ranked list with bars (best for top-N by weight/amount; no height needed).
- `stackedBar(t, {labels: [...], series: [{name, values: [...], color?}], format, height})`
- `donut(t, {data: [{label, value, color?}], format, size=180, centerValue?, centerLabel?, legend=true})` — legend shows value + share.
- `sparkline(t, {values: [...], height=32, color?})` — colour by direction.
- `heatmap(t, {rows: [...], cols: [...], values: [[...]], format, diverging?})` — diverging (green/red around 0) when values go negative.

### Tables — `openpf.table(target, {columns, rows, sort?, sortable=true, empty?})`

`columns: [{key, label, format?, align?, tone?, ticker?, render?(value, row) → string|Node}]`. `format` right-aligns + monospaces the column; `tone: true` colours by sign; `ticker: true` makes the cell open the instrument sheet. `sort: {key, dir: 'desc'|'asc'}`. Returns `{update(rows)}`. Wrap in `<section class="op-card op-card-flush">` with an `.op-card-header`.

### CSS kit

Layout `op-stack` (vertical, 16px gap), `op-row`, `op-between`, `op-grid` + `op-grid-2|3|4` (responsive), `op-span-2`. Card `op-card` (`op-card-flush` for edge-to-edge tables), `op-card-header`, `op-card-title`, `op-card-desc`. Stat `op-stat` > `op-stat-label` + `op-stat-value` + `op-stat-hint` (put stats inside `op-card`s in an `op-grid`). Text `op-eyebrow`, `op-muted`, `op-small`, `op-num`, `op-positive`, `op-negative`, `op-warning`, `op-section-title`. `op-badge` (+ `-positive|-negative|-warning|-primary`). `op-btn` (+ `op-btn-primary`, `op-btn-ghost`, `op-btn-sm`), `op-input`. States `op-empty`, `op-skeleton`, `op-divider`. Theme vars: `--foreground --muted-foreground --card --muted --accent --border --primary --positive --negative --warning --chart-1..8 --font-mono --radius`.

## Data sources — `await openpf.data(source, params)`

All money is in the display currency (GBP default) unless a field says otherwise. Fractions are 0–1 (`0.05` = 5%). Dates are ISO strings. Call `sample_data` to see live examples before writing code.

**`portfolio`** `{account?: 'all'|'invest'|'stocks_isa', combine?: true}` — current holdings. With `combine` (default) positions held in both accounts are merged per ticker.
```
{currency:'GBP', as_of, account:{account:'all', total, invested, free_cash, ppl},
 accounts:[{account:'invest'|'stocks_isa', total, invested, free_cash, ppl}],
 positions:[{ticker:'NVDA', name:'NVIDIA', accounts:['stocks_isa'], yf:'NVDA', instrument_currency:'USD',
   quantity, price, avg_price /* both in instrument_currency; GBX = pence */, value, cost, ppl /* display ccy */,
   ppl_pct, weight /* of account total incl. cash */, momentum_63d, rsi_14, volatility_30d, risk_flag}],
 metrics:{total_value, free_cash, cash_ratio, concentration_hhi, top_position_weight, estimated_beta, estimated_volatility}}
```
Positions are sorted by value desc. No sector field — use `fundamentals` per ticker (slow; ≤ ~15 tickers, run in parallel with `Promise.all`) or bucket by name.

**`history`** `{range?: '1m'|'3m'|'6m'|'ytd'|'1y'|'2y'|'5y'|'all', account?}` — daily equity curve.
`{currency, range, points:[{date:'2026-07-12', total, invested, free_cash, gain /* value earned net of deposits since range start */}], return_pct /* money-weighted */, net_contributed, start_value, end_value}`

**`orders`** `{account?, ticker?, limit?: 1–50}` — most recent filled/cancelled broker orders, newest first (per account, up to 50 each).
`{orders:[{id, account, ticker, name, side:'buy'|'sell', type:'MARKET'|'LIMIT'…, status:'FILLED'…, quantity, fill_price /* instrument ccy */, value /* account ccy */, created_at}], errors:[]}`

**`dividends`** `{account?}` — every dividend received (can be empty for new accounts).
`{currency, items:[{account, ticker, amount, currency, paid_on:'2026-09-08', quantity, type}], monthly:[{month:'2026-06', amount}] /* only months with payments, ascending */, by_ticker:[{ticker, amount}], total, errors:[]}`
Fill missing months with 0 yourself when charting a calendar range.

**`prices`** `{ticker, range?: '5d'|'1mo'|'3mo'|'6mo'|'1y'|'2y'|'5y'|'ytd'|'max', interval?: '1d'|'1wk'|'1mo'}` — OHLC. Use a position's `yf` for non-US listings.
`{ticker, range, interval, candles:[{date, open, high, low, close, volume}]}`

**`quotes`** `{tickers: ['AAPL', 'VUSA.L']}` (≤30) — latest price + day change.
`{quotes:[{ticker, price, change_pct /* fraction */, currency}]}` (price null if unavailable)

**`fundamentals`** `{ticker}` — company facts (yfinance; ~1s each, cached).
`{ticker, long_name, sector, industry, currency, market_cap, trailing_pe, forward_pe, price_to_book, price_to_sales, peg_ratio, profit_margins, gross_margins, operating_margins, return_on_equity, debt_to_equity, free_cashflow, fcf_yield, total_revenue, revenue_growth, earnings_growth, dividend_yield /* fraction, 0.0073 = 0.73% */, beta}` (any may be null)

**`watchlist`** `{status?: 'watching'|'acted'|'archived'|'all'}` — `{items:[{id, symbol, name, note, conviction:'low'|'medium'|'high'|null, status, target_price, target_direction:'above'|'below'|null, created_at}]}`

**`proposals`** `{limit?}` — Archie's trade proposals awaiting/after approval.
`{proposals:[{id, created_at, status:'proposed'|'approved'|'executed'|'rejected'|…, ticker, side, quantity, notional, confidence, rationale, account, executed_at}]}`

**`plays`** `{status?: 'draft'|'active'|'paused'|'closed'}` — theme bets.
`{available, plays:[{id, title, thesis, status, conviction, horizon_months, account_kind, budget_gbp, legs:[{ticker, target_weight, …}], kill_criteria:[…], review:{verdict, …}, created_at, activated_at}]}`. Link a play with `openpf.navigate('/plays/' + id)`. If `plays` is empty, say so and link to `/plays`.

Any other source name is rejected.

## Patterns

- Hero stats: `<div class="op-grid op-grid-3">` of `<div class="op-card op-stat">` with label/value/hint.
- Inputs that persist (targets, thresholds): read `openpf.state.get('targets', {})`, render `op-input`s, on change `openpf.state.set(...)` and re-render the derived parts (not the inputs).
- Make tickers clickable (`ticker: true` column or `openpf.openTicker`).
- Offer at most one "Ask Archie" action that turns the view's insight into a question, e.g. `openpf.ask('Propose trades to bring my portfolio back to these targets: …')`.
- Never invent data. If something can't be computed from the sources, say so in the view.
