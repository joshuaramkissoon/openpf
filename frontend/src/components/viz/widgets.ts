import type { WidgetSize } from './shared'
import type { VizSpec } from './types'

/**
 * Widget gallery seeds for a customisable Home (Apple-widget style): each is a
 * one-block viz spec that renders well at the listed sizes via
 * `<VizBlock spec={w.spec} size="S" | "M" | "L" />`. Pinned view specs can be
 * offered alongside these (use their first block).
 */
export type WidgetPreset = {
  id: string
  title: string
  description: string
  sizes: WidgetSize[]
  defaultSize: WidgetSize
  spec: VizSpec
}

const pf = { pf: { source: 'portfolio' } }
const hist = { hist: { source: 'history', params: { range: '1y' } } }

export const WIDGET_PRESETS: WidgetPreset[] = [
  {
    id: 'today',
    title: 'Today',
    description: "Today's gain or loss across your holdings",
    sizes: ['S', 'M'],
    defaultSize: 'S',
    spec: {
      version: 1,
      title: 'Today',
      data: pf,
      blocks: [
        {
          type: 'hero',
          label: 'Today',
          from: 'pf.positions',
          value: { agg: 'sum', field: 'day_change' },
          format: 'signedMoney',
          delta: { agg: 'sum', field: 'day_change', per: { from: 'pf.account', field: 'invested' } },
          deltaFormat: 'signedPct',
        },
      ],
    },
  },
  {
    id: 'value',
    title: 'Portfolio value',
    description: 'Total value over the last year',
    sizes: ['S', 'M', 'L'],
    defaultSize: 'M',
    spec: {
      version: 1,
      title: 'Portfolio value',
      data: hist,
      blocks: [{ type: 'timeseries', title: 'Portfolio value', from: 'hist.points', series: [{ field: 'total', label: 'Value' }], style: 'area', format: 'moneyCompact' }],
    },
  },
  {
    id: 'movers-map',
    title: 'Market map',
    description: 'Holdings sized by value, coloured by today’s move',
    sizes: ['M', 'L'],
    defaultSize: 'L',
    spec: {
      version: 1,
      title: 'Market map',
      data: pf,
      blocks: [{ type: 'treemap', title: 'Today', from: 'pf.positions', transform: [{ enrich: ['theme'] }], label: 'ticker', size: 'value', color: 'day_change_pct', group: 'theme' }],
    },
  },
  {
    id: 'movers',
    title: 'Top movers',
    description: 'Biggest contributors to today’s move',
    sizes: ['S', 'M', 'L'],
    defaultSize: 'M',
    spec: {
      version: 1,
      title: 'Top movers',
      data: pf,
      blocks: [
        {
          type: 'bars',
          title: 'Top movers',
          from: 'pf.positions',
          transform: [{ compute: { as: 'abs_move', op: 'abs', field: 'day_change' } }, { sort: { by: 'abs_move', dir: 'desc' } }, { top: { n: 8 } }],
          label: 'ticker',
          value: 'day_change',
          format: 'signedMoney',
          secondary: 'day_change_pct',
          secondaryFormat: 'signedPct',
        },
      ],
    },
  },
  {
    id: 'sectors',
    title: 'Sector mix',
    description: 'Where your money sits by sector',
    sizes: ['S', 'M', 'L'],
    defaultSize: 'M',
    spec: {
      version: 1,
      title: 'Sector mix',
      data: pf,
      blocks: [{ type: 'allocation', title: 'Sector mix', from: 'pf.positions', transform: [{ enrich: ['sector'] }, { groupBy: 'sector' }, { sort: { by: 'value' } }], label: 'sector', value: 'value' }],
    },
  },
  {
    id: 'cash',
    title: 'Idle cash',
    description: 'Uninvested cash and its trend',
    sizes: ['S', 'M'],
    defaultSize: 'S',
    spec: {
      version: 1,
      title: 'Idle cash',
      data: { ...pf, ...hist },
      blocks: [
        {
          type: 'hero',
          label: 'Idle cash',
          from: 'pf.account',
          value: { field: 'free_cash' },
          format: 'money',
          delta: { from: 'pf.metrics', field: 'cash_ratio' },
          deltaFormat: 'pct',
          trend: { from: 'hist.points', field: 'free_cash' },
        },
      ],
    },
  },
  {
    id: 'isa',
    title: 'ISA allowance',
    description: 'This tax year’s £20,000 Stocks ISA allowance',
    sizes: ['S', 'M'],
    defaultSize: 'M',
    spec: {
      version: 1,
      title: 'ISA allowance',
      data: { cf: { source: 'cashflows' } },
      blocks: [
        {
          type: 'meter',
          label: 'ISA allowance used',
          from: 'cf.tax_year',
          value: { field: 'isa_used' },
          max: { field: 'isa_allowance' },
          mark: { field: 'year_elapsed_pct' },
          markLabel: 'Share of the tax year gone',
          format: 'money',
        },
      ],
    },
  },
  {
    id: 'income',
    title: 'Dividends',
    description: 'Dividends received in the last 12 months',
    sizes: ['S', 'M', 'L'],
    defaultSize: 'M',
    spec: {
      version: 1,
      title: 'Dividends',
      data: { div: { source: 'dividends' } },
      blocks: [
        {
          type: 'bars',
          title: 'Dividends by month',
          from: 'div.items',
          transform: [{ month: { field: 'paid_on', sum: 'amount', last: 12 } }],
          label: 'label',
          value: 'amount',
          orientation: 'vertical',
          format: 'money',
          empty: 'No dividends yet.',
        },
      ],
    },
  },
  {
    id: 'winners',
    title: 'Winners & losers',
    description: 'Gain or loss since purchase',
    sizes: ['S', 'M', 'L'],
    defaultSize: 'L',
    spec: {
      version: 1,
      title: 'Winners & losers',
      data: pf,
      blocks: [{ type: 'bars', title: 'Since purchase', from: 'pf.positions', transform: [{ sort: { by: 'ppl', dir: 'desc' } }], label: 'ticker', value: 'ppl', format: 'signedMoney', secondary: 'ppl_pct', secondaryFormat: 'signedPct' }],
    },
  },
]
