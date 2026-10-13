import { useEffect, useState } from 'react'
import { Check } from 'lucide-react'

import { cn } from '@/lib/utils'
import type { ViewJob } from '@/types/views'
import './build.css'

/**
 * The building state for a view: a sketch of the likely layout that resolves
 * block by block while gentle copy follows the real build phases. Calm in the
 * fast case (5s), honest and unhurried in the slow one (50s).
 */

export const PHASES = ['Understanding your request', 'Choosing the charts', 'Fetching your data', 'Putting it together'] as const

/** Map the builder's real progress lines (and the clock) onto the four phases. */
export function phaseOf(job: ViewJob, elapsed: number): number {
  if (job.status === 'done') return 3
  const texts = job.progress.map((p) => p.text.toLowerCase())
  const any = (re: RegExp) => texts.some((t) => re.test(t))
  if (any(/^reading (your|the) (?!current)|sample|fetch|checking it|safe and valid|fixing \d/)) return 2
  if (elapsed >= 1.6 && (any(/design|plan|applying|opening|writing|refining|reviewing|slower way|reading the current/) || job.status === 'running')) return 1
  return 0
}

function slowCopy(phase: number, elapsed: number, codePath: boolean): string {
  if (phase === 0) return 'Reading what you asked for.'
  if (phase === 2) return elapsed > 25 ? 'Pulling in your live numbers. Almost there.' : 'Pulling in your live numbers.'
  if (phase === 3) return 'Laying it out.'
  if (codePath) {
    if (elapsed < 20) return 'This one needs a custom layout, so it takes a little longer.'
    if (elapsed < 40) return 'Still drawing it. Custom views can take up to a minute.'
    return 'Nearly there. You can leave this page; it will be waiting in Views.'
  }
  if (elapsed < 8) return 'Picking the clearest way to show it.'
  if (elapsed < 20) return 'Weighing a couple of layouts.'
  if (elapsed < 40) return 'Still working. Bigger views can take up to a minute.'
  return 'Nearly there. You can leave this page; it will be waiting in Views.'
}

// ── layout guess ───────────────────────────────────────────────────────
type Sketch = 'hero' | 'kpis' | 'treemap' | 'bars' | 'columns' | 'timeseries' | 'allocation' | 'table'
type Slot = { kind: Sketch; span: 'full' | 'half' }

export function guessLayout(prompt: string): Slot[] {
  const p = prompt.toLowerCase()
  const scores: Record<Exclude<Sketch, 'hero' | 'kpis' | 'table' | 'columns'>, number> = {
    treemap: /treemap|\bmap\b|where|colou?r|moved|today|heat/.test(p) ? 2 : 0,
    timeseries: /over time|last (year|month|\d)|history|growth|since|curve|trend|vs what i put|value over|performance/.test(p) ? 2 : 0,
    bars: /winner|loser|biggest|top|rank|by month|dividend|income|pays|contribut|best|worst/.test(p) ? 2 : 0,
    allocation: /sector|countr|currenc|mix|expos|split|allocation|breakdown|diversif/.test(p) ? 2 : 0,
  }
  const ranked = (Object.keys(scores) as (keyof typeof scores)[]).sort((a, b) => scores[b] - scores[a])
  const main = scores[ranked[0]] ? ranked[0] : 'treemap'
  const rest = ranked.filter((k) => k !== main)
  const second = scores[rest[0]] ? rest[0] : main === 'bars' ? 'allocation' : 'bars'
  const third = rest.find((k) => k !== second) ?? 'allocation'
  // Time buckets ("by month") read as columns, not ranked bars.
  const buckets = /by month|monthly|per month|each month|by year|by week/.test(p)
  const asCols = (k: Sketch): Sketch => (k === 'bars' && buckets ? 'columns' : k)
  return [
    { kind: 'hero', span: 'full' },
    { kind: 'kpis', span: 'full' },
    { kind: asCols(main), span: 'full' },
    { kind: second, span: 'half' },
    { kind: /table|list|detail|upcoming|coming up/.test(p) ? 'table' : third, span: 'half' },
  ]
}

// ── the sketches ───────────────────────────────────────────────────────
const TILES = [
  [0, 0, 46, 62], [46, 0, 30, 38], [76, 0, 24, 38], [46, 38, 22, 24], [68, 38, 32, 24],
  [0, 62, 28, 38], [28, 62, 22, 38], [50, 62, 26, 38], [76, 62, 24, 38],
]
const BARS = [0.92, 0.7, 0.56, 0.44, 0.31, -0.18, -0.36]

function Glyph({ kind }: { kind: Sketch }) {
  switch (kind) {
    case 'hero':
      return (
        <div className="space-y-3">
          <div className="build-ink h-2.5 w-16" />
          <div className="build-ink h-9 w-56 max-w-full rounded-lg" />
          <svg viewBox="0 0 200 24" className="build-draw h-6 w-48 max-w-full" preserveAspectRatio="none" aria-hidden>
            <path d="M0 18 C 20 16, 30 8, 50 12 S 80 20, 100 12 S 140 2, 160 8 S 190 6, 200 4" pathLength={1} />
          </svg>
        </div>
      )
    case 'kpis':
      return (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="build-tile space-y-2 rounded-lg p-3" style={{ animationDelay: `${i * 90}ms` }}>
              <div className="build-ink h-2 w-12" />
              <div className="build-ink h-5 w-20" />
            </div>
          ))}
        </div>
      )
    case 'treemap':
      return (
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="build-treemap h-56 w-full sm:h-64" aria-hidden>
          {TILES.map(([x, y, w, h], i) => (
            <rect key={i} x={x + 0.6} y={y + 0.6} width={w - 1.2} height={h - 1.2} rx={1.2} style={{ animationDelay: `${i * 70}ms` }} />
          ))}
        </svg>
      )
    case 'bars':
      return (
        <div className="space-y-2.5 py-1">
          {BARS.map((v, i) => (
            <div key={i} className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-3">
              <div className="build-ink h-2 w-14" />
              <div className="relative h-3">
                <span className="absolute inset-y-[-3px] left-[30%] w-px bg-border" />
                <span
                  className={cn('build-bar absolute inset-y-0', v >= 0 ? 'rounded-r-[3px]' : 'rounded-l-[3px]')}
                  style={{ ...(v >= 0 ? { left: '30%', width: `${v * 70}%` } : { right: '70%', width: `${-v * 70}%` }), animationDelay: `${i * 80}ms` }}
                />
              </div>
            </div>
          ))}
        </div>
      )
    case 'columns':
      return (
        <div className="flex h-44 items-end gap-[3%] border-b border-border/70 px-1 pt-2">
          {[38, 52, 30, 64, 46, 72, 58, 40, 80, 62, 50, 88].map((h, i) => (
            <span key={i} className="build-col flex-1 rounded-t-[3px]" style={{ height: `${h}%`, animationDelay: `${i * 45}ms` }} />
          ))}
        </div>
      )
    case 'timeseries':
      return (
        <svg viewBox="0 0 300 120" preserveAspectRatio="none" className="build-draw build-area h-48 w-full" aria-hidden>
          <path className="build-area-fill" d="M0 100 C 30 96, 50 80, 80 84 S 130 60, 160 64 S 220 30, 250 38 S 290 18, 300 14 L 300 120 L 0 120 Z" />
          <path d="M0 100 C 30 96, 50 80, 80 84 S 130 60, 160 64 S 220 30, 250 38 S 290 18, 300 14" pathLength={1} />
        </svg>
      )
    case 'allocation':
      return (
        <div className="space-y-4 py-1">
          <div className="flex h-4 gap-[2px] overflow-hidden rounded-[4px]">
            {[34, 22, 16, 12, 9, 7].map((w, i) => (
              <span key={i} className="build-seg h-full" style={{ width: `${w}%`, animationDelay: `${i * 80}ms`, ['--seg' as string]: `var(--viz-${(i % 6) + 1})` }} />
            ))}
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="flex items-center gap-1.5">
                <span className="build-ink size-2.5 rounded-[3px]" />
                <span className="build-ink h-2 w-14" />
              </div>
            ))}
          </div>
        </div>
      )
    case 'table':
      return (
        <div className="space-y-3 py-1">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="grid grid-cols-[1fr_4rem_3rem] gap-4">
              <div className="build-ink h-2.5" style={{ width: `${80 - i * 9}%` }} />
              <div className="build-ink h-2.5" />
              <div className="build-ink h-2.5" />
            </div>
          ))}
        </div>
      )
  }
}

const SKETCH_TITLE: Record<Sketch, string | null> = { hero: null, kpis: null, treemap: 'w-40', bars: 'w-32', columns: 'w-28', timeseries: 'w-36', allocation: 'w-28', table: 'w-24' }

// ── canvas ─────────────────────────────────────────────────────────────
export function BuildCanvas({ job, prompt }: { job: ViewJob | null; prompt: string }) {
  const [startedAt] = useState(() => Date.now() - (job?.elapsed_s ?? 0) * 1000)
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(t)
  }, [])
  const elapsed = Math.max(0, (now - startedAt) / 1000)
  const phase = job ? phaseOf(job, elapsed) : 0
  const codePath = !!job && (job.engine === 'code' || job.progress.some((p) => /slower way/i.test(p.text)))
  const layout = guessLayout(prompt)
  // Blocks resolve one by one: sketched while choosing charts, inked once data arrives.
  const sketched = phase === 0 ? 0 : Math.min(layout.length, 1 + Math.floor((elapsed - 1.6) / 1.4))
  const inked = phase >= 2 ? layout.length : 0

  return (
    <div className="space-y-6" aria-live="polite">
      <BuildStepper phase={phase} elapsed={elapsed} copy={slowCopy(phase, elapsed, codePath)} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12" aria-hidden>
        {layout.map((slot, i) => {
          const state = i < inked ? 'inked' : i < sketched ? 'sketched' : 'ghost'
          const bare = slot.kind === 'hero' || slot.kind === 'kpis'
          return (
            <div key={i} className={cn('build-block min-w-0', slot.span === 'full' ? 'lg:col-span-12' : 'lg:col-span-6')} data-state={state} style={{ animationDelay: `${i * 120}ms` }}>
              {bare ? (
                <Glyph kind={slot.kind} />
              ) : (
                <div className="build-card rounded-xl border p-5">
                  {SKETCH_TITLE[slot.kind] ? <div className={cn('build-ink mb-5 h-3', SKETCH_TITLE[slot.kind])} /> : null}
                  <Glyph kind={slot.kind} />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** Four quiet steps; the current one names itself, with one line of copy below. */
export function BuildStepper({ phase, elapsed, copy, compact = false }: { phase: number; elapsed: number; copy: string; compact?: boolean }) {
  return (
    <div className={cn('space-y-3', compact && 'space-y-2')}>
      <ol className="flex items-center gap-2" aria-label="Build progress">
        {PHASES.map((label, i) => {
          const done = i < phase
          const current = i === phase
          return (
            <li key={label} className={cn('flex items-center gap-2', current ? 'min-w-0' : 'shrink-0')} aria-current={current ? 'step' : undefined}>
              <span
                className={cn(
                  'flex size-5 shrink-0 items-center justify-center rounded-full border transition-colors duration-500',
                  done ? 'border-primary/40 bg-primary/15 text-primary' : current ? 'build-current border-primary/60 text-primary' : 'border-border text-transparent',
                )}
              >
                {done ? <Check className="size-3" strokeWidth={2.5} /> : <span className={cn('size-1.5 rounded-full', current ? 'bg-primary' : 'bg-border')} />}
              </span>
              <span className={cn('truncate text-sm transition-colors duration-500', current ? 'font-medium text-foreground' : 'sr-only')}>{label}</span>
              {i < PHASES.length - 1 ? <span className={cn('h-px w-5 shrink-0 transition-colors duration-700 sm:w-8', done ? 'bg-primary/40' : 'bg-border')} /> : null}
            </li>
          )
        })}
      </ol>
      <p className="flex items-baseline justify-between gap-3 text-[13px] text-muted-foreground">
        <span key={copy} className="build-copy">
          {copy}
        </span>
        {elapsed >= 6 ? <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground/70">{Math.floor(elapsed)}s</span> : null}
      </p>
    </div>
  )
}

/** Edits to an existing view: a slim progress strip while the current version stays in view. */
export function BuildStrip({ job }: { job: ViewJob }) {
  const [startedAt] = useState(() => Date.now() - job.elapsed_s * 1000)
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(t)
  }, [])
  const elapsed = Math.max(0, (now - startedAt) / 1000)
  const phase = phaseOf(job, elapsed)
  const codePath = job.engine === 'code' || job.progress.some((p) => /slower way/i.test(p.text))
  return (
    <div className="build-strip relative overflow-hidden rounded-xl border border-primary/20 bg-primary/[0.03] px-4 py-3" aria-live="polite">
      <p className="mb-2 truncate text-xs text-muted-foreground">
        {job.kind === 'fix' ? 'Fixing this view' : 'Applying'} <span className="text-foreground/80">“{job.prompt.split('\n')[0].slice(0, 120)}”</span>
      </p>
      <BuildStepper phase={phase} elapsed={elapsed} copy={slowCopy(phase, elapsed, codePath)} compact />
    </div>
  )
}
