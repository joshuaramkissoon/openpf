import { useState } from 'react'

import { cn } from '@/lib/utils'
import { num } from '../engine'
import { Breadcrumb, EmptyNote, isSigned, prettyLabel, Tooltip, useBlur, useFmt, useTooltip, useVizEnv, useWidth } from '../shared'
import type { BarsBlock, Row } from '../types'
import { DrillList } from './DrillList'

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1
    min -= pad
    max += pad
  }
  const span = max - min
  let step = Math.pow(10, Math.floor(Math.log10(span / count)))
  const err = (count * step) / span
  if (err <= 0.15) step *= 10
  else if (err <= 0.35) step *= 5
  else if (err <= 0.75) step *= 2
  const lo = Math.floor(min / step) * step
  const hi = Math.ceil(max / step) * step
  const out: number[] = []
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Math.abs(v) < step / 1e6 ? 0 : v)
  return out
}
export { niceTicks }

export function Bars({ block, rows }: { block: BarsBlock; rows: Row[] }) {
  const fmt = useFmt()
  const blur = useBlur()
  const env = useVizEnv()
  const [ref, width] = useWidth<HTMLDivElement>()
  const { tip, show, hide, hostRef } = useTooltip()
  const [drill, setDrill] = useState<Row | null>(null)
  const data = rows.filter((r) => num(r[block.value]) != null).slice(0, block.limit ?? 12)
  if (!data.length || data.every((r) => num(r[block.value]) === 0)) return <EmptyNote>{block.empty ?? 'Nothing to show yet.'}</EmptyNote>
  const signed = isSigned(data, block.value)
  const colorOf = (v: number) => (signed ? (v >= 0 ? 'var(--viz-gain)' : 'var(--viz-loss)') : 'var(--viz-1)')
  const labelOf = (r: Row) => prettyLabel(r[block.label])
  const click = (r: Row) => {
    if (Array.isArray(r.items) && (r.items as Row[]).length) setDrill(r)
    else if (typeof r.ticker === 'string' && !r.__other) env.openTicker(r.ticker)
  }
  const tipFor = (r: Row) => ({
    title: String(r.name && r.name !== r[block.label] ? `${labelOf(r)} · ${r.name}` : labelOf(r)),
    rows: [
      { label: block.title ?? block.value, value: fmt(r[block.value], block.format), color: colorOf(num(r[block.value]) ?? 0), money: block.format?.toLowerCase().includes('money') },
      ...(block.secondary ? [{ label: block.secondary.replace(/_/g, ' '), value: fmt(r[block.secondary], block.secondaryFormat) }] : []),
    ],
    note: Array.isArray(r.items) ? 'Click to see holdings' : undefined,
  })

  if (drill) {
    return (
      <div>
        <Breadcrumb trail={['All', labelOf(drill)]} onBack={() => setDrill(null)} />
        <DrillList rows={drill.items as Row[]} valueField={(drill.items as Row[]).some((r) => num(r[block.value]) != null) ? block.value : 'value'} format={block.format} />
      </div>
    )
  }

  if (block.orientation === 'vertical') {
    const values = data.map((r) => num(r[block.value]) ?? 0)
    const ticks = niceTicks(Math.min(0, ...values), Math.max(0, ...values))
    const y0 = ticks[0]
    const y1 = ticks[ticks.length - 1]
    const h = env.compact ? 200 : 240
    const m = { t: 8, r: 4, b: 26, l: 48 }
    const iw = Math.max(0, width - m.l - m.r)
    const ih = h - m.t - m.b
    const Y = (v: number) => m.t + ih - ((v - y0) / (y1 - y0)) * ih
    const band = iw / data.length
    const bw = Math.min(24, band * 0.62)
    const every = Math.ceil((data.length * 44) / Math.max(1, iw))
    return (
      <div ref={hostRef} className="relative">
        <div ref={ref} className="w-full">
          {width > 0 ? (
            <svg width={width} height={h} role="img" aria-label={block.title ?? 'Bar chart'}>
              {ticks.map((t) => (
                <g key={t}>
                  <line x1={m.l} x2={width - m.r} y1={Y(t)} y2={Y(t)} stroke={t === 0 ? 'var(--viz-axis)' : 'var(--viz-grid)'} />
                  <text x={m.l - 8} y={Y(t)} dy="0.32em" textAnchor="end" className={cn('viz-axis-text', blur(block.format))}>
                    {fmt(t, block.format?.includes('oney') ? 'moneyCompact' : block.format)}
                  </text>
                </g>
              ))}
              {data.map((r, i) => {
                const v = values[i]
                const x = m.l + band * i + (band - bw) / 2
                const top = Math.min(Y(v), Y(0))
                const bh = Math.max(1, Math.abs(Y(v) - Y(0)))
                const rad = Math.min(4, bh / 2, bw / 2)
                const path = v >= 0
                  ? `M${x},${top + bh} V${top + rad} Q${x},${top} ${x + rad},${top} H${x + bw - rad} Q${x + bw},${top} ${x + bw},${top + rad} V${top + bh} Z`
                  : `M${x},${top} V${top + bh - rad} Q${x},${top + bh} ${x + rad},${top + bh} H${x + bw - rad} Q${x + bw},${top + bh} ${x + bw},${top + bh - rad} V${top} Z`
                return (
                  <g key={i}>
                    <rect x={m.l + band * i} y={m.t} width={band} height={ih} fill="transparent" onPointerMove={(e) => show(e, tipFor(r))} onPointerLeave={hide} onClick={() => click(r)} />
                    <path d={path} fill={v === 0 ? 'var(--viz-mid)' : colorOf(v)} pointerEvents="none" />
                    {i % every === 0 ? (
                      <text x={x + bw / 2} y={h - 8} textAnchor="middle" className="viz-axis-text">
                        {labelOf(r)}
                      </text>
                    ) : null}
                  </g>
                )
              })}
            </svg>
          ) : null}
        </div>
        <Tooltip tip={tip} hostWidth={width} />
      </div>
    )
  }

  // Horizontal ranked bars; signed values diverge from a centre zero line.
  const values = data.map((r) => num(r[block.value]) ?? 0)
  const maxAbs = Math.max(...values.map(Math.abs)) || 1
  const posMax = Math.max(0, ...values)
  const negMax = Math.max(0, ...values.map((v) => -v))
  const negShare = signed ? negMax / (posMax + negMax || 1) : 0
  return (
    <div ref={hostRef} className="relative">
      <div ref={ref} className="space-y-1">
        {data.map((r, i) => {
          const v = values[i]
          const pct = (Math.abs(v) / (signed ? posMax + negMax || 1 : maxAbs)) * 100
          const drillable = Array.isArray(r.items) || (typeof r.ticker === 'string' && !r.__other)
          return (
            <button
              key={`${labelOf(r)}-${i}`}
              type="button"
              disabled={!drillable}
              onClick={() => click(r)}
              onPointerMove={(e) => show(e, tipFor(r))}
              onPointerLeave={hide}
              className={cn(
                'grid w-full items-center gap-3 rounded-md px-1 py-1 text-left text-sm',
                // Fixed value column so every row's bar track (and the zero line) lines up.
                block.secondary ? 'grid-cols-[minmax(56px,128px)_1fr_9.5rem]' : 'grid-cols-[minmax(56px,128px)_1fr_6rem]',
                env.size === 'S' && 'grid-cols-[minmax(40px,64px)_1fr_4.5rem]',
                drillable && 'hover:bg-muted/50',
              )}
            >
              <span className="min-w-0 truncate font-medium">{labelOf(r)}</span>
              <span className={cn('relative h-4', env.size === 'S' && 'invisible')}>
                {signed ? <span className="absolute inset-y-[-3px] w-px bg-[var(--viz-axis)]" style={{ left: `${negShare * 100}%` }} /> : null}
                <span
                  className={cn('absolute inset-y-0', v >= 0 ? 'rounded-r-[4px]' : 'rounded-l-[4px]')}
                  style={
                    signed
                      ? v >= 0
                        ? { left: `${negShare * 100}%`, width: `${pct}%`, background: colorOf(v) }
                        : { right: `${(1 - negShare) * 100}%`, width: `${pct}%`, background: colorOf(v) }
                      : { left: 0, width: `${pct}%`, background: colorOf(v) }
                  }
                />
              </span>
              <span className="flex items-baseline justify-end gap-2 text-right">
                <span className={cn('tabular-nums', blur(block.format))}>{fmt(v, block.format)}</span>
                {block.secondary && env.size !== 'S' ? (
                  <span className={cn('w-14 text-xs tabular-nums', block.secondaryFormat === 'signedPct' || block.secondaryFormat === 'signedMoney' || !block.secondaryFormat ? ((num(r[block.secondary]) ?? 0) >= 0 ? 'text-positive' : 'text-negative') : 'text-muted-foreground')}>{fmt(r[block.secondary], block.secondaryFormat)}</span>
                ) : null}
              </span>
            </button>
          )
        })}
      </div>
      <Tooltip tip={tip} hostWidth={width} />
    </div>
  )
}
