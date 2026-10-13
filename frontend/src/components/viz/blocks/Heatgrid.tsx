import { cn } from '@/lib/utils'
import { num } from '../engine'
import { colorCap, divergingFill, EmptyNote, inkOn, Tooltip, useBlur, useFmt, useTooltip, useWidth } from '../shared'
import type { HeatgridBlock, Row, VizFormat } from '../types'
import { usePriceSeries } from './Multiples'

type Grid = { rows: string[]; cols: string[]; values: (number | null)[][]; format: VizFormat; kind: 'signed' | 'magnitude' | 'correlation'; tickers?: string[] }

function pivot(block: HeatgridBlock, data: Row[]): Grid {
  const rowKeys: string[] = []
  const colKeys: string[] = []
  const cell = new Map<string, number>()
  for (const r of data) {
    const rk = String(r[block.rows!] ?? 'Other')
    const ck = String(Array.isArray(r[block.cols!]) ? (r[block.cols!] as unknown[]).join(' + ') : (r[block.cols!] ?? 'Other'))
    if (!rowKeys.includes(rk)) rowKeys.push(rk)
    if (!colKeys.includes(ck)) colKeys.push(ck)
    cell.set(`${rk}\u0000${ck}`, (cell.get(`${rk}\u0000${ck}`) ?? 0) + (num(r[block.value!]) ?? 0))
  }
  const values = rowKeys.map((rk) => colKeys.map((ck) => cell.get(`${rk}\u0000${ck}`) ?? null))
  const signed = values.flat().some((v) => (v ?? 0) < 0)
  return { rows: rowKeys, cols: colKeys, values, format: block.format ?? 'money', kind: signed ? 'signed' : 'magnitude' }
}

function pearson(a: number[], b: number[]) {
  const n = Math.min(a.length, b.length)
  if (n < 5) return null
  const ma = a.slice(-n).reduce((s, x) => s + x, 0) / n
  const mb = b.slice(-n).reduce((s, x) => s + x, 0) / n
  let num_ = 0
  let da = 0
  let db = 0
  for (let i = 0; i < n; i++) {
    const x = a[a.length - n + i] - ma
    const y = b[b.length - n + i] - mb
    num_ += x * y
    da += x * x
    db += y * y
  }
  return da && db ? num_ / Math.sqrt(da * db) : null
}

export function Heatgrid({ block, rows }: { block: HeatgridBlock; rows: Row[] }) {
  const mode = block.mode ?? 'pivot'
  const needsPrices = mode !== 'pivot'
  const cap = mode === 'correlation' ? 12 : 15
  const prices = usePriceSeries(needsPrices ? rows : [], block.ticker ?? 'yf', block.label ?? 'ticker', mode === 'correlation' ? (block.range ?? '6mo') : '1mo', cap)

  if (!rows.length) return <EmptyNote>{block.empty ?? 'Nothing to show yet.'}</EmptyNote>
  if (needsPrices && prices.loading && !prices.series.length) return <div className="h-64 animate-pulse rounded-lg bg-muted/60" />

  let grid: Grid
  if (mode === 'pivot') grid = pivot(block, rows)
  else {
    const rets = prices.series.map((s) => {
      const out: { date: string; r: number }[] = []
      for (let i = 1; i < s.closes.length; i++) out.push({ date: s.closes[i].date, r: s.closes[i].close / s.closes[i - 1].close - 1 })
      return { label: s.label, ticker: s.ticker, rets: out }
    })
    if (mode === 'returns') {
      const days = block.days ?? 10
      const dates = [...new Set(rets.flatMap((s) => s.rets.map((x) => x.date)))].sort().slice(-days)
      grid = {
        rows: rets.map((s) => s.label),
        tickers: rets.map((s) => s.ticker),
        cols: dates.map((d) => new Date(`${d}T00:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })),
        values: rets.map((s) => dates.map((d) => s.rets.find((x) => x.date === d)?.r ?? null)),
        format: 'signedPct',
        kind: 'signed',
      }
    } else {
      const series = rets.map((s) => s.rets.map((x) => x.r))
      grid = {
        rows: rets.map((s) => s.label),
        tickers: rets.map((s) => s.ticker),
        cols: rets.map((s) => s.label),
        values: series.map((a) => series.map((b) => pearson(a, b))),
        format: 'num',
        kind: 'correlation',
      }
    }
  }
  return <GridView block={block} grid={grid} />
}

function GridView({ block, grid }: { block: HeatgridBlock; grid: Grid }) {
  const fmt = useFmt()
  const blur = useBlur()
  const [ref, width] = useWidth<HTMLDivElement>()
  const { tip, show, hide, hostRef } = useTooltip()
  if (!grid.rows.length || !grid.cols.length) return <EmptyNote>{block.empty ?? 'Nothing to show yet.'}</EmptyNote>
  const flat = grid.values.flat()
  const capV = grid.kind === 'correlation' ? 1 : grid.kind === 'signed' ? colorCap(flat, 0.005, 0.1) : Math.max(...flat.map((v) => Math.abs(v ?? 0))) || 1
  const fill = (v: number | null) => {
    if (v == null) return { fill: 'transparent', strength: 0 }
    if (grid.kind === 'signed') return divergingFill(v, capV)
    if (grid.kind === 'correlation') {
      // Correlation isn't good/bad: cool ↔ warm, grey at 0.
      const t = Math.min(1, Math.abs(v))
      const strength = 0.12 + 0.88 * t
      return { fill: `color-mix(in oklab, ${v >= 0 ? 'var(--viz-warm)' : 'var(--viz-cool)'} ${Math.round(strength * 100)}%, var(--viz-mid))`, strength }
    }
    const t = Math.abs(v) / capV
    const strength = 0.12 + 0.88 * t
    return { fill: `color-mix(in oklab, var(--viz-1) ${Math.round(strength * 100)}%, var(--viz-mid))`, strength }
  }
  const labelW = Math.min(132, Math.max(...grid.rows.map((r) => r.length)) * 7 + 16)
  const cellW = Math.max(22, (width - labelW) / grid.cols.length)
  const cellH = grid.kind === 'correlation' ? Math.min(cellW, 34) : 30
  const showText = cellW >= 44
  const isMoney = grid.format.includes('oney')

  return (
    <div ref={hostRef} className="relative">
      <div ref={ref} className="w-full overflow-x-auto">
        {width > 0 ? (
          <div style={{ display: 'grid', gridTemplateColumns: `${labelW}px repeat(${grid.cols.length}, ${cellW}px)` }} className="text-xs">
            <div />
            {grid.cols.map((c) => (
              <div key={c} className="truncate px-1 pb-1.5 text-center text-[10.5px] text-muted-foreground" title={c}>
                {c}
              </div>
            ))}
            {grid.rows.map((r, i) => (
              <div key={r} className="contents">
                <div className="flex items-center truncate pr-2 font-medium" style={{ height: cellH }} title={r}>
                  {r}
                </div>
                {grid.cols.map((c, j) => {
                  const v = grid.values[i][j]
                  const f = fill(v)
                  return (
                    <div
                      key={c}
                      className="p-[1px]"
                      onPointerMove={(e) => show(e, { title: `${r} · ${c}`, rows: [{ label: grid.kind === 'correlation' ? 'Correlation' : (block.value ?? 'Move'), value: v == null ? '—' : fmt(v, grid.format), money: isMoney }] })}
                      onPointerLeave={hide}
                    >
                      <div
                        className={cn('flex h-full items-center justify-center rounded-[3px] tabular-nums', isMoney && blur(grid.format))}
                        style={{ height: cellH - 2, background: f.fill, color: inkOn(f.strength), border: v == null ? '1px dashed var(--viz-grid)' : undefined }}
                      >
                        {showText && v != null ? (grid.kind === 'correlation' ? v.toFixed(2) : fmt(v, grid.format === 'money' ? 'moneyCompact' : grid.format)) : ''}
                      </div>
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        ) : null}
      </div>
      <Tooltip tip={tip} hostWidth={width} />
    </div>
  )
}
