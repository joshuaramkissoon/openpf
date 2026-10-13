import { useState } from 'react'

import { cn } from '@/lib/utils'
import { num } from '../engine'
import { Breadcrumb, catColor, EmptyNote, prettyLabel, Tooltip, useBlur, useFmt, useTooltip, useVizEnv, useWidth } from '../shared'
import type { AllocationBlock, Row } from '../types'
import { detailRows } from './Bars'
import { DrillList } from './DrillList'

type Slice = { label: string; value: number; color: string; row: Row; other: boolean }

export function slicesFor(block: AllocationBlock, rows: Row[], maxSlices = 7): Slice[] {
  const sorted = rows
    .map((r) => ({ row: r, value: Math.max(0, num(r[block.value]) ?? 0), label: prettyLabel(r[block.label] ?? 'Other') }))
    .filter((s) => s.value > 0)
    .sort((a, b) => Number(a.row.__other ?? 0) - Number(b.row.__other ?? 0) || b.value - a.value)
  let head = sorted
  let tail: typeof sorted = []
  if (sorted.length > maxSlices) {
    head = sorted.slice(0, maxSlices - 1)
    tail = sorted.slice(maxSlices - 1)
  }
  const out: Slice[] = head.map((s, i) => ({ ...s, color: catColor(i, !!s.row.__other || s.label === 'Other'), other: !!s.row.__other || s.label === 'Other' }))
  if (tail.length) {
    const items = tail.flatMap((t) => (Array.isArray(t.row.items) ? (t.row.items as Row[]) : [t.row]))
    out.push({ label: 'Other', value: tail.reduce((a, t) => a + t.value, 0), color: 'var(--viz-other)', row: { label: 'Other', items }, other: true })
  }
  return out
}

export function Allocation({ block, rows }: { block: AllocationBlock; rows: Row[] }) {
  const fmt = useFmt()
  const blur = useBlur()
  const env = useVizEnv()
  const [ref, width] = useWidth<HTMLDivElement>()
  const { tip, show, hide, hostRef } = useTooltip()
  const [drill, setDrill] = useState<Slice | null>(null)
  const slices = slicesFor(block, rows, env.size === 'S' ? 4 : block.style === 'donut' ? 6 : 7)
  const legendCap = env.size === 'S' ? 3 : env.size === 'M' ? 4 : env.size === 'L' ? 6 : 99
  if (!slices.length) return <EmptyNote>{block.empty ?? 'Nothing to show yet.'}</EmptyNote>
  const total = slices.reduce((a, s) => a + s.value, 0)

  const open = (s: Slice) => {
    if (Array.isArray(s.row.items) && (s.row.items as Row[]).length) setDrill(s)
    else if (typeof s.row.ticker === 'string' && !s.other) env.openTicker(s.row.ticker)
  }
  const tipFor = (s: Slice) => ({
    title: s.label,
    rows: [
      { label: 'Value', value: fmt(s.value, block.format), color: s.color, money: true },
      { label: 'Share', value: fmt(s.value / total, 'pct') },
      ...(num(s.row.day_change_pct) != null ? [{ label: 'Today', value: fmt(s.row.day_change_pct, 'signedPct') }] : []),
      ...(typeof s.row.count === 'number' ? [{ label: 'Holdings', value: String(s.row.count) }] : []),
      ...detailRows(block.details, s.row, fmt),
    ].filter((x, i, arr) => arr.findIndex((y) => y.label === x.label) === i),
    note: Array.isArray(s.row.items) ? 'Click to see holdings' : undefined,
  })

  if (slices.length === 1 && !drill) {
    // One slice is not a chart: say it plainly.
    const s = slices[0]
    return (
      <div className="flex flex-col gap-3 py-2">
        <p className="text-3xl font-semibold tracking-tight">
          100% <span className="text-muted-foreground">{s.label}</span>
        </p>
        <div className="h-2.5 rounded-full" style={{ background: s.color }} />
        <p className={cn('text-sm text-muted-foreground', blur(block.format))}>{fmt(s.value, block.format)} — everything here is in one bucket.</p>
      </div>
    )
  }

  if (drill) {
    return (
      <div>
        <Breadcrumb trail={['All', drill.label]} onBack={() => setDrill(null)} />
        <DrillList rows={drill.row.items as Row[]} valueField={block.value} format={block.format} />
      </div>
    )
  }

  const legend = (
    <ul className={cn('grid gap-y-0.5', block.style === 'donut' && !env.size ? 'min-w-0 flex-1' : env.size === 'S' ? 'mt-3' : env.size ? 'mt-4 gap-x-6 sm:grid-cols-2' : 'mt-5')}>
      {env.size ? null : (
        <li className="flex items-end gap-2 px-1.5 pb-1" aria-hidden>
          <span className="flex-1" />
          <span className="viz-colhead">Value</span>
          <span className="viz-colhead w-16 text-right">% of total</span>
        </li>
      )}
      {slices.slice(0, legendCap).map((s) => {
        const drillable = Array.isArray(s.row.items) || (typeof s.row.ticker === 'string' && !s.other)
        return (
          <li key={s.label}>
            <button
              type="button"
              disabled={!drillable}
              onClick={() => open(s)}
              className={cn('flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-sm', drillable && 'hover:bg-muted/60')}
            >
              <span className="size-2.5 shrink-0 rounded-[3px]" style={{ background: s.color }} />
              <span className="min-w-0 flex-1 truncate">{s.label}</span>
              {env.size ? null : <span className={cn('tabular-nums text-muted-foreground', blur(block.format))}>{fmt(s.value, block.format)}</span>}
              <span className={cn('text-right font-medium tabular-nums', env.size ? 'w-12' : 'w-16')}>{fmt(s.value / total, 'pct')}</span>
            </button>
          </li>
        )
      })}
    </ul>
  )

  if (block.style === 'donut' && !(env.size === 'S' || env.size === 'M')) {
    const size = 168
    const r = 70
    const stroke = 18
    const circ = 2 * Math.PI * r
    const gapLen = slices.length > 1 ? 2 : 0
    let acc = 0
    return (
      <div ref={hostRef} className="relative flex flex-wrap items-center gap-6">
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0" role="img" aria-label={block.title ?? 'Allocation'}>
          <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
            {slices.map((s) => {
              const len = (s.value / total) * circ
              const el = (
                <circle
                  key={s.label}
                  cx={size / 2}
                  cy={size / 2}
                  r={r}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={stroke}
                  strokeDasharray={`${Math.max(0, len - gapLen)} ${circ - Math.max(0, len - gapLen)}`}
                  strokeDashoffset={-acc}
                  className="cursor-pointer transition-opacity hover:opacity-85"
                  onPointerMove={(e) => show(e, tipFor(s))}
                  onPointerLeave={hide}
                  onClick={() => open(s)}
                />
              )
              acc += len
              return el
            })}
          </g>
          <text x="50%" y="47%" textAnchor="middle" className={cn('fill-foreground text-[15px] font-semibold', blur(block.format))}>
            {fmt(total, 'moneyCompact')}
          </text>
          <text x="50%" y="59%" textAnchor="middle" className="fill-muted-foreground text-[11px]">
            total
          </text>
        </svg>
        {legend}
        <Tooltip tip={tip} hostWidth={width || 400} />
        <div ref={ref} className="absolute inset-x-0 top-0 h-0" />
      </div>
    )
  }

  // 100% stacked bar: 2px surface gaps, 4px rounded outer ends.
  return (
    <div ref={hostRef} className="relative">
      <div ref={ref} className="flex h-6 w-full gap-[2px]" role="img" aria-label={block.title ?? 'Allocation'}>
        {slices.map((s, i) => (
          <button
            key={s.label}
            type="button"
            aria-label={`${s.label}: ${fmt(s.value / total, 'pct')}`}
            onClick={() => open(s)}
            onPointerMove={(e) => show(e, tipFor(s))}
            onPointerLeave={hide}
            className={cn('h-full min-w-[3px] transition-opacity hover:opacity-85', i === 0 && 'rounded-l-[4px]', i === slices.length - 1 && 'rounded-r-[4px]')}
            style={{ flexGrow: s.value, flexBasis: 0, background: s.color }}
          />
        ))}
      </div>
      {legend}
      <Tooltip tip={tip} hostWidth={width} />
    </div>
  )
}
