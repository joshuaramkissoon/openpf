/** Viz spec v1 — mirrors backend/app/views_sdk/VIZ_SPEC.md and app/services/viz_spec.py. */

export type VizFormat = 'money' | 'moneyCompact' | 'signedMoney' | 'pct' | 'signedPct' | 'num' | 'int' | 'date' | 'text'
export type VizWidth = 'full' | 'half' | 'third' | 'two-thirds'

export type Row = Record<string, unknown>

export type Step =
  | { filter: { field: string; op?: '=' | '!=' | '>' | '>=' | '<' | '<=' | 'in' | 'notin' | 'contains'; value?: unknown } }
  | { enrich: ('sector' | 'industry' | 'country' | 'asset_type' | 'play' | 'theme' | 'play_id' | 'dividend_yield' | 'ex_dividend_date')[] }
  | { groupBy: string }
  | { sort: { by: string; dir?: 'asc' | 'desc' } }
  | { top: { n: number; other?: boolean } }
  | { compute: { as: string; op: 'share' | 'ratio' | 'diff' | 'product' | 'abs' | 'neg' | 'scale'; field?: string; a?: string; b?: string; by?: number } }
  | { month: { field: string; sum: string; last?: number } }

export type Measure = {
  field?: string
  agg?: 'sum' | 'avg' | 'min' | 'max' | 'count'
  per?: Measure
  from?: string
  transform?: Step[]
}

type Base = {
  title?: string
  subtitle?: string
  width?: VizWidth
  from?: string
  transform?: Step[]
  empty?: string
}

/** `last`: only the most recent N points. */
export type Trend = { from: string; field: string; x?: string; last?: number }
/** Counts the block's rows up vs down on a signed field ("8 up · 14 down"). */
export type Breadth = { field: string }
/**
 * `deltaLabel` names what a delta is relative to ("of your book", "on cost") — a % never stands alone.
 * `name`: a field of the block's first row that says what the number is about ("country" → United States).
 */
export type HeroBlock = Base & { type: 'hero'; label: string; value: Measure; format?: VizFormat; delta?: Measure; deltaFormat?: VizFormat; deltaLabel?: string; name?: string; caption?: string; trend?: Trend; breadth?: Breadth }
export type MeterBlock = Base & { type: 'meter'; label: string; value: Measure; max: Measure | number; format?: VizFormat; mark?: Measure; markLabel?: string; caption?: string }
export type KpiItem = { label: string; value: Measure; format?: VizFormat; delta?: Measure; deltaFormat?: VizFormat; hint?: string }
export type KpisBlock = Base & { type: 'kpis'; items: KpiItem[] }
/** Extra per-row columns a chart shows in its tooltip and table view. */
export type Detail = { field: string; label: string; format?: VizFormat }
export type TreemapBlock = Base & { type: 'treemap'; label: string; size: string; color?: string; group?: string; format?: VizFormat; colorFormat?: VizFormat; details?: Detail[] }
export type AllocationBlock = Base & { type: 'allocation'; label: string; value: string; style?: 'bar' | 'donut'; format?: VizFormat; details?: Detail[] }
export type BarsBlock = Base & {
  type: 'bars'
  label: string
  value: string
  format?: VizFormat
  orientation?: 'horizontal' | 'vertical'
  limit?: number
  secondary?: string
  secondaryFormat?: VizFormat
  /** Column headers over the values: what the number is, and what a % is a share of ("% of account"). */
  valueLabel?: string
  secondaryLabel?: string
  /** Which bars carry the accent; the rest stay neutral. Default: the largest (horizontal), all (vertical). */
  highlight?: 'max' | 'min' | 'first' | 'last' | 'all' | 'none'
  details?: Detail[]
  /** 'sign': two labelled groups — the top `limit` up and the top `limit` down, on one scale. */
  split?: 'sign'
}
/**
 * `zero`: start the y-axis at 0 (default for money levels — a small change must not look like a cliff).
 * `curve`: 'step' for balances that change in jumps (cash); auto-detected when omitted.
 */
export type TimeseriesBlock = Base & { type: 'timeseries'; x?: string; series: { field: string; label: string }[]; style?: 'area' | 'line'; format?: VizFormat; zero?: boolean; curve?: 'linear' | 'step' }
export type MultiplesBlock = Base & { type: 'multiples'; label: string; ticker?: string; range?: string; value?: string; valueFormat?: VizFormat }
export type HeatgridBlock = Base & {
  type: 'heatgrid'
  mode?: 'pivot' | 'returns' | 'correlation'
  rows?: string
  cols?: string
  value?: string
  label?: string
  ticker?: string
  days?: number
  range?: string
  format?: VizFormat
}
export type TableBlock = Base & { type: 'table'; columns: { field: string; label: string; format?: VizFormat }[]; limit?: number }
export type NoteBlock = Base & { type: 'note'; text: string }

export type Block =
  | HeroBlock
  | MeterBlock
  | KpisBlock
  | TreemapBlock
  | AllocationBlock
  | BarsBlock
  | TimeseriesBlock
  | MultiplesBlock
  | HeatgridBlock
  | TableBlock
  | NoteBlock

export type Query = { source: string; params?: Record<string, unknown> }

export type VizSpec = {
  version: 1
  title: string
  description?: string
  icon?: string
  controls?: { account?: boolean }
  data?: Record<string, Query>
  blocks: Block[]
}

export type Account = 'all' | 'invest' | 'stocks_isa'
