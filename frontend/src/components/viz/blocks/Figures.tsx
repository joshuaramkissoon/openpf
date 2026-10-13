import { useId } from 'react'

import { cn } from '@/lib/utils'
import { useBlur, useFmt, useVizEnv, useWidth } from '../shared'
import type { HeroBlock, KpisBlock, MeterBlock, VizFormat } from '../types'

const SIGNED = new Set<VizFormat | undefined>(['signedMoney', 'signedPct'])

function tone(v: number | null, format?: VizFormat) {
  if (v == null || !SIGNED.has(format) || v === 0) return ''
  return v > 0 ? 'text-positive' : 'text-negative'
}

/** De-emphasised area sparkline; the end point carries the accent. */
export function Sparkline({ values, height = 44, signed = false }: { values: number[]; height?: number; signed?: boolean }) {
  const gid = useId().replace(/:/g, '')
  const [ref, width] = useWidth<HTMLDivElement>()
  if (values.length < 2) return <div ref={ref} />
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const X = (i: number) => 2 + (i / (values.length - 1)) * (width - 6)
  const Y = (v: number) => height - 3 - ((v - lo) / (hi - lo || 1)) * (height - 8)
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')
  const up = values[values.length - 1] >= values[0]
  // Direction colour only where up/down means good/bad; otherwise the neutral series hue.
  const color = signed ? (up ? 'var(--viz-gain)' : 'var(--viz-loss)') : 'var(--viz-1)'
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
          <circle cx={X(values.length - 1)} cy={Y(values[values.length - 1])} r={3.5} fill={color} stroke="var(--card)" strokeWidth={2} />
        </svg>
      ) : null}
    </div>
  )
}

/** The one number a page leads with. Proportional figures, same sans, ≥48px on pages. */
export function Hero({ block, value, delta, trend }: { block: HeroBlock; value: number | null; delta: number | null; trend?: number[] }) {
  const fmt = useFmt()
  const blur = useBlur()
  const env = useVizEnv()
  const small = env.size === 'S' || env.size === 'M'
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
        <div className="min-w-0">
          <p className="text-xs font-medium text-muted-foreground">{block.label}</p>
          <p
            className={cn(
              'mt-1 font-semibold leading-none tracking-tight',
              small ? 'text-[30px]' : 'text-5xl sm:text-[56px]',
              tone(value, block.format),
              blur(block.format),
            )}
          >
            {fmt(value, block.format)}
          </p>
        </div>
        {block.delta ? (
          <p className={cn('font-medium tabular-nums', small ? 'text-sm' : 'pb-1.5 text-lg', tone(delta, block.deltaFormat), blur(block.deltaFormat))}>{fmt(delta, block.deltaFormat)}</p>
        ) : null}
      </div>
      {block.caption && env.size !== 'S' ? <p className="text-sm text-muted-foreground">{block.caption}</p> : null}
      {trend && trend.length > 1 ? <Sparkline values={trend} height={env.size === 'L' ? 150 : small ? 40 : 64} /> : null}
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
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs font-medium text-muted-foreground">{block.label}</p>
          <p className={cn('mt-1 font-semibold leading-none tracking-tight', env.size ? 'text-[28px]' : 'text-4xl', blur(block.format))}>{fmt(value, block.format)}</p>
        </div>
        <p className="text-sm text-muted-foreground">
          of <span className={cn('font-medium text-foreground', blur(block.format))}>{fmt(max, block.format)}</span> · <span className="font-medium text-foreground tabular-nums">{fmt(share, 'pct')}</span>
        </p>
      </div>
      <div
        className="relative h-2.5 overflow-visible rounded-full"
        style={{ background: 'color-mix(in oklab, var(--viz-1) 16%, var(--viz-mid))' }}
        role="meter"
        aria-valuemin={0}
        aria-valuemax={max ?? 0}
        aria-valuenow={value ?? 0}
        aria-label={block.label}
      >
        <div className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-500" style={{ width: `${share * 100}%`, background: 'var(--viz-1)' }} />
        {mark != null ? (
          <div className="absolute -top-1 -bottom-1 w-0.5 rounded-full bg-foreground/60" style={{ left: `calc(${Math.max(0, Math.min(1, mark)) * 100}% - 1px)` }} title={block.markLabel} />
        ) : null}
      </div>
      {mark != null && block.markLabel && env.size !== 'S' ? (
        <p className="text-xs text-muted-foreground">
          <span className="mr-1 inline-block h-2.5 w-0.5 translate-y-0.5 rounded-full bg-foreground/60" />
          {block.markLabel} ({fmt(mark, 'pct')}) — you’re {ahead ? 'ahead of' : 'behind'} an even pace.
        </p>
      ) : null}
      {block.caption && env.size !== 'S' ? <p className="text-xs text-muted-foreground">{block.caption}</p> : null}
    </div>
  )
}

export function Kpis({ block, values }: { block: KpisBlock; values: { value: number | null; delta: number | null }[] }) {
  const fmt = useFmt()
  const blur = useBlur()
  const env = useVizEnv()
  const cap = env.size === 'S' ? 1 : env.size === 'M' ? 2 : env.size === 'L' ? 4 : 5
  const items = block.items.slice(0, cap)
  const cols = env.size
    ? items.length >= 2
      ? 'grid-cols-2'
      : 'grid-cols-1'
    : items.length >= 4
      ? 'sm:grid-cols-2 lg:grid-cols-4'
      : items.length === 3
        ? 'sm:grid-cols-3'
        : 'sm:grid-cols-2'
  return (
    <div className={cn('grid gap-px overflow-hidden rounded-xl border border-border bg-border', env.size ? '' : 'grid-cols-2', cols)}>
      {items.map((it, i) => {
        const v = values[i] ?? { value: null, delta: null }
        return (
          <div key={it.label} className="min-w-0 bg-card px-4 py-3.5">
            <p className="truncate text-xs text-muted-foreground">{it.label}</p>
            <p className={cn('mt-1 truncate font-semibold tracking-tight', items.length >= 4 && !env.size ? 'text-xl' : 'text-2xl', tone(v.value, it.format), blur(it.format))}>
              {fmt(v.value, it.format)}
            </p>
            {it.delta || it.hint ? (
              <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                {it.delta ? <span className={cn('mr-1.5 font-medium tabular-nums', tone(v.delta, it.deltaFormat), blur(it.deltaFormat))}>{fmt(v.delta, it.deltaFormat)}</span> : null}
                {it.hint}
              </p>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
