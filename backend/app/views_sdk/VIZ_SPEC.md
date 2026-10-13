# OpenPF Viz Spec (v1)

A **viz spec** is JSON that describes a visual page — or a single chart — over the
user's live portfolio data. It is data, not code: the app renders it natively with
its own chart library (`frontend/src/components/viz`), reading only the
whitelisted, read-only sources listed below. It cannot trade, fetch the network,
or run scripts. The backend validates every spec (`app/services/viz_spec.py`)
before it is saved or rendered.

Where specs appear:

- **Views** — saved pages (`/views/<id>`). Presets are hand-made specs.
- **Chat** — Archie can embed a spec in a reply as a fenced block:

  ````
  ```viz
  {"version": 1, "title": "Today by theme", "blocks": [{"type": "treemap", "from": "pf.positions", "transform": [{"enrich": ["theme"]}], "label": "ticker", "size": "value", "color": "day_change_pct", "group": "theme"}]}
  ```
  ````

  The chat renders it with `<VizBlock spec={...}/>` and offers **Pin as view**
  (`POST /api/views/specs {spec}` → a saved view, instantly). `data` may be
  omitted in chat: the default query `pf` (= `portfolio`) is always available.

Design intent: **visual first, detail on demand.** Lead with one hero number or
a KPI strip, then the picture (treemap, allocation, ranked bars, a curve).
Tables are the drill-down layer, not the page. 3–6 blocks is typical.

**No repeats.** Never follow a chart with a `table` of the same rows (same
`from` and the same `groupBy`, or the chart's label column). Every chart
already has a table-view toggle and drill-down; put the extra columns you'd
have shown (value, weight, holdings count…) in the chart's `details` instead.
A `table` is only for rows no chart on the page draws (upcoming ex-dividend
dates, news, cash movements). The validator drops a duplicate table and moves
its columns into the chart's `details`.

**Honest titles.** A title names the dimension the block really uses: grouped
by `sector` → "… by sector". `theme` is the owning play's title and falls back
to the sector for holdings outside a play, so only say "theme" when grouping by
`theme`. The validator retitles a mismatch ("by theme" on a sector grouping).

## Top level

```jsonc
{
  "version": 1,                       // required, always 1
  "title": "Today by theme",          // ≤ 60 chars
  "description": "Where today's move came from",   // ≤ 160 chars, optional
  "icon": "layout-grid",              // optional, lucide name from the icon list in VIEW_SDK.md
  "controls": {"account": true},      // optional: All / Invest / ISA switch above the page
  "data": {                           // optional named queries (max 8). "pf" defaults to portfolio.
    "pf":   {"source": "portfolio"},
    "hist": {"source": "history", "params": {"range": "1y"}}
  },
  "blocks": [ /* 1–12 blocks, rendered in order on a 12-column grid */ ]
}
```

`controls.account` rewrites `params.account` of every query that supports it
(`portfolio`, `history`, `orders`, `dividends`, `cashflows`).

## Common block fields

| field | type | notes |
|---|---|---|
| `type` | string | `hero` `meter` `kpis` `treemap` `allocation` `bars` `timeseries` `multiples` `heatgrid` `table` `note` |
| `title` | string ≤ 60 | sentence case; optional for `hero`/`kpis` |
| `subtitle` | string ≤ 160 | one line explaining a non-obvious metric |
| `width` | `full` (default) · `half` · `third` · `two-thirds` | stacks to full width on phones |
| `from` | `"<query>.<path>"` | rows the block reads, e.g. `pf.positions`, `hist.points`, `pf.account` (an object = one row) |
| `transform` | array | steps applied in order (below) |
| `empty` | string ≤ 160 | message when there are no rows |

### Transforms

Each step is an object with exactly one key:

| step | example | effect |
|---|---|---|
| `filter` | `{"filter": {"field": "value", "op": ">", "value": 0}}` | ops `= != > >= < <= in notin contains`; `value` may be a list for `in`/`notin`; `"$today"` and `"$tax_year_start"` (6 April) are dynamic dates for comparing ISO date fields |
| `enrich` | `{"enrich": ["sector", "theme"]}` | adds per-ticker fields: `sector` `industry` `country` `asset_type` (`Stock`/`ETF`/`Fund`) `play` (owning play title or `Unsorted`) `theme` (owning play title, else sector) `play_id` `dividend_yield` (fraction) `ex_dividend_date` |
| `groupBy` | `{"groupBy": "theme"}` | one row per value; sums numeric money fields (`value cost ppl day_change amount weight target_gbp`), adds `count` and `items` (the rows), and recomputes `day_change_pct` = Σday_change/(Σvalue−Σday_change), `ppl_pct` = Σppl/Σcost |
| `sort` | `{"sort": {"by": "value", "dir": "desc"}}` | `dir` `asc`/`desc` (default desc) |
| `top` | `{"top": {"n": 8, "other": true}}` | keep N; with `other` the rest fold into one "Other" row |
| `compute` | `{"compute": {"as": "share", "op": "share", "field": "value"}}` | ops: `share` (field ÷ Σfield), `ratio` (`a` ÷ `b`), `diff` (`a` − `b`), `product` (`a` × `b`, e.g. value × dividend_yield), `abs`, `neg`, `scale` (`field` × `by` number) |
| `month` | `{"month": {"field": "paid_on", "sum": "amount", "last": 12}}` | buckets dated rows into calendar months, zero-filled, → rows `{month, label, amount}` |

### Measures (single numbers)

Used by `hero`, `kpis` and `note`:

```jsonc
{"field": "total"}                                   // from the first row (e.g. from: "pf.account")
{"agg": "sum", "field": "day_change"}                // over rows: sum avg min max count
{"agg": "sum", "field": "day_change", "per": {"agg": "sum", "field": "value"}}   // ratio of two measures
```

A measure may carry its own `from` and `transform` (otherwise the block's are used).

### Formats

`money` · `moneyCompact` · `signedMoney` · `pct` · `signedPct` · `num` · `int` · `date` · `text`.
Money is in the display currency and honours privacy mode. Fractions are 0–1.

### Details (chart blocks)

`treemap`, `allocation` and `bars` take an optional `details` list (≤ 6) of
extra per-row columns, shown in the hover tooltip and the table view:
`"details": [{"field": "value", "label": "Value", "format": "money"}, {"field": "weight", "label": "Weight", "format": "pct"}, {"field": "count", "label": "Holdings", "format": "int"}]`.
Labels are what the user reads: never a raw field name.

## Blocks

**`hero`** — the one number the page leads with (one per page).
`{"type": "hero", "label": "Today", "value": Measure, "format": "signedMoney", "delta": Measure, "deltaFormat": "signedPct", "caption": "across 20 holdings", "trend": {"from": "hist.points", "field": "total"}?}`
`trend` adds a sparkline under the number.

**`meter`** — one amount against a limit, with an optional pace marker.
`{"type": "meter", "label": "ISA allowance used", "from": "cf.tax_year", "value": {"field": "isa_used"}, "max": {"field": "isa_allowance"} | 20000, "mark": {"field": "year_elapsed_pct"}?, "markLabel": "Share of the tax year gone"?, "format": "money"}`

**`kpis`** — 2–5 stat tiles.
`{"type": "kpis", "items": [{"label": "Invested", "value": Measure, "format": "money", "delta": Measure?, "deltaFormat": "signedPct"?, "hint": "text"?}]}`

**`treemap`** — part-to-whole by size, coloured by a signed measure. Best for "where is my money / what moved".
`{"type": "treemap", "from": "pf.positions", "label": "ticker", "size": "value", "color": "day_change_pct", "group": "theme"?, "format": "money"?, "colorFormat": "signedPct"?}`
`color` is diverging (red ← 0 → green) when signed; omit it for one neutral hue. With `group`, tiles nest under group headers and clicking a group drills into it; clicking a holding opens it.

**`allocation`** — part-to-whole for ≤ 7 slices (more fold into Other).
`{"type": "allocation", "from": "...", "label": "sector", "value": "value", "style": "bar" | "donut", "format": "money"?}`
`bar` (default) is a single 100% stacked bar with a legend; `donut` for ≤ 5 slices.

**`bars`** — ranked magnitude or a signed contribution.
`{"type": "bars", "from": "...", "label": "ticker", "value": "day_change", "format": "signedMoney", "orientation": "horizontal" (default) | "vertical", "limit": 10?, "secondary": "day_change_pct"?, "secondaryFormat": "signedPct"?}`
Signed values diverge from a zero line (gains green, losses red); unsigned bars use one hue. Use `vertical` for time buckets (e.g. months).

**`timeseries`** — change over time.
`{"type": "timeseries", "from": "hist.points", "x": "date", "series": [{"field": "total", "label": "Value"}, {"field": "invested", "label": "Invested"}], "style": "area" | "line", "format": "moneyCompact"}`
≤ 4 series, one y-axis. A single `area` series reads best.

**`multiples`** — one small sparkline per row, same scale per tile.
`{"type": "multiples", "from": "pf.positions", "transform": [{"top": {"n": 8}}], "label": "ticker", "ticker": "yf", "range": "3mo", "value": "day_change_pct"?, "valueFormat": "signedPct"?}`
Fetches `prices` for each row's `ticker` field (≤ 12 rows).

**`heatgrid`** — a grid of signed values.
`{"type": "heatgrid", "mode": "pivot", "from": "...", "rows": "sector", "cols": "account", "value": "value", "format": "money"}` or
`{"type": "heatgrid", "mode": "returns", "from": "pf.positions", "label": "ticker", "ticker": "yf", "days": 10}` (daily % moves per holding, ≤ 15 rows) or
`{"type": "heatgrid", "mode": "correlation", "from": "pf.positions", "label": "ticker", "ticker": "yf", "range": "6mo"}` (≤ 12 rows).

**`table`** — detail rows no chart on the page draws (keep it below the visuals; never repeat a chart's rows — use `details`).
`{"type": "table", "from": "...", "columns": [{"field": "ticker", "label": "Holding", "format": "text"}, {"field": "value", "label": "Value", "format": "money"}], "limit": 20?}`
A column with `"field": "ticker"` opens the holding on click.

**`note`** — one or two calm sentences of context. `{"type": "note", "text": "Plain text only."}`

Every chart block also offers a **table view** toggle and hover tooltips. Groups and
segments drill down (group → holdings → holding).

## Data sources

Same whitelist as code views (see `VIEW_SDK.md` for full shapes). Useful paths:

| source | params | paths |
|---|---|---|
| `portfolio` | `account` | `positions[]` (ticker, name, accounts, yf, instrument_currency, exposure_currency (GBX→GBP), value, cost, ppl, ppl_pct, weight, day_change, day_change_pct, quantity, momentum_63d, volatility_30d), `account` (total, invested, free_cash, ppl), `accounts[]` (account, total, invested, free_cash, ppl), `metrics` |
| `history` | `range` 1m 3m 6m ytd 1y 2y 5y all, `account` | `points[]` (date, total, invested, free_cash, gain), plus return_pct, net_contributed |
| `dividends` | `account` | `items[]` (ticker, amount, paid_on, account), `monthly[]` (month, amount), `by_ticker[]` (ticker, amount), `total` |
| `orders` | `account`, `ticker`, `limit` | `orders[]` (ticker, side, value, created_at, status) |
| `plays` | `status` | `plays[]` (id, title, status, exposure, owned_count, bench_count, rings.owned[]/related[]/bench[]), `owners[]` (ticker, play_id, value) |
| `cashflows` | `account` | `items[]` (account, type DEPOSIT/WITHDRAW, amount, currency, date), `tax_year` (start, end, isa_allowance, isa_used, isa_remaining, isa_withdrawn, isa_used_pct, days_left, year_elapsed_pct, monthly_to_max) |
| `news` | `movers` (top-N movers today) or `tickers`, `days`, `limit` | `items[]` (ticker, title, source, published_at, summary) — text only |
| `profiles` | `tickers` (≤ 40) | `profiles[]` (sector, industry, country, quote_type, dividend_yield, ex_dividend_date) — prefer the `enrich` transform |
| `fx` | — | `rates` (GBP, USD, EUR per 1 unit of display currency) |
| `watchlist`, `proposals` | | `items[]`, `proposals[]` |

**Money rule:** money comes only from display-currency fields (`value`, `cost`,
`ppl`, `day_change`, dividend `amount`); never multiply prices by quantities.

## Widgets

Any single block also renders as a fixed-size widget: `<VizBlock spec={...} size="S" | "M" | "L" />`
(S 1×1, M 2×1, L 2×2). Blocks pick compact forms at small sizes (hero → number + sparkline,
timeseries → sparkline, bars → top 3–8, allocation → bar + top slices). `WIDGET_PRESETS` in
`frontend/src/components/viz/widgets.ts` lists ready-made one-block widgets.

## Limits

≤ 12 blocks, ≤ 8 queries, ≤ 12 transform steps per block, spec ≤ 32 KB. Unknown
block types, sources, transforms or formats are rejected with a message that
names the problem.
