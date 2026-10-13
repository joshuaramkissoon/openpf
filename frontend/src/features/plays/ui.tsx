import type { ReactNode } from 'react'
import { CircleCheck, CircleDashed, CircleHelp, CircleX, TriangleAlert } from 'lucide-react'

import { cn } from '@/lib/utils'
import { cleanTicker } from './names'
import type { Conviction, KillRule, KillState, PlayStatus, Verdict } from './types'

/** Borderless titled block — mirrors the v2 shell's `Section` rhythm. */
export function Block({
  title,
  description,
  action,
  className,
  children,
}: {
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
  children: ReactNode
}) {
  return (
    <section className={cn('min-w-0', className)}>
      {title || action ? (
        <div className="mb-3 flex items-end justify-between gap-3">
          <div className="min-w-0">
            {title ? <h2 className="text-sm font-semibold tracking-tight">{title}</h2> : null}
            {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
          </div>
          {action ? <div className="flex shrink-0 items-center gap-1">{action}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  )
}

export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn('text-[11px] font-medium uppercase tracking-wider text-muted-foreground', className)}>{children}</p>
}

const STATUS: Record<PlayStatus, { label: string; dot: string }> = {
  draft: { label: 'Draft', dot: 'bg-muted-foreground/60' },
  active: { label: 'Active', dot: 'bg-positive' },
  paused: { label: 'Paused', dot: 'bg-warning' },
  closed: { label: 'Closed', dot: 'bg-muted-foreground/40' },
}

export function StatusChip({ status }: { status: PlayStatus }) {
  const s = STATUS[status]
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span className={cn('size-1.5 rounded-full', s.dot)} />
      {s.label}
    </span>
  )
}

const VERDICT: Record<Verdict, { label: string; cls: string }> = {
  on_track: { label: 'On track', cls: 'bg-positive/10 text-positive' },
  watch: { label: 'Watch', cls: 'bg-warning/10 text-warning' },
  broken: { label: 'Broken', cls: 'bg-negative/10 text-negative' },
}

export function VerdictChip({ verdict, className }: { verdict?: Verdict | null; className?: string }) {
  if (!verdict) return null
  const v = VERDICT[verdict]
  return <span className={cn('inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium', v.cls, className)}>{v.label}</span>
}

export function ConvictionDots({ conviction }: { conviction: Conviction }) {
  const n = { low: 1, medium: 2, high: 3 }[conviction]
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground" title={`${conviction} conviction`}>
      <span className="inline-flex gap-0.5">
        {[0, 1, 2].map((i) => (
          <span key={i} className={cn('h-2.5 w-1 rounded-full', i < n ? 'bg-foreground/70' : 'bg-border')} />
        ))}
      </span>
      <span className="capitalize">{conviction}</span>
    </span>
  )
}

export function KillIcon({ state }: { state: KillState }) {
  if (state === 'intact') return <CircleCheck className="size-4 text-positive" aria-label="Intact" />
  if (state === 'near') return <TriangleAlert className="size-4 text-warning" aria-label="Near" />
  if (state === 'tripped') return <CircleX className="size-4 text-negative" aria-label="Tripped" />
  if (state === 'manual') return <CircleDashed className="size-4 text-muted-foreground" aria-label="Judgement call" />
  return <CircleHelp className="size-4 text-muted-foreground" aria-label="Unknown" />
}

export function ruleLabel(rule?: KillRule | null): string | null {
  if (!rule) return null
  const v = rule.value
  switch (rule.type) {
    case 'price_below':
      return `${cleanTicker(rule.ticker ?? '')} < ${v}`
    case 'drawdown_pct': {
      const n = Number(v)
      return `Drawdown ≥ ${n <= 1 ? Math.round(n * 100) : n}%`
    }
    case 'weight_above': {
      const n = Number(v)
      const pct = `${n <= 1 ? Math.round(n * 100) : n}%`
      return rule.ticker ? `${cleanTicker(rule.ticker)} > ${pct} of play` : `Play > ${pct} of book`
    }
    case 'date_after':
      return `Review by ${String(v).slice(0, 10)}`
  }
}

/** A thin progress bar for "deployed vs budget". */
export function Meter({ value, className }: { value: number; className?: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <div className={cn('h-1 w-full overflow-hidden rounded-full bg-muted', className)}>
      <div className="h-full rounded-full bg-foreground/70" style={{ width: `${pct}%` }} />
    </div>
  )
}

/** Minimal inline SVG sparkline (no axes). */
export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length < 2) return <div className={cn('h-8', className)} />
  const w = 120
  const h = 32
  const min = Math.min(...values, 0)
  const max = Math.max(...values, 0)
  const span = max - min || 1
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - min) / span) * h}`).join(' ')
  const zeroY = h - ((0 - min) / span) * h
  const up = values[values.length - 1] >= 0
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={cn('h-8 w-full', className)} aria-hidden>
      <line x1={0} x2={w} y1={zeroY} y2={zeroY} className="stroke-border" strokeDasharray="2 3" strokeWidth={1} vectorEffect="non-scaling-stroke" />
      <polyline points={pts} fill="none" className={up ? 'stroke-positive' : 'stroke-negative'} strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  )
}

/** Segmented single-choice control. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  className,
}: {
  value: T | null
  options: { value: T; label: string }[]
  onChange: (v: T) => void
  className?: string
}) {
  return (
    <div className={cn('inline-flex rounded-lg border border-border p-0.5', className)} role="radiogroup">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            'rounded-md px-2.5 py-1 text-xs transition-colors',
            value === o.value ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export const ROLE_LABEL: Record<string, string> = { core: 'Core', satellite: 'Satellite', hedge: 'Hedge' }
