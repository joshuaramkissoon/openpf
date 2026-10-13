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

export type Trend = { from: string; field: string; x?: string }
export type HeroBlock = Base & { type: 'hero'; label: string; value: Measure; format?: VizFormat; delta?: Measure; deltaFormat?: VizFormat; caption?: string; trend?: Trend }
export type MeterBlock = Base & { type: 'meter'; label: string; value: Measure; max: Measure | number; format?: VizFormat; mark?: Measure; markLabel?: string; caption?: string }
export type KpiItem = { label: string; value: Measure; format?: VizFormat; delta?: Measure; deltaFormat?: VizFormat; hint?: string }
export type KpisBlock = Base & { type: 'kpis'; items: KpiItem[] }
export type TreemapBlock = Base & { type: 'treemap'; label: string; size: string; color?: string; group?: string; format?: VizFormat; colorFormat?: VizFormat }
export type AllocationBlock = Base & { type: 'allocation'; label: string; value: string; style?: 'bar' | 'donut'; format?: VizFormat }
export type BarsBlock = Base & {
  type: 'bars'
  label: string
  value: string
  format?: VizFormat
  orientation?: 'horizontal' | 'vertical'
  limit?: number
  secondary?: string
  secondaryFormat?: VizFormat
}
export type TimeseriesBlock = Base & { type: 'timeseries'; x?: string; series: { field: string; label: string }[]; style?: 'area' | 'line'; format?: VizFormat }
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
