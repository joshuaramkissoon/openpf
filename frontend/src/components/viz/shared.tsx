import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

import { privacyBlurClass, type PrivacyMode } from '@/lib/privacy'
import { cn } from '@/lib/utils'
import { formatValue, MONEY_FORMATS, num } from './engine'
import type { Row, VizFormat } from './types'

// ── context ─────────────────────────────────────────────────────────────
export type VizEnv = {
  currency: 'GBP' | 'USD'
  privacy: PrivacyMode
  openTicker: (ticker: string) => void
  navigate: (path: string) => void
  compact: boolean
  /** Widget size (Home grid): S 1×1, M 2×1, L 2×2. null = page/chat layout. */
  size: WidgetSize | null
}

export type WidgetSize = 'S' | 'M' | 'L'

export const VizEnvContext = createContext<VizEnv>({
  currency: 'GBP',
  privacy: 'off',
  openTicker: () => {},
  navigate: () => {},
  compact: false,
  size: null,
})

export function useVizEnv() {
  return useContext(VizEnvContext)
}

export function useFmt() {
  const env = useVizEnv()
  return (v: unknown, format?: VizFormat, compact?: boolean) => formatValue(v, format, { currency: env.currency, privacy: env.privacy, compact })
}

/** Class to blur sensitive (money) figures in blur privacy mode. */
export function useBlur() {
  const env = useVizEnv()
  return (format?: VizFormat) => (format && MONEY_FORMATS.has(format) ? privacyBlurClass(env.privacy) : '')
}

// ── colour ──────────────────────────────────────────────────────────────
export const CATEGORICAL = ['var(--viz-1)', 'var(--viz-2)', 'var(--viz-3)', 'var(--viz-4)', 'var(--viz-5)', 'var(--viz-6)']

/** Categorical colour by entity (stable index), folding past slot 6 to "Other" grey. */
export function catColor(i: number, isOther = false) {
  return isOther || i >= CATEGORICAL.length ? 'var(--viz-other)' : CATEGORICAL[i]
}

/** Diverging fill for a signed value: grey at 0 → gain/loss pole at |v| ≥ cap. */
export function divergingFill(v: number | null, cap: number): { fill: string; strength: number } {
  if (v == null || !Number.isFinite(v) || cap <= 0) return { fill: 'var(--viz-mid)', strength: 0 }
  const t = Math.min(1, Math.abs(v) / cap)
  const strength = 0.18 + 0.82 * t
  const pole = v >= 0 ? 'var(--viz-gain)' : 'var(--viz-loss)'
  return { fill: `color-mix(in oklab, ${pole} ${Math.round(strength * 100)}%, var(--viz-mid))`, strength }
}

/** Ink that clears contrast on a diverging fill. */
export function inkOn(strength: number) {
  return strength > 0.62 ? '#ffffff' : 'var(--foreground)'
}

/** A robust colour cap: ~85th percentile of |v|, kept within sane bounds. */
export function colorCap(values: (number | null)[], min = 0.005, max = 0.6) {
  const abs = values.filter((v): v is number => v != null && Number.isFinite(v)).map(Math.abs).sort((a, b) => a - b)
  if (!abs.length) return min
  const p = abs[Math.min(abs.length - 1, Math.floor(abs.length * 0.85))]
  return Math.max(min, Math.min(max, p))
}

export function isSigned(rows: Row[], field: string) {
  return rows.some((r) => (num(r[field]) ?? 0) < 0)
}

// ── layout ──────────────────────────────────────────────────────────────
export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    setWidth(el.clientWidth)
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, width] as const
}

// ── tooltip ─────────────────────────────────────────────────────────────
export type TipContent = { title: string; rows: { label: string; value: string; color?: string; line?: boolean; money?: boolean }[]; note?: string }

export function useTooltip() {
  const [tip, setTip] = useState<{ x: number; y: number; content: TipContent } | null>(null)
  const hostRef = useRef<HTMLDivElement>(null)
  const show = (e: { clientX: number; clientY: number }, content: TipContent) => {
    const host = hostRef.current?.getBoundingClientRect()
    if (!host) return
    setTip({ x: e.clientX - host.left, y: e.clientY - host.top, content })
  }
  const hide = () => setTip(null)
  return { tip, show, hide, hostRef }
}

export function Tooltip({ tip, hostWidth }: { tip: { x: number; y: number; content: TipContent } | null; hostWidth: number }) {
  const env = useVizEnv()
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(200)
  useEffect(() => {
    if (ref.current) setW(ref.current.offsetWidth)
  }, [tip])
  if (!tip) return null
  const left = tip.x + 14 + w > hostWidth ? Math.max(4, tip.x - w - 14) : tip.x + 14
  return (
    <div
      ref={ref}
      role="tooltip"
      className="pointer-events-none absolute z-20 min-w-36 max-w-64 rounded-lg border border-border bg-popover px-3 py-2 text-xs shadow-lg shadow-black/10"
      style={{ left, top: Math.max(4, tip.y - 12) }}
    >
      <p className="mb-1 text-muted-foreground">{tip.content.title}</p>
      {tip.content.rows.map((r, i) => (
        <div key={i} className="flex items-center gap-2">
          {r.color ? <span className={cn('inline-block shrink-0', r.line ? 'h-0.5 w-3 rounded-full' : 'size-2 rounded-[2px]')} style={{ background: r.color }} /> : null}
          <span className="text-muted-foreground">{r.label}</span>
          <span className={cn('ml-auto pl-3 font-semibold tabular-nums text-foreground', r.money && privacyBlurClass(env.privacy))}>{r.value}</span>
        </div>
      ))}
      {tip.content.note ? <p className="mt-1 text-muted-foreground">{tip.content.note}</p> : null}
    </div>
  )
}

// ── small pieces ────────────────────────────────────────────────────────
export function Legend({ items }: { items: { label: string; color: string; line?: boolean; value?: string; money?: boolean }[] }) {
  const env = useVizEnv()
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
      {items.map((it) => (
        <li key={it.label} className="inline-flex items-center gap-1.5 text-muted-foreground">
          <span className={cn('inline-block shrink-0', it.line ? 'h-0.5 w-3.5 rounded-full' : 'size-2.5 rounded-[3px]')} style={{ background: it.color }} />
          <span className="text-foreground/90">{it.label}</span>
          {it.value ? <span className={cn('tabular-nums', it.money && privacyBlurClass(env.privacy))}>{it.value}</span> : null}
        </li>
      ))}
    </ul>
  )
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <div className="flex min-h-24 items-center justify-center px-4 py-6 text-center text-sm text-muted-foreground">{children}</div>
}

export function Breadcrumb({ trail, onBack }: { trail: string[]; onBack: (depth: number) => void }) {
  return (
    <nav className="mb-3 flex flex-wrap items-center gap-1 text-xs" aria-label="Drill-down">
      {trail.map((t, i) => (
        <span key={i} className="inline-flex items-center gap-1">
          {i > 0 ? <span className="text-muted-foreground/60">/</span> : null}
          {i < trail.length - 1 ? (
            <button type="button" className="rounded px-1 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" onClick={() => onBack(i)}>
              {t}
            </button>
          ) : (
            <span className="px-1 py-0.5 font-medium text-foreground">{t}</span>
          )}
        </span>
      ))}
    </nav>
  )
}

const PRETTY: Record<string, string> = { stocks_isa: 'Stocks ISA', invest: 'Invest', all: 'All accounts', DEPOSIT: 'Deposit', WITHDRAW: 'Withdrawal', WITHDRAWAL: 'Withdrawal' }

/** Human labels for raw enum values that show up as categories (accounts, cash types). */
export function prettyLabel(v: unknown): string {
  const s = v == null ? '' : String(v)
  return PRETTY[s] ?? s
}
