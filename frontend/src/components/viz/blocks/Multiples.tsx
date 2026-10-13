import { useEffect, useState } from 'react'

import { cn } from '@/lib/utils'
import { fetchSource, num } from '../engine'
import { EmptyNote, Tooltip, useFmt, useTooltip, useVizEnv, useWidth } from '../shared'
import type { MultiplesBlock, Row } from '../types'

type Series = { label: string; ticker: string; row: Row; closes: { date: string; close: number }[] }

/** Daily closes for up to 12 tickers, indexed to the first close so every tile shares one scale. */
export function usePriceSeries(rows: Row[], tickerField: string, labelField: string, range: string, cap = 12) {
  const env = useVizEnv()
  const [state, setState] = useState<{ series: Series[]; loading: boolean }>({ series: [], loading: true })
  const key = rows.slice(0, cap).map((r) => String(r[tickerField] ?? '')).join(',')
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, loading: true }))
    const picks = rows.slice(0, cap).filter((r) => typeof r[tickerField] === 'string')
    Promise.all(
      picks.map(async (r) => {
        try {
          const res = (await fetchSource('prices', { ticker: r[tickerField], range, interval: '1d' }, env.currency)) as { candles?: { date: string; close: number }[] }
          return { label: String(r[labelField] ?? r[tickerField]), ticker: String(r.ticker ?? r[tickerField]), row: r, closes: (res.candles ?? []).filter((c) => num(c.close) != null) }
        } catch {
          return { label: String(r[labelField] ?? r[tickerField]), ticker: String(r.ticker ?? r[tickerField]), row: r, closes: [] }
        }
      }),
    ).then((series) => alive && setState({ series, loading: false }))
    return () => {
      alive = false
    }
  }, [key, range]) // eslint-disable-line react-hooks/exhaustive-deps
  return state
}

export function Multiples({ block, rows }: { block: MultiplesBlock; rows: Row[] }) {
  const fmt = useFmt()
  const env = useVizEnv()
  const [ref, width] = useWidth<HTMLDivElement>()
  const { tip, show, hide, hostRef } = useTooltip()
  const { series, loading } = usePriceSeries(rows, block.ticker ?? 'yf', block.label, block.range ?? '3mo', env.size === 'S' ? 1 : env.size === 'M' ? 2 : env.size === 'L' ? 4 : 12)
  if (!rows.length) return <EmptyNote>{block.empty ?? 'Nothing to show yet.'}</EmptyNote>
  if (loading && !series.length) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {rows.slice(0, 8).map((_, i) => (
          <div key={i} className="h-24 animate-pulse rounded-lg bg-muted/60" />
        ))}
      </div>
    )
  }
  const indexed = series.map((s) => {
    const first = s.closes[0]?.close
    return { ...s, pts: first ? s.closes.map((c) => c.close / first - 1) : [] }
  })
  const all = indexed.flatMap((s) => s.pts)
  const lo = Math.min(0, ...all)
  const hi = Math.max(0, ...all)
  const cols = env.size === 'S' ? 1 : env.size ? 2 : width < 480 ? 2 : width < 760 ? 3 : 4
  const tileW = (width - (cols - 1) * 12) / cols
  const sh = 44

  return (
    <div ref={hostRef} className="relative">
      <div ref={ref} className="grid gap-3" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
        {indexed.map((s) => {
          const ret = s.pts.length ? s.pts[s.pts.length - 1] : null
          const color = ret == null ? 'var(--viz-other)' : ret >= 0 ? 'var(--viz-gain)' : 'var(--viz-loss)'
          const X = (i: number) => (i / Math.max(1, s.pts.length - 1)) * (tileW - 24)
          const Y = (v: number) => sh - 2 - ((v - lo) / (hi - lo || 1)) * (sh - 4)
          const d = s.pts.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')
          const side = block.value ? num(s.row[block.value]) : null
          return (
            <button
              key={s.ticker}
              type="button"
              onClick={() => env.openTicker(s.ticker)}
              onPointerMove={(e) => show(e, { title: s.label, rows: [{ label: `Over ${block.range ?? '3mo'}`, value: fmt(ret, 'signedPct') }, ...(block.value ? [{ label: block.value.replace(/_/g, ' '), value: fmt(side, block.valueFormat) }] : [])] })}
              onPointerLeave={hide}
              className="rounded-lg border border-border/70 p-3 text-left transition-colors hover:bg-muted/40"
            >
              <div className="mb-1 flex items-baseline justify-between gap-2">
                <span className="truncate text-sm font-semibold">{s.label}</span>
                <span className={cn('text-xs tabular-nums', ret == null ? 'text-muted-foreground' : ret >= 0 ? 'text-positive' : 'text-negative')}>{fmt(ret, 'signedPct')}</span>
              </div>
              {s.pts.length > 1 ? (
                <svg width="100%" height={sh} viewBox={`0 0 ${Math.max(1, tileW - 24)} ${sh}`} preserveAspectRatio="none" aria-hidden>
                  <line x1={0} x2={tileW} y1={Y(0)} y2={Y(0)} stroke="var(--viz-grid)" />
                  <path d={d} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
                </svg>
              ) : (
                <div className="flex h-11 items-center text-xs text-muted-foreground">No price history</div>
              )}
              {block.value ? <div className="mt-1 text-[11px] text-muted-foreground tabular-nums">{fmt(side, block.valueFormat)} {block.value === 'day_change_pct' ? 'today' : block.value.replace(/_/g, ' ')}</div> : null}
            </button>
          )
        })}
      </div>
      <Tooltip tip={tip} hostWidth={width} />
    </div>
  )
}
