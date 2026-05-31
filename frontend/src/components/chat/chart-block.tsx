import { useMemo } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { StockChart } from '@/components/StockChart'

/**
 * Chart spec emitted by Archie inside a ```chart fenced code block.
 *
 * Two shapes are supported:
 *
 * 1. Ticker spec (preferred — renders the interactive StockChart with the
 *    live candles + Kronos forecast cone):
 *      {"ticker":"MU","period":"6mo","indicators":["sma20","sma50"],"forecast":true,"title":"…"}
 *
 * 2. Inline series spec (for arbitrary computed data — drawn with Recharts):
 *      {"type":"line","title":"…","xKey":"date","data":[…],"series":[{"key":"value","label":"…","color":"#…"}]}
 */
interface TickerSpec {
  ticker: string
  period?: string
  interval?: string
  chartType?: 'candlestick' | 'line'
  indicators?: string[]
  forecast?: boolean
  forecastHorizon?: number
  height?: number
  title?: string
}

interface InlineSeries {
  key: string
  label?: string
  color?: string
}

interface InlineSpec {
  type: 'line' | 'bar'
  title?: string
  xKey?: string
  data?: Array<Record<string, unknown>>
  series?: InlineSeries[]
  height?: number
}

type Spec = TickerSpec | InlineSpec

const INLINE_COLORS = ['#6ea8d4', '#d8a85c', '#5fb98a', '#cf6a98', '#a98fcf', '#e0635a']

function isTickerSpec(spec: Spec): spec is TickerSpec {
  return typeof (spec as TickerSpec).ticker === 'string' && (spec as TickerSpec).ticker.length > 0
}

function isInlineSpec(spec: Spec): spec is InlineSpec {
  const s = spec as InlineSpec
  return (s.type === 'line' || s.type === 'bar') && Array.isArray(s.data)
}

/** Small muted note shown when the spec can't be parsed or understood. */
function InvalidSpec({ raw }: { raw: string }) {
  return (
    <div className="my-2 rounded-lg border border-border/60 bg-muted/40 p-3 text-xs text-muted-foreground">
      <span className="font-medium">Invalid chart spec</span>
      <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] opacity-80">
        {raw.trim()}
      </pre>
    </div>
  )
}

function ChartCard({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <div className="my-2 w-full overflow-hidden rounded-lg border border-border/60 bg-card/40 p-3">
      {title ? (
        <div className="mb-2 text-xs font-medium tracking-tight text-muted-foreground">{title}</div>
      ) : null}
      {children}
    </div>
  )
}

function InlineChart({ spec }: { spec: InlineSpec }) {
  const data = spec.data ?? []
  const xKey = spec.xKey ?? 'x'
  const height = typeof spec.height === 'number' ? spec.height : 300
  const series: InlineSeries[] =
    spec.series && spec.series.length > 0
      ? spec.series
      : // Infer a single series from the first non-x numeric key when none provided.
        (() => {
          const first = data[0] ?? {}
          const key = Object.keys(first).find((k) => k !== xKey && typeof first[k] === 'number')
          return key ? [{ key }] : []
        })()

  if (data.length === 0 || series.length === 0) {
    return (
      <div className="flex h-[160px] items-center justify-center text-xs text-muted-foreground">
        No data to plot.
      </div>
    )
  }

  const axisProps = {
    stroke: '#a3a09a',
    tick: { fill: '#a3a09a', fontSize: 11 },
    tickLine: false,
    axisLine: { stroke: 'rgba(255,255,255,0.1)' },
  } as const

  return (
    <ResponsiveContainer width="100%" height={height}>
      {spec.type === 'bar' ? (
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
          <XAxis dataKey={xKey} {...axisProps} />
          <YAxis {...axisProps} width={44} />
          <Tooltip
            cursor={{ fill: 'rgba(255,255,255,0.04)' }}
            contentStyle={{
              background: '#1a1a1a',
              border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: 8,
              fontSize: 12,
            }}
          />
          {series.map((s, idx) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label ?? s.key}
              fill={s.color ?? INLINE_COLORS[idx % INLINE_COLORS.length]}
              radius={[2, 2, 0, 0]}
            />
          ))}
        </BarChart>
      ) : (
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
          <XAxis dataKey={xKey} {...axisProps} />
          <YAxis {...axisProps} width={44} />
          <Tooltip
            contentStyle={{
              background: '#1a1a1a',
              border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: 8,
              fontSize: 12,
            }}
          />
          {series.map((s, idx) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label ?? s.key}
              stroke={s.color ?? INLINE_COLORS[idx % INLINE_COLORS.length]}
              strokeWidth={2}
              dot={false}
            />
          ))}
        </LineChart>
      )}
    </ResponsiveContainer>
  )
}

export function ChartBlock({ spec }: { spec: string }) {
  const parsed = useMemo<{ value: Spec } | null>(() => {
    try {
      const value = JSON.parse(spec) as Spec
      if (value && typeof value === 'object') return { value }
      return null
    } catch {
      return null
    }
  }, [spec])

  if (!parsed) {
    return <InvalidSpec raw={spec} />
  }

  const value = parsed.value

  // Preferred path: a ticker spec → reuse the interactive StockChart, which
  // pulls candles + the Kronos forecast cone onto a single chart.
  if (isTickerSpec(value)) {
    return (
      <ChartCard title={value.title}>
        <StockChart
          ticker={value.ticker}
          period={value.period ?? '6mo'}
          interval={value.interval ?? '1d'}
          chartType={value.chartType ?? 'candlestick'}
          indicators={value.indicators ?? ['sma20', 'sma50']}
          forecast={value.forecast ?? false}
          forecastHorizon={value.forecastHorizon ?? 30}
          height={typeof value.height === 'number' ? value.height : 300}
        />
      </ChartCard>
    )
  }

  // Inline computed-series spec → small Recharts line/bar chart.
  if (isInlineSpec(value)) {
    return (
      <ChartCard title={value.title}>
        <InlineChart spec={value} />
      </ChartCard>
    )
  }

  return <InvalidSpec raw={spec} />
}
