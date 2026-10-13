import { useId, useState } from 'react'

import { cn } from '@/lib/utils'
import { num } from '../engine'
import { catColor, EmptyNote, Legend, Tooltip, useBlur, useFmt, useTooltip, useVizEnv, useWidth } from '../shared'
import type { Row, TimeseriesBlock } from '../types'
import { niceTicks } from './Bars'
import { Sparkline } from './Figures'
import { isStepSeries } from './step'

function toTime(v: unknown): number | null {
  if (typeof v === 'number') return v
  const s = String(v ?? '')
  const d = new Date(/^\d{4}-\d{2}$/.test(s) ? `${s}-01T00:00:00` : /^\d{4}-\d{2}-\d{2}$/.test(s) ? `${s}T00:00:00` : s)
  return Number.isNaN(d.getTime()) ? null : d.getTime()
}

export function Timeseries({ block, rows }: { block: TimeseriesBlock; rows: Row[] }) {
  const fmt = useFmt()
  const blur = useBlur()
  const env = useVizEnv()
  const gid = useId().replace(/:/g, '')
  const [ref, width] = useWidth<HTMLDivElement>()
  const { tip, show, hide, hostRef } = useTooltip()
  const [hover, setHover] = useState<number | null>(null)
  const xField = block.x ?? 'date'
  const pts = rows
    .map((r) => ({ t: toTime(r[xField]), r }))
    .filter((p): p is { t: number; r: Row } => p.t != null)
    .sort((a, b) => a.t - b.t)
  const series = block.series.map((s, i) => ({ ...s, color: catColor(i) }))
  const all = pts.flatMap((p) => series.map((s) => num(p.r[s.field]))).filter((v): v is number => v != null)
  if (pts.length < 2 || !all.length) return <EmptyNote>{block.empty ?? 'Not enough history yet.'}</EmptyNote>
  if (env.size === 'S' || env.size === 'M') {
    const vals = pts.map((p) => num(p.r[series[0].field])).filter((v): v is number => v != null)
    const last = vals[vals.length - 1]
    const change = vals.length > 1 && vals[0] ? last / vals[0] - 1 : null
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className={cn('text-xl font-semibold tracking-tight', blur(block.format))}>{fmt(last, block.format === 'moneyCompact' ? 'money' : block.format)}</span>
          <span className={cn('text-xs tabular-nums', (change ?? 0) >= 0 ? 'text-positive' : 'text-negative')}>{fmt(change, 'signedPct')}</span>
        </div>
        <Sparkline values={vals} height={env.size === 'S' ? 56 : 72} signed zero={block.zero ?? ((!block.format || block.format.includes('oney')) && Math.min(...vals) >= 0)} />
      </div>
    )
  }

  const h = env.size === 'L' ? 316 : env.compact ? 200 : 260
  const m = { t: 12, r: 12, b: 24, l: 56 }
  const iw = Math.max(0, width - m.l - m.r)
  const ih = h - m.t - m.b
  let lo = Math.min(...all)
  let hi = Math.max(...all)
  // Honest axes: an amount ("how much cash", "what it's worth") starts at zero,
  // so a £400 step on £5,000 looks like the 8% it is, not a cliff. Opt out with
  // `zero: false` for rates/indices where the level itself isn't the point.
  const isAmount = !block.format || block.format === 'money' || block.format === 'moneyCompact' || block.format === 'num' || block.format === 'int'
  const zero = block.zero ?? (isAmount && lo >= 0)
  if (zero && lo > 0) lo = 0
  const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.05 || 1
  lo -= lo === 0 ? 0 : pad
  hi += pad
  const ticks = niceTicks(lo, hi, 4).filter((t) => t >= lo - pad && t <= hi + pad)
  const y0 = Math.min(lo, ticks[0])
  const y1 = Math.max(hi, ticks[ticks.length - 1])
  const t0 = pts[0].t
  const t1 = pts[pts.length - 1].t
  const X = (t: number) => m.l + ((t - t0) / (t1 - t0 || 1)) * iw
  const Y = (v: number) => m.t + ih - ((v - y0) / (y1 - y0 || 1)) * ih
  const spanDays = (t1 - t0) / 86400000
  const xLabel = (t: number) =>
    new Date(t).toLocaleDateString('en-GB', spanDays > 400 ? { month: 'short', year: '2-digit' } : spanDays > 60 ? { month: 'short' } : { day: 'numeric', month: 'short' })
  const nTicks = Math.max(2, Math.min(6, Math.floor(iw / 90)))
  const xTicks: number[] = Array.from({ length: nTicks }, (_, i) => t0 + ((t1 - t0) * i) / (nTicks - 1))
  const axisFmt = block.format?.includes('oney') ? 'moneyCompact' : block.format
  const step = (block.curve ?? (isStepSeries(pts.map((p) => num(p.r[series[0].field]))) ? 'step' : 'linear')) === 'step'

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const box = (e.currentTarget as SVGRectElement).getBoundingClientRect()
    const t = t0 + ((e.clientX - box.left) / box.width) * (t1 - t0)
    let best = 0
    for (let i = 1; i < pts.length; i++) if (Math.abs(pts[i].t - t) < Math.abs(pts[best].t - t)) best = i
    setHover(best)
    const p = pts[best]
    show(e, {
      title: new Date(p.t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }),
      rows: series.map((s) => ({ label: s.label, value: fmt(p.r[s.field], block.format), color: s.color, line: true, money: block.format?.includes('oney') })),
    })
  }

  return (
    <div ref={hostRef} className="relative">
      {series.length > 1 ? (
        <div className="mb-3">
          <Legend items={series.map((s) => ({ label: s.label, color: s.color, line: true }))} />
        </div>
      ) : null}
      <div ref={ref} className="w-full">
        {width > 0 ? (
          <svg width={width} height={h} role="img" aria-label={block.title ?? 'Time series'}>
            <defs>
              {series.map((s, i) => (
                <linearGradient key={i} id={`${gid}-g${i}`} x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor={s.color} stopOpacity={0.18} />
                  <stop offset="100%" stopColor={s.color} stopOpacity={0.02} />
                </linearGradient>
              ))}
            </defs>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={m.l} x2={width - m.r} y1={Y(t)} y2={Y(t)} stroke="var(--viz-grid)" />
                <text x={m.l - 8} y={Y(t)} dy="0.32em" textAnchor="end" className={cn('viz-axis-text', blur(block.format))}>
                  {fmt(t, axisFmt)}
                </text>
              </g>
            ))}
            {xTicks.map((t, i) => (
              <text key={i} x={X(t)} y={h - 6} textAnchor={i === 0 ? 'start' : i === xTicks.length - 1 ? 'end' : 'middle'} className="viz-axis-text">
                {xLabel(t)}
              </text>
            ))}
            {series.map((s, si) => {
              const valid = pts.filter((p) => num(p.r[s.field]) != null)
              if (valid.length < 2) return null
              // Balances move in jumps (a deposit lands on a day): draw steps, not ramps.
              const d = valid
                .map((p, j) => {
                  const x = X(p.t).toFixed(1)
                  const y = Y(num(p.r[s.field])!).toFixed(1)
                  if (!j) return `M${x},${y}`
                  return step ? `H${x} V${y}` : `L${x},${y}`
                })
                .join(' ')
              const area = block.style !== 'line' && si === 0
              return (
                <g key={s.field}>
                  {area ? <path d={`${d} L${X(valid[valid.length - 1].t).toFixed(1)},${Y(y0)} L${X(valid[0].t).toFixed(1)},${Y(y0)} Z`} fill={`url(#${gid}-g${si})`} /> : null}
                  <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" opacity={si === 0 ? 1 : 0.85} />
                </g>
              )
            })}
            {/* end label on the lead series */}
            {(() => {
              const s = series[0]
              const last = [...pts].reverse().find((p) => num(p.r[s.field]) != null)
              if (!last) return null
              const ly = Y(num(last.r[s.field])!)
              return (
                <g pointerEvents="none">
                  <circle cx={X(last.t)} cy={ly} r={4} fill={s.color} stroke="var(--viz-surface)" strokeWidth={2} />
                  {hover == null ? (
                    <text x={X(last.t) - 8} y={ly < m.t + 16 ? ly + 18 : ly - 10} textAnchor="end" className={cn('viz-axis-text', blur(block.format))} style={{ fill: 'var(--foreground)', fontWeight: 500 }}>
                      {fmt(last.r[s.field], block.format === 'moneyCompact' ? 'money' : block.format)}
                    </text>
                  ) : null}
                </g>
              )
            })()}
            {hover != null ? (
              <g pointerEvents="none">
                <line x1={X(pts[hover].t)} x2={X(pts[hover].t)} y1={m.t} y2={m.t + ih} stroke="var(--viz-axis)" />
                {series.map((s) => {
                  const v = num(pts[hover].r[s.field])
                  return v == null ? null : <circle key={s.field} cx={X(pts[hover].t)} cy={Y(v)} r={4} fill={s.color} stroke="var(--viz-surface)" strokeWidth={2} />
                })}
              </g>
            ) : null}
            <rect x={m.l} y={m.t} width={iw} height={ih} fill="transparent" onPointerMove={onMove} onPointerLeave={() => { setHover(null); hide() }} />
          </svg>
        ) : null}
      </div>
      <Tooltip tip={tip} hostWidth={width} />
    </div>
  )
}
