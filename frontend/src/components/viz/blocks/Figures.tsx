import { useId } from 'react'

import { cn } from '@/lib/utils'
import { useBlur, useFmt, useVizEnv, useWidth } from '../shared'
import type { HeroBlock, KpisBlock, MeterBlock, VizFormat } from '../types'
import { isStepSeries } from './step'

const SIGNED = new Set<VizFormat | undefined>(['signedMoney', 'signedPct'])

function tone(v: number | null, format?: VizFormat) {
  if (v == null || !SIGNED.has(format) || v === 0) return ''
  return v > 0 ? 'text-positive' : 'text-negative'
}

/** De-emphasised area sparkline; the end point carries the accent. */
export function Sparkline({ values, height = 44, signed = false, zero = false }: { values: number[]; height?: number; signed?: boolean; zero?: boolean }) {
  const gid = useId().replace(/:/g, '')
  const [ref, width] = useWidth<HTMLDivElement>()
  if (values.length < 2) return <div ref={ref} />
  // Amounts sit on a zero baseline: a small step must not read as a cliff.
  const lo = zero ? Math.min(0, ...values) : Math.min(...values)
  const hi = Math.max(...values)
  const X = (i: number) => 2 + (i / (values.length - 1)) * (width - 6)
  const Y = (v: number) => height - 3 - ((v - lo) / (hi - lo || 1)) * (height - 8)
  const step = isStepSeries(values)
  const d = values.map((v, i) => (i ? (step ? `H${X(i).toFixed(1)} V${Y(v).toFixed(1)}` : `L${X(i).toFixed(1)},${Y(v).toFixed(1)}`) : `M${X(i).toFixed(1)},${Y(v).toFixed(1)}`)).join(' ')
  const up = values[values.length - 1] >= values[0]
  // Direction colour only where up/down means good/bad; otherwise the accent.
  const color = signed ? (up ? 'var(--viz-gain)' : 'var(--viz-loss)') : 'var(--viz-accent)'
  return (
    <div ref={ref} className="w-full" aria-hidden>
      {width > 0 ? (
        <svg width={width} height={height}>
          <defs>
            <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.16} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <path d={`${d} L${X(values.length - 1)},${height} L${X(0)},${height} Z`} fill={`url(#${gid})`} />
          <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          <circle cx={X(values.length - 1)} cy={Y(values[values.length - 1])} r={3.5} fill={color} stroke="var(--viz-surface)" strokeWidth={2} />
        </svg>
      ) : null}
    </div>
  )
}

/** The one number a page leads with — the answer. Label, figure, then what the figure means. */
export function Hero({ block, value, delta, trend, name }: { block: HeroBlock; value: number | null; delta: number | null; trend?: number[]; name?: string }) {
  const fmt = useFmt()
  const blur = useBlur()
  const env = useVizEnv()
  const small = env.size === 'S' || env.size === 'M'
  return (
    <div className="flex flex-col">
      <p className={cn('truncate font-medium text-muted-foreground', small ? 'text-xs' : 'text-[13px]')}>
        {block.label}
        {name ? <span className="text-foreground"> · {name}</span> : null}
      </p>
      <p className={cn('mt-2 font-semibold', small ? 'text-[30px] leading-none tracking-tight' : 'viz-hero-value', tone(value, block.format), blur(block.format))}>
        {fmt(value, block.format)}
      </p>
      {block.delta ? (
        <p className={cn('leading-snug', small ? 'mt-1.5 truncate text-xs' : 'mt-4 text-[15px]')}>
          <span className={cn('font-medium tabular-nums', tone(delta, block.deltaFormat) || 'text-foreground', blur(block.deltaFormat))}>{fmt(delta, block.deltaFormat)}</span>
          {block.deltaLabel ? <span className="text-muted-foreground"> {block.deltaLabel}</span> : null}
        </p>
      ) : null}
      {block.caption && env.size !== 'S' ? (
        <p className={cn('max-w-[56ch] text-muted-foreground', small ? 'mt-1 line-clamp-2 text-xs' : 'mt-2 text-[13px] leading-relaxed')}>{block.caption}</p>
      ) : null}
      {trend && trend.length > 1 ? (
        <div className={small ? 'mt-2' : 'mt-6'}>
          <Sparkline values={trend} height={env.size === 'L' ? 150 : small ? 40 : 56} zero={!block.format || block.format === 'money' || block.format === 'moneyCompact'} />
        </div>
      ) : null}
    </div>
  )
}

/** One amount against a limit, with an optional pace marker. */
export function Meter({ block, value, max, mark }: { block: MeterBlock; value: number | null; max: number | null; mark: number | null }) {
  const fmt = useFmt()
  const blur = useBlur()
  const env = useVizEnv()
  const share = value != null && max ? Math.max(0, Math.min(1, value / max)) : 0
  const ahead = mark != null ? share >= mark : true
  return (
    <div className="flex flex-col">
      <p className={cn('font-medium text-muted-foreground', env.size ? 'text-xs' : 'text-[13px]')}>{block.label}</p>
      <div className="mt-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className={cn('font-semibold', env.size ? 'text-[28px] leading-none tracking-tight' : 'viz-hero-value', blur(block.format))}>{fmt(value, block.format)}</p>
        <p className={cn('text-muted-foreground', env.size ? 'text-xs' : 'text-[15px]')}>
          <span className="font-medium text-foreground tabular-nums">{fmt(share, 'pct')}</span> used of{' '}
          <span className={cn('font-medium text-foreground', blur(block.format))}>{fmt(max, block.format)}</span>
        </p>
      </div>
      <div
        className={cn('relative overflow-visible rounded-full', env.size ? 'mt-3 h-2' : 'mt-5 h-2.5')}
        style={{ background: 'color-mix(in oklab, var(--viz-accent) 16%, var(--viz-mid))' }}
        role="meter"
        aria-valuemin={0}
        aria-valuemax={max ?? 0}
        aria-valuenow={value ?? 0}
        aria-label={block.label}
      >
        <div className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-500" style={{ width: `${share * 100}%`, background: 'var(--viz-accent)' }} />
        {mark != null ? (
          <div
            className="absolute -top-1.5 -bottom-1.5 w-0.5 rounded-full bg-foreground/70"
            style={{ left: `calc(${Math.max(0, Math.min(1, mark)) * 100}% - 1px)` }}
            title={block.markLabel}
          />
        ) : null}
      </div>
      {mark != null && block.markLabel && env.size !== 'S' ? (
        <p className={cn('text-muted-foreground', env.size ? 'mt-2 text-xs' : 'mt-3 text-[13px]')}>
          <span className="mr-1.5 inline-block h-3 w-0.5 translate-y-0.5 rounded-full bg-foreground/70" />
          {block.markLabel} ({fmt(mark, 'pct')}) — you’re {ahead ? 'ahead of' : 'behind'} an even pace.
        </p>
      ) : null}
      {block.caption && !env.size ? <p className="mt-1.5 max-w-[68ch] text-[13px] leading-relaxed text-muted-foreground">{block.caption}</p> : null}
    </div>
  )
}

/**
 * Supporting figures. On a page they sit on the canvas separated by hairlines:
 * side by side, or a stacked column when the block is narrow (the aside next
 * to a hero). In a widget they share the widget's own card.
 */
export function Kpis({ block, values, stacked = false }: { block: KpisBlock; values: { value: number | null; delta: number | null }[]; stacked?: boolean }) {
  const fmt = useFmt()
  const blur = useBlur()
  const env = useVizEnv()
  const cap = env.size === 'S' ? 1 : env.size === 'M' ? 2 : env.size === 'L' ? 4 : 5
  const items = block.items.slice(0, cap)
  const item = (it: (typeof items)[number], i: number, cls: string) => {
    const v = values[i] ?? { value: null, delta: null }
    return (
      <div key={it.label} className={cn('min-w-0', cls)}>
        <p className={cn('truncate text-muted-foreground', env.size ? 'text-xs' : 'text-[13px]')}>{it.label}</p>
        <p className={cn('mt-1.5 truncate', env.size ? 'text-2xl font-semibold tracking-tight' : 'viz-kpi-value', tone(v.value, it.format), blur(it.format))}>
          {fmt(v.value, it.format)}
        </p>
        {it.delta || it.hint ? (
          <p className={cn('mt-1 line-clamp-2 text-muted-foreground', env.size ? 'text-[11px]' : 'text-xs leading-snug')}>
            {it.delta ? (
              <span className={cn('mr-1 font-medium tabular-nums', tone(v.delta, it.deltaFormat) || 'text-foreground', blur(it.deltaFormat))}>{fmt(v.delta, it.deltaFormat)}</span>
            ) : null}
            {it.hint}
          </p>
        ) : null}
      </div>
    )
  }
  if (env.size) {
    return <div className={cn('grid gap-x-4 gap-y-3', items.length >= 2 ? 'grid-cols-2' : 'grid-cols-1')}>{items.map((it, i) => item(it, i, ''))}</div>
  }
  if (stacked) {
    // Beside the hero on desktop: a stacked column. Below it on phones: a compact two-up grid.
    return (
      <div className="grid grid-cols-2 gap-x-6 gap-y-5 lg:flex lg:flex-col lg:gap-0 lg:divide-y lg:divide-border/70">
        {items.map((it, i) => item(it, i, 'lg:py-4 lg:first:pt-0 lg:last:pb-0'))}
      </div>
    )
  }
  const cols = items.length >= 4 ? 'sm:grid-cols-4' : items.length === 3 ? 'sm:grid-cols-3' : items.length === 2 ? 'sm:grid-cols-2' : ''
  return <div className={cn('grid grid-cols-2 gap-y-6', cols)}>{items.map((it, i) => item(it, i, cn('pr-4', i > 0 && 'sm:border-l sm:border-border/70 sm:pl-6')))}</div>
}
