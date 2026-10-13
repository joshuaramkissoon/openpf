import { cn } from '@/lib/utils'
import { num } from '../engine'
import { EmptyNote, useBlur, useFmt, useVizEnv } from '../shared'
import type { Row, VizFormat } from '../types'

/**
 * The drill-down layer: the holdings behind a segment as ranked bars with value,
 * today's move and P&L. Clicking a holding opens its detail sheet.
 */
export function DrillList({ rows, valueField = 'value', format = 'money' }: { rows: Row[]; valueField?: string; format?: VizFormat }) {
  const fmt = useFmt()
  const blur = useBlur()
  const env = useVizEnv()
  const items = [...rows].sort((a, b) => (num(b[valueField]) ?? 0) - (num(a[valueField]) ?? 0))
  if (!items.length) return <EmptyNote>No holdings here.</EmptyNote>
  const max = Math.max(...items.map((r) => Math.abs(num(r[valueField]) ?? 0))) || 1
  return (
    <ul className="space-y-1">
      {items.map((r, i) => {
        const v = num(r[valueField]) ?? 0
        const ticker = typeof r.ticker === 'string' ? r.ticker : null
        const dc = num(r.day_change_pct)
        return (
          <li key={`${ticker ?? i}`}>
            <button
              type="button"
              disabled={!ticker}
              onClick={() => ticker && env.openTicker(ticker)}
              className="grid w-full grid-cols-[minmax(64px,140px)_1fr_auto_auto] items-center gap-3 rounded-md px-1.5 py-1.5 text-left text-sm hover:bg-muted/60"
            >
              <span className="min-w-0">
                <span className="block truncate font-medium">{ticker ?? String(r.label ?? '')}</span>
                {r.name && r.name !== ticker ? <span className="block truncate text-[11px] text-muted-foreground">{String(r.name)}</span> : null}
              </span>
              <span className="h-2 overflow-hidden rounded-full bg-muted">
                <span className="block h-full rounded-full" style={{ width: `${(Math.abs(v) / max) * 100}%`, background: 'var(--viz-1)' }} />
              </span>
              <span className={cn('text-right tabular-nums', blur(format))}>{fmt(v, format)}</span>
              <span className={cn('w-16 text-right text-xs tabular-nums', dc == null ? 'text-muted-foreground' : dc >= 0 ? 'text-positive' : 'text-negative')}>
                {dc == null ? '' : fmt(dc, 'signedPct')}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
