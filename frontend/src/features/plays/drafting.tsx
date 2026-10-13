import { useEffect, useMemo, useState } from 'react'
import {
  BookOpenText,
  Check,
  CandlestickChart,
  Globe,
  type LucideIcon,
  Scale,
  Sigma,
  Sparkles,
  Wallet,
  X,
} from 'lucide-react'

import { cn } from '@/lib/utils'
import type { DraftJob, DraftProgress, DraftPhase } from './types'
import { Eyebrow } from './ui'

type PhaseDef = { id: DraftPhase; label: string; source: string; Icon: LucideIcon }

/** The drafting pipeline, in order. Each backend progress event maps to one phase. */
const PHASES: PhaseDef[] = [
  { id: 'portfolio', label: 'Reading your portfolio', source: 'Your portfolio', Icon: Wallet },
  { id: 'research', label: 'Researching the theme', source: 'News & web', Icon: Globe },
  { id: 'prices', label: 'Checking prices', source: 'Market data', Icon: CandlestickChart },
  { id: 'fundamentals', label: 'Reading fundamentals', source: 'Fundamentals', Icon: BookOpenText },
  { id: 'compare', label: 'Comparing candidates', source: 'Quant', Icon: Sigma },
  { id: 'sizing', label: 'Sizing against your book', source: 'Your book & risk rails', Icon: Scale },
  { id: 'done', label: 'Draft ready', source: 'Ready to review', Icon: Sparkles },
]
const ORDER = PHASES.map((p) => p.id)

/** Older jobs (and fixed lines) carry no phase: infer it from the text. */
function phaseOf(e: DraftProgress): DraftPhase | null {
  if (e.phase && ORDER.includes(e.phase)) return e.phase
  const t = e.text.toLowerCase()
  if (t.startsWith('reading your portfolio') || t.startsWith('book:')) return 'portfolio'
  if (t.startsWith('researching') || t.startsWith('searching')) return 'research'
  if (t.startsWith('checking price') || t.startsWith('measuring risk')) return 'prices'
  if (t.startsWith('reading fundamentals') || t.startsWith('checking valuation')) return 'fundamentals'
  if (t.startsWith('comparing') || t.startsWith('checking correlations')) return 'compare'
  if (t.startsWith('basket:') || t.startsWith("checking it's all") || t.startsWith('sizing') || t.startsWith('tightening')) return 'sizing'
  if (t.startsWith('draft ready')) return 'done'
  return null
}

function tickersOf(e: DraftProgress): string[] {
  if (e.tickers?.length) return e.tickers
  const m = e.text.match(/^(?:Checking price for|Measuring risk for|Reading fundamentals for|Checking valuation for|Comparing|Checking correlations across)\s+(.+)$/i)
  return m ? m[1].split(/[,\s]+/).map((t) => t.trim().toUpperCase()).filter(Boolean) : []
}

type PhaseState = 'done' | 'active' | 'pending' | 'skipped' | 'failed'

type PhaseView = PhaseDef & {
  state: PhaseState
  events: DraftProgress[]
  tickers: string[]
  notes: string[]
}

function buildPhases(job: DraftJob | null, failed: boolean): PhaseView[] {
  const events = job?.progress ?? []
  const byPhase = new Map<DraftPhase, DraftProgress[]>()
  let last: DraftPhase = 'portfolio'
  for (const e of events) {
    const ph: DraftPhase = phaseOf(e) ?? last
    last = ph
    byPhase.set(ph, [...(byPhase.get(ph) ?? []), e])
  }
  const reached = Math.max(0, ...[...byPhase.keys()].map((p) => ORDER.indexOf(p)))
  const finished = job?.status === 'done'
  return PHASES.map((def, i) => {
    const evs = byPhase.get(def.id) ?? []
    let state: PhaseState
    if (finished) state = evs.length || def.id === 'done' ? 'done' : 'skipped'
    else if (i < reached) state = evs.length ? 'done' : 'skipped'
    else if (i === reached) state = failed ? 'failed' : 'active'
    else state = 'pending'
    const tickers: string[] = []
    for (const e of evs) for (const t of tickersOf(e)) if (!tickers.includes(t)) tickers.push(t)
    const notes = evs
      .filter((e) => !tickersOf(e).length && e.text.toLowerCase() !== def.label.toLowerCase())
      .map((e) => e.text.replace(/^Searching the web:\s*/i, '“').replace(/^(“.+)$/, '$1”'))
    return { ...def, state, events: evs, tickers, notes }
  })
}

function useElapsed(since?: string | null, until?: string | null) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (until) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [until])
  if (!since) return null
  const end = until ? new Date(until).getTime() : now
  const s = Math.max(0, Math.round((end - new Date(since.endsWith('Z') ? since : `${since}Z`).getTime()) / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** The research pipeline as a few grouped phases, each with its data source and the
 *  names it touched, plus the basket as it forms. */
export function DraftProgressView({ job, failed }: { job: DraftJob | null; failed: boolean }) {
  const phases = useMemo(() => buildPhases(job, failed), [job, failed])
  const elapsed = useElapsed(job?.started_at, job?.finished_at)
  const basketEvent = [...(job?.progress ?? [])].reverse().find((e) => e.basket?.length)
  const candidates = useMemo(() => {
    const seen: string[] = []
    for (const p of phases) if (['prices', 'fundamentals', 'compare'].includes(p.id)) for (const t of p.tickers) if (!seen.includes(t)) seen.push(t)
    return seen
  }, [phases])
  const visible = phases.filter((p) => p.state !== 'skipped' || p.id !== 'compare')

  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_17rem]">
      <ol className="relative" aria-label="Drafting progress">
        {visible.map((p, i) => (
          <PhaseRow key={p.id} phase={p} last={i === visible.length - 1} />
        ))}
      </ol>

      <aside className="space-y-3 lg:border-l lg:border-border lg:pl-6" aria-live="polite">
        <div className="flex items-baseline justify-between">
          <Eyebrow>{basketEvent ? 'The basket' : 'Emerging basket'}</Eyebrow>
          {elapsed ? <span className="font-mono text-[11px] text-muted-foreground tabular-nums">{elapsed}</span> : null}
        </div>
        {basketEvent?.basket ? (
          <ul className="space-y-2.5">
            {basketEvent.basket.map((l, i) => (
              <li
                key={l.ticker}
                className="animate-in fade-in slide-in-from-bottom-1 fill-mode-both duration-500"
                style={{ animationDelay: `${i * 70}ms` }}
              >
                <div className="flex items-baseline justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate">
                    <span className="font-mono font-medium text-foreground">{l.ticker}</span>
                    {l.name ? <span className="ml-1.5 text-muted-foreground">{l.name}</span> : null}
                  </span>
                  <span className="shrink-0 font-mono text-muted-foreground tabular-nums">{Math.round((l.weight ?? 0) * 100)}%</span>
                </div>
                <div className="mt-1 h-1 rounded-full bg-muted">
                  <div
                    className={cn('h-full rounded-full', l.role === 'hedge' ? 'bg-muted-foreground/60' : 'bg-foreground/70')}
                    style={{ width: `${Math.min(100, (l.weight ?? 0) * 100 * 2)}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        ) : candidates.length ? (
          <>
            <div className="flex flex-wrap gap-1.5">
              {candidates.map((t) => (
                <TickerChip key={t} ticker={t} />
              ))}
            </div>
            <p className="text-xs text-muted-foreground">Names Archie is weighing. The final basket appears here once it's chosen.</p>
          </>
        ) : (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Candidates appear here as Archie checks them, then the basket it settles on, with weights.
          </p>
        )}
      </aside>
    </div>
  )
}

function PhaseRow({ phase, last }: { phase: PhaseView; last: boolean }) {
  const { state, Icon } = phase
  const live = state === 'active'
  return (
    <li className="relative flex gap-4 pb-6 last:pb-0">
      {!last ? (
        <span
          aria-hidden
          className={cn('absolute top-8 bottom-1 left-[15px] w-px', state === 'done' || state === 'skipped' ? 'bg-foreground/20' : 'bg-border')}
        />
      ) : null}
      <span
        className={cn(
          'relative z-10 flex size-8 shrink-0 items-center justify-center rounded-full border transition-colors duration-500',
          state === 'done' && 'border-foreground/20 bg-muted text-foreground',
          live && 'border-foreground/60 bg-background text-foreground',
          state === 'failed' && 'border-warning/60 bg-warning/10 text-warning',
          (state === 'pending' || state === 'skipped') && 'border-border bg-background text-muted-foreground/50',
        )}
      >
        {live ? <span aria-hidden className="absolute inset-0 animate-ping rounded-full border border-foreground/30 [animation-duration:2.4s]" /> : null}
        {state === 'failed' ? <X className="size-3.5" /> : <Icon className="size-3.5" />}
        {state === 'done' && phase.id !== 'done' ? (
          <span className="absolute -right-0.5 -bottom-0.5 flex size-3.5 items-center justify-center rounded-full bg-background">
            <Check className="size-2.5 text-positive" strokeWidth={3} />
          </span>
        ) : null}
      </span>
      <div className={cn('min-w-0 flex-1 pt-1', (state === 'pending' || state === 'skipped') && 'opacity-50')}>
        <div className="flex flex-wrap items-baseline gap-x-2">
          <p className={cn('text-sm', live || state === 'done' ? 'font-medium text-foreground' : 'text-muted-foreground')}>{phase.label}</p>
          <span className="text-[11px] text-muted-foreground">
            {state === 'skipped' ? 'not needed' : phase.source}
            {phase.tickers.length > 1 && state !== 'pending' ? ` · ${phase.tickers.length} names` : ''}
          </span>
        </div>
        {phase.tickers.length && state !== 'pending' ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {phase.tickers.map((t) => (
              <TickerChip key={t} ticker={t} />
            ))}
          </div>
        ) : null}
        {phase.notes.length && state !== 'pending' ? (
          <ul className="mt-1 space-y-0.5">
            {phase.notes.slice(-3).map((n, i) => (
              <li key={i} className="animate-in fade-in truncate text-xs text-muted-foreground duration-500">
                {n.startsWith('Basket:') ? 'Picked the basket' : n}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </li>
  )
}

function TickerChip({ ticker }: { ticker: string }) {
  return (
    <span className="animate-in fade-in zoom-in-95 rounded-md border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/85 duration-500">
      {ticker}
    </span>
  )
}
