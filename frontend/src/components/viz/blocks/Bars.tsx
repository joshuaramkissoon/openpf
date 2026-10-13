import { useMemo, useState } from 'react'

import { cn } from '@/lib/utils'
import { num } from '../engine'
import { ACCENT, Breadcrumb, EmptyNote, fieldLabel, isSigned, NEUTRAL, prettyLabel, shortLabel, Tooltip, useBlur, useFmt, useTooltip, useVizEnv, useWidth } from '../shared'
import type { BarsBlock, Detail, Row } from '../types'
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

let measureCtx: CanvasRenderingContext2D | null | undefined
/** Rendered width of a label in the bar list's type (14px, medium). */
function textWidth(s: string): number {
  if (measureCtx === undefined) measureCtx = typeof document !== 'undefined' ? document.createElement('canvas').getContext('2d') : null
  if (!measureCtx) return s.length * 7.4
  measureCtx.font = `500 14px ${getComputedStyle(document.body).fontFamily || 'sans-serif'}`
  return measureCtx.measureText(s).width
}

/** Tooltip rows for a chart's extra `details` columns. */
export function detailRows(details: Detail[] | undefined, r: Row, fmt: ReturnType<typeof useFmt>) {
  return (details ?? [])
    .filter((d) => r[d.field] != null && r[d.field] !== '')
    .map((d) => ({ label: d.label || fieldLabel(d.field), value: d.format === 'text' || !d.format ? prettyLabel(r[d.field]) : fmt(r[d.field], d.format), money: !!d.format?.toLowerCase().includes('money') }))
}

export function Bars({ block, rows }: { block: BarsBlock; rows: Row[] }) {
  const fmt = useFmt()
  const blur = useBlur()
  const env = useVizEnv()
  const [ref, width] = useWidth<HTMLDivElement>()
  const { tip, show, hide, hostRef } = useTooltip()
  const [drill, setDrill] = useState<Row | null>(null)
  // Vertical bars are time buckets: a limit keeps the latest, not the oldest.
  const valued = rows.filter((r) => num(r[block.value]) != null)
  const data = block.orientation === 'vertical' ? valued.slice(-(block.limit ?? 12)) : valued.slice(0, block.limit ?? 12)
  // Label column sized to its content (never a fixed gutter): as wide as the
  // longest label, capped at ~38% of the chart; long names shorten, then wrap.
  const labelKey = data.map((r) => String(r[block.label])).join('|')
  const labelCol = useMemo(() => {
    if (!width) return { px: 112, short: false }
    const cap = Math.max(88, Math.round(width * (env.size === 'S' ? 0.3 : 0.38)))
    const full = Math.max(0, ...data.map((r) => textWidth(prettyLabel(r[block.label]))))
    if (full + 6 <= cap) return { px: Math.max(40, Math.ceil(full + 6)), short: false }
    const short = Math.max(0, ...data.map((r) => textWidth(shortLabel(prettyLabel(r[block.label])))))
    return { px: Math.max(40, Math.min(cap, Math.ceil(short + 6))), short: true }
  }, [width, labelKey, env.size]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!data.length || data.every((r) => num(r[block.value]) === 0)) return <EmptyNote>{block.empty ?? 'Nothing to show yet.'}</EmptyNote>
  const signed = isSigned(data, block.value)
  // Emphasis, not decoration: one accent for the bar(s) the chart is about,
  // neutral for the rest. Signed bars keep gain/loss — the sign is the point.
  const vals = data.map((r) => num(r[block.value]) ?? 0)
  const highlight = block.highlight ?? (block.orientation === 'vertical' ? 'all' : 'max')
  const pick =
    highlight === 'max' ? vals.indexOf(Math.max(...vals)) : highlight === 'min' ? vals.indexOf(Math.min(...vals)) : highlight === 'first' ? 0 : highlight === 'last' ? vals.length - 1 : -1
  const emphasised = (i: number) => highlight === 'all' || i === pick
  const colorOf = (v: number, i = -1) => (signed ? (v >= 0 ? 'var(--viz-gain)' : 'var(--viz-loss)') : emphasised(i) ? ACCENT : NEUTRAL)
  const valueHead = block.valueLabel || fieldLabel(block.value)
  const secondaryHead = block.secondary ? block.secondaryLabel || fieldLabel(block.secondary) : null
  const labelOf = (r: Row) => prettyLabel(r[block.label])
  const shownLabel = (r: Row) => (labelCol.short ? shortLabel(labelOf(r)) : labelOf(r))
  const click = (r: Row) => {
    if (Array.isArray(r.items) && (r.items as Row[]).length) setDrill(r)
    else if (typeof r.ticker === 'string' && !r.__other) env.openTicker(r.ticker)
  }
  const tipFor = (r: Row) => ({
    title: String(r.name && r.name !== r[block.label] ? `${labelOf(r)} · ${r.name}` : labelOf(r)),
    rows: [
      { label: valueHead, value: fmt(r[block.value], block.format), color: colorOf(num(r[block.value]) ?? 0, data.indexOf(r)), money: block.format?.toLowerCase().includes('money') },
      ...(block.secondary && block.secondary !== block.value ? [{ label: secondaryHead ?? '', value: fmt(r[block.secondary], block.secondaryFormat), money: !!block.secondaryFormat?.toLowerCase().includes('money') }] : []),
      ...detailRows(block.details, r, fmt),
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
    const m = { t: 20, r: 4, b: 26, l: 48 }
    const peak = values.reduce((best, v, i) => (Math.abs(v) > Math.abs(values[best]) ? i : best), 0)
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
                    <path d={path} fill={v === 0 ? 'var(--viz-mid)' : colorOf(v, i)} pointerEvents="none" />
                    {/* Selective direct label: the peak (and the latest bar), never every cap. */}
                    {v !== 0 && (i === peak || i === data.length - 1) ? (
                      <text x={x + bw / 2} y={v >= 0 ? top - 6 : top + bh + 13} textAnchor="middle" className={cn('viz-axis-text', blur(block.format))} style={{ fill: 'var(--foreground)', fontWeight: 500 }}>
                        {fmt(v, block.format)}
                      </text>
                    ) : null}
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

  // Diverging bars with room to spare: labels sit at the zero line on the side
  // opposite their bar and values ride the bar end, so nothing floats in a gap.
  const PAD = block.secondary ? 112 : 64
  const inner = Math.max(0, width - PAD * 2)
  const zeroX = PAD + negShare * inner
  const scale = inner / (posMax + negMax || 1)
  const axisLabels =
    signed && !env.size && width > 0 && zeroX - 10 >= labelCol.px && (negMax === 0 || width - zeroX - 10 >= labelCol.px) && !labelCol.short
  if (axisLabels) {
    return (
      <div ref={hostRef} className="relative">
        <div ref={ref} className="relative">
          <span className="pointer-events-none absolute inset-y-0 w-px bg-[var(--viz-axis)]" style={{ left: zeroX }} aria-hidden />
          {data.map((r, i) => {
            const v = values[i]
            const w = Math.max(v === 0 ? 0 : 2, Math.abs(v) * scale)
            const pos = v >= 0
            const drillable = Array.isArray(r.items) || (typeof r.ticker === 'string' && !r.__other)
            const sec = block.secondary ? r[block.secondary] : null
            return (
              <button
                key={`${labelOf(r)}-${i}`}
                type="button"
                disabled={!drillable}
                onClick={() => click(r)}
                onPointerMove={(e) => show(e, tipFor(r))}
                onPointerLeave={hide}
                className={cn('relative block h-8 w-full rounded-md text-left text-sm', drillable && 'hover:bg-muted/50')}
              >
                <span
                  className="absolute top-1/2 -translate-y-1/2 truncate font-medium"
                  style={pos ? { right: width - zeroX + 10, maxWidth: zeroX - 10, textAlign: 'right' } : { left: zeroX + 10, maxWidth: width - zeroX - 10 }}
                >
                  {labelOf(r)}
                </span>
                <span
                  className={cn('absolute inset-y-2', pos ? 'rounded-r-[4px]' : 'rounded-l-[4px]')}
                  style={{ left: pos ? zeroX : zeroX - w, width: w, background: v === 0 ? "var(--viz-mid)" : colorOf(v, i) }}
                />
                <span
                  className="absolute top-1/2 flex -translate-y-1/2 items-baseline gap-1.5 whitespace-nowrap"
                  style={pos ? { left: zeroX + w + 8 } : { right: width - (zeroX - w) + 8, flexDirection: 'row-reverse' }}
                >
                  <span className={cn('tabular-nums', blur(block.format))}>{fmt(v, block.format)}</span>
                  {sec != null ? <span className="text-xs tabular-nums text-muted-foreground">{fmt(sec, block.secondaryFormat)}</span> : null}
                </span>
              </button>
            )
          })}
        </div>
        <Tooltip tip={tip} hostWidth={width} />
      </div>
    )
  }
  // Fixed value columns (sized to content type) so every bar track, the zero
  // line and the column headers line up; headers say what each number is.
  const sec = !!block.secondary && env.size !== 'S'
  const narrow = width > 0 && width < 520
  const cols = `${labelCol.px}px minmax(0,1fr) ${env.size === 'S' ? '4.5rem' : narrow ? '4.75rem' : '5.75rem'}${sec ? (narrow ? ' 5.5rem' : ' 6.25rem') : ''}`
  const heads = !env.size && (sec || !!block.valueLabel)
  return (
    <div ref={hostRef} className="relative">
      <div ref={ref} className={env.size ? 'space-y-1' : 'space-y-0.5'}>
        {heads ? (
          <div className="grid items-end gap-3 px-1 pb-1.5" style={{ gridTemplateColumns: cols }} aria-hidden>
            <span />
            <span />
            <span className="viz-colhead text-right">{valueHead}</span>
            {sec ? <span className="viz-colhead text-right">{secondaryHead}</span> : null}
          </div>
        ) : null}
        {data.map((r, i) => {
          const v = values[i]
          const pct = (Math.abs(v) / (signed ? posMax + negMax || 1 : maxAbs)) * 100
          const drillable = Array.isArray(r.items) || (typeof r.ticker === 'string' && !r.__other)
          const sv = block.secondary ? num(r[block.secondary]) : null
          const signedSec = block.secondaryFormat === 'signedPct' || block.secondaryFormat === 'signedMoney' || !block.secondaryFormat
          return (
            <button
              key={`${labelOf(r)}-${i}`}
              type="button"
              disabled={!drillable}
              onClick={() => click(r)}
              onPointerMove={(e) => show(e, tipFor(r))}
              onPointerLeave={hide}
              className={cn('grid w-full items-center gap-3 rounded-md px-1 text-left text-sm', env.size ? 'py-1' : 'py-1.5', drillable && 'hover:bg-muted/50')}
              style={{ gridTemplateColumns: cols }}
            >
              <span className="line-clamp-2 min-w-0 break-words font-medium leading-tight" title={labelOf(r)}>
                {shownLabel(r)}
              </span>
              <span className={cn('relative h-3.5', env.size === 'S' && 'invisible')}>
                {signed ? <span className="absolute inset-y-[-4px] w-px bg-[var(--viz-axis)]" style={{ left: `${negShare * 100}%` }} /> : null}
                <span
                  className={cn('absolute inset-y-0', v >= 0 ? 'rounded-r-[4px]' : 'rounded-l-[4px]')}
                  style={
                    signed
                      ? v >= 0
                        ? { left: `${negShare * 100}%`, width: `${pct}%`, background: colorOf(v, i) }
                        : { right: `${(1 - negShare) * 100}%`, width: `${pct}%`, background: colorOf(v, i) }
                      : { left: 0, width: `${pct}%`, background: colorOf(v, i) }
                  }
                />
              </span>
              <span className={cn('text-right tabular-nums', emphasised(i) || signed ? 'text-foreground' : 'text-foreground/80', blur(block.format))}>{fmt(v, block.format)}</span>
              {sec ? (
                <span className={cn('text-right text-xs tabular-nums', signedSec && sv ? (sv > 0 ? 'text-positive' : 'text-negative') : 'text-muted-foreground')}>
                  {fmt(r[block.secondary!], block.secondaryFormat)}
                </span>
              ) : null}
            </button>
          )
        })}
      </div>
      <Tooltip tip={tip} hostWidth={width} />
    </div>
  )
}
