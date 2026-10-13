import { useState } from 'react'
import {
  ArrowRight,
  CalendarClock,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleX,
  Clock3,
  Loader2,
  Scale,
  ShieldAlert,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'

import { ArchieIcon, Money, MoneyDelta, PctDelta } from '@/components/kit'
import { RichMarkdown } from '@/components/RichMarkdown'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { formatPercent } from '@/utils/format'
import { ownerColor } from './book'
import type { BookGroup, KillCriterion, KillState, Play, PlayPerformance, ReviewEntry } from './types'
import { Eyebrow, ruleLabel, VerdictChip } from './ui'

/** A holding is "drifted" when its share of the play is this far from target. */
export const DRIFT_FLAG = 0.03

const shortDate = (d: string | Date) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })

// ── Thesis, up top, as a quote ───────────────────────────────────────────────

function firstParagraph(md: string) {
  const para = md.trim().split(/\n\s*\n/)[0] ?? ''
  return para.replace(/^#+\s*/gm, '').replace(/\*\*|__|`/g, '').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').trim()
}

export function ThesisQuote({ thesis }: { thesis: string }) {
  const [open, setOpen] = useState(false)
  if (!thesis.trim()) return null
  const lead = firstParagraph(thesis)
  const more = thesis.trim().length > lead.length + 4
  return (
    <div className="max-w-3xl border-l-2 border-border pl-4">
      {open ? (
        <div className="text-sm leading-relaxed text-foreground/90">
          <RichMarkdown markdown={thesis} />
        </div>
      ) : (
        <p className="line-clamp-3 text-[15px] leading-relaxed text-foreground/85 text-pretty">{lead}</p>
      )}
      {more ? (
        <button type="button" onClick={() => setOpen((o) => !o)} className="mt-1 text-xs text-muted-foreground hover:text-foreground">
          {open ? 'Show less' : 'Read the full thesis'}
        </button>
      ) : null}
    </div>
  )
}

// ── Hero: value, return, share of book, and the one next action ─────────────

export type NextAction = {
  key: string
  tone: 'critical' | 'warn' | 'info'
  Icon: LucideIcon
  title: string
  detail?: string
  cta: string
  onClick: () => void
  busy?: boolean
}

function ShareOfBook({ weight, target, color }: { weight: number; target?: number | null; color: string }) {
  const max = Math.max(weight, target ?? 0, 0.01) * 1.15
  const over = target ? weight - target : 0
  return (
    <div className="space-y-1.5">
      <p className="flex flex-wrap items-baseline gap-x-1.5 text-xs text-muted-foreground">
        <span className="font-mono text-sm font-medium text-foreground tabular-nums">{formatPercent(weight, 1)}</span>
        of your book
        {target ? (
          <>
            <span aria-hidden>·</span> target <span className="font-mono tabular-nums">{formatPercent(target, 1)}</span>
            {Math.abs(over) >= 0.01 ? (
              <span className={cn('font-mono tabular-nums', over > 0.02 ? 'text-warning' : 'text-muted-foreground')}>
                ({over > 0 ? '+' : '−'}{(Math.abs(over) * 100).toFixed(1)} pts)
              </span>
            ) : null}
          </>
        ) : null}
      </p>
      <div className="relative h-1.5 w-full max-w-sm rounded-full bg-muted" role="img" aria-label={`${formatPercent(weight, 1)} of book, target ${formatPercent(target ?? 0, 1)}`}>
        <div className="h-full rounded-full" style={{ width: `${(weight / max) * 100}%`, background: color }} />
        {target ? (
          <span className="absolute -top-1 h-3.5 w-0.5 rounded-full bg-foreground" style={{ left: `calc(${(target / max) * 100}% - 1px)` }} />
        ) : null}
      </div>
    </div>
  )
}

export function PlayHero({
  play,
  group,
  perf,
  actions,
}: {
  play: Play
  group: BookGroup | null
  perf: PlayPerformance | null
  actions: NextAction[]
}) {
  const value = group?.value_gbp ?? perf?.held_gbp ?? null
  const ppl = group?.ppl_gbp ?? null
  const pplPct = group?.ppl_pct ?? null
  const since = play.activated_at ?? play.created_at
  const today = new Date().toDateString() === new Date(since).toDateString()
  const ret = perf?.basket_return ?? null
  const bench = perf?.benchmark_return ?? null
  const [primary, ...rest] = actions

  return (
    <section className="grid gap-6 rounded-xl border border-border bg-card p-5 md:grid-cols-[minmax(0,1fr)_minmax(0,22rem)] md:gap-8">
      <div className="min-w-0 space-y-4">
        <div>
          <Eyebrow className="mb-1">Value</Eyebrow>
          {value != null ? (
            <Money value={value} decimals={0} className="text-4xl font-semibold tracking-tight" />
          ) : (
            <Skeleton className="h-10 w-40" />
          )}
          {ppl != null && value ? (
            <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 text-xs text-muted-foreground">
              <MoneyDelta value={ppl} decimals={0} className="text-sm" />
              {pplPct != null ? <PctDelta value={pplPct} className="text-sm" /> : null}
              <span>on what you paid</span>
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-x-8 gap-y-3">
          <div>
            <p className="text-xs text-muted-foreground">Since {shortDate(since)}</p>
            {perf == null ? (
              <Skeleton className="mt-1 h-5 w-28" />
            ) : ret != null ? (
              <p className="flex flex-wrap items-baseline gap-x-1.5 text-xs text-muted-foreground">
                <PctDelta value={ret} className="text-base font-medium" />
                {bench != null ? (
                  <span>
                    vs {perf.benchmark} <span className="font-mono tabular-nums">{bench >= 0 ? '+' : ''}{(bench * 100).toFixed(1)}%</span>
                  </span>
                ) : null}
              </p>
            ) : (
              <p className="mt-0.5 text-sm text-muted-foreground">{today ? 'Tracking from tomorrow' : 'Tracking from the first full day'}</p>
            )}
          </div>
          {group ? (
            <div className="min-w-48 flex-1">
              <ShareOfBook weight={group.weight} target={group.target_pct} color={ownerColor('play', group.color_index ?? 0)} />
            </div>
          ) : null}
        </div>
      </div>

      <div className="flex min-w-0 flex-col justify-center gap-3 border-t border-border pt-5 md:border-t-0 md:border-l md:pt-0 md:pl-8">
        {primary ? (
          <>
            <div className="flex items-start gap-3">
              <span
                className={cn(
                  'mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg',
                  primary.tone === 'critical' ? 'bg-negative/10 text-negative' : primary.tone === 'warn' ? 'bg-warning/10 text-warning' : 'bg-muted text-foreground',
                )}
              >
                <primary.Icon className="size-4" />
              </span>
              <div className="min-w-0">
                <Eyebrow>Next</Eyebrow>
                <p className="text-sm font-medium text-balance">{primary.title}</p>
                {primary.detail ? <p className="mt-0.5 text-xs text-muted-foreground">{primary.detail}</p> : null}
              </div>
            </div>
            <Button size="sm" className="self-start" onClick={primary.onClick} disabled={primary.busy}>
              {primary.busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
              {primary.cta}
              {!primary.busy ? <ArrowRight className="size-3.5" /> : null}
            </Button>
            {rest.length ? (
              <ul className="space-y-1 border-t border-border/60 pt-2.5">
                {rest.map((a) => (
                  <li key={a.key} className="flex items-center justify-between gap-3 text-xs">
                    <span className="flex min-w-0 items-center gap-1.5 text-muted-foreground">
                      <a.Icon className={cn('size-3.5 shrink-0', a.tone === 'warn' ? 'text-warning' : a.tone === 'critical' ? 'text-negative' : '')} />
                      <span className="truncate">{a.title}</span>
                    </span>
                    <button type="button" onClick={a.onClick} disabled={a.busy} className="shrink-0 font-medium text-foreground hover:underline disabled:opacity-50">
                      {a.cta}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <div className="flex items-start gap-3">
            <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-positive/10 text-positive">
              <CircleCheck className="size-4" />
            </span>
            <div>
              <Eyebrow>Next</Eyebrow>
              <p className="text-sm font-medium">Nothing needs you</p>
              <p className="mt-0.5 text-xs text-muted-foreground">Archie checks the kill rules after every close.</p>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

/** The actions worth surfacing, most urgent first. */
export function nextActions({
  play,
  perf,
  drifted,
  onStage,
  onRebalance,
  onReview,
  staging,
  reviewing,
}: {
  play: Play
  perf: PlayPerformance | null
  drifted: number
  onStage: (index: number) => void
  onRebalance: () => void
  onReview: () => void
  staging: boolean
  reviewing: boolean
}): NextAction[] {
  const out: NextAction[] = []
  if (play.status === 'closed') return out
  const kills = perf?.kill_status ?? []
  const hit = kills.filter((k) => k.state === 'tripped')
  if (hit.length) {
    out.push({
      key: 'kill', tone: 'critical', Icon: ShieldAlert,
      title: hit.length === 1 ? `Kill rule hit: ${shortKill(hit[0])}` : `${hit.length} kill rules hit`,
      detail: 'Time to decide if the thesis still holds.', cta: 'Run a review', onClick: onReview, busy: reviewing,
    })
  }
  const tranches = play.sizing.tranches ?? []
  const done = new Set((play.sizing.staged ?? []).map((s) => s.index))
  const next = tranches.findIndex((_, i) => !done.has(i))
  if (play.status === 'active' && next >= 0 && play.budget_gbp > 0) {
    const t = tranches[next]
    const amount = Math.round(play.budget_gbp * t.pct_of_budget)
    out.push({
      key: 'stage', tone: 'info', Icon: CalendarClock,
      title: `Stage the ${t.label.toLowerCase()} tranche · £${amount.toLocaleString()}`,
      detail: next === 0 ? 'Creates proposals for you to approve. Nothing is bought yet.' : t.trigger,
      cta: 'Stage tranche', onClick: () => onStage(next), busy: staging,
    })
  }
  if (drifted > 0 && play.status !== 'paused') {
    out.push({
      key: 'drift', tone: 'warn', Icon: Scale,
      title: `${drifted} holding${drifted === 1 ? '' : 's'} off target by ${DRIFT_FLAG * 100}+ pts`,
      detail: 'Preview the trades that bring the play back to its weights.', cta: 'Rebalance', onClick: onRebalance,
    })
  }
  const last = play.review.last_reviewed_at ? new Date(play.review.last_reviewed_at).getTime() : null
  const activeDays = play.activated_at ? (Date.now() - new Date(play.activated_at).getTime()) / 864e5 : 0
  if (!hit.length && ((last == null && activeDays >= 7) || (last != null && Date.now() - last > 30 * 864e5))) {
    out.push({
      key: 'review', tone: 'info', Icon: Clock3, title: 'Review due',
      detail: last ? `Last reviewed ${shortDate(new Date(last))}.` : 'Get Archie’s read on the thesis.',
      cta: 'Run review', onClick: onReview, busy: reviewing,
    })
  }
  return out
}

// ── Health at a glance ──────────────────────────────────────────────────────

type LiveKill = KillCriterion & { state?: KillState; detail?: string }

function shortKill(k: LiveKill) {
  return ruleLabel(k.rule) ?? (k.text.length > 32 ? `${k.text.slice(0, 30).trimEnd()}…` : k.text)
}

const KILL_LOOK: Record<KillState, { label: string; Icon: LucideIcon; cls: string; icon: string }> = {
  intact: { label: 'Fine', Icon: CircleCheck, cls: 'border-border', icon: 'text-positive' },
  near: { label: 'Near', Icon: TriangleAlert, cls: 'border-warning/40 bg-warning/10', icon: 'text-warning' },
  tripped: { label: 'Hit', Icon: CircleX, cls: 'border-negative/40 bg-negative/10', icon: 'text-negative' },
  manual: { label: 'Judgement', Icon: CircleDashed, cls: 'border-dashed border-border', icon: 'text-muted-foreground' },
  unknown: { label: 'Pending', Icon: CircleHelp, cls: 'border-dashed border-border', icon: 'text-muted-foreground' },
}

function KillChip({ k }: { k: LiveKill }) {
  const state: KillState = k.state ?? (k.rule ? 'unknown' : 'manual')
  const look = KILL_LOOK[state]
  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        delay={150}
        className={cn('inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors hover:bg-muted', look.cls)}
      >
        <look.Icon className={cn('size-3.5 shrink-0', look.icon)} aria-hidden />
        <span className="truncate">{shortKill(k)}</span>
        <span className={cn('shrink-0 text-[11px]', state === 'intact' || state === 'manual' || state === 'unknown' ? 'text-muted-foreground' : look.icon)}>
          {look.label}
        </span>
      </PopoverTrigger>
      <PopoverContent side="bottom" align="start" className="w-72">
        <p className="text-sm leading-snug">{k.text}</p>
        <p className="text-xs text-muted-foreground">
          {k.detail ?? (k.rule ? 'Checked after every close.' : 'A judgement call: checked in reviews.')}
          {k.rule && k.detail ? <span className="font-mono"> · {ruleLabel(k.rule)}</span> : null}
        </p>
      </PopoverContent>
    </Popover>
  )
}

export function HealthRow({ play, perf }: { play: Play; perf: PlayPerformance | null }) {
  const kills: LiveKill[] = perf?.kill_status ?? play.kill_criteria
  const verdict = play.review.verdict
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-2" aria-label="Health">
      <span className="mr-1 text-xs font-medium text-muted-foreground">Kill rules</span>
      {kills.length ? kills.map((k, i) => <KillChip key={i} k={k} />) : <span className="text-xs text-muted-foreground">None set</span>}
      <span className="mx-1 hidden h-4 w-px bg-border sm:block" aria-hidden />
      <span className="flex items-center gap-2 text-xs text-muted-foreground">
        Verdict
        {verdict ? <VerdictChip verdict={verdict} /> : <span className="text-foreground/80">not reviewed yet</span>}
      </span>
      <span className="mx-1 hidden h-4 w-px bg-border sm:block" aria-hidden />
      <span className="text-xs text-muted-foreground">Next check after today’s close</span>
    </div>
  )
}

// ── Entry plan as a stepper ─────────────────────────────────────────────────

export function EntryStepper({
  play,
  onStage,
  staging,
}: {
  play: Play
  onStage?: (index: number) => void
  staging?: boolean
}) {
  const tranches = play.sizing.tranches ?? []
  const done = new Map((play.sizing.staged ?? []).map((s) => [s.index, s]))
  const next = tranches.findIndex((_, i) => !done.has(i))
  if (!tranches.length) {
    return <p className="text-sm text-muted-foreground">No new money planned: what you hold already covers the target.</p>
  }
  return (
    <ol className="grid gap-3 sm:auto-cols-fr sm:grid-flow-col sm:gap-0">
      {tranches.map((t, i) => {
        const s = done.get(i)
        const current = i === next
        const amount = play.budget_gbp * t.pct_of_budget
        return (
          <li key={i} className="relative flex gap-3 sm:block">
            {/* connector */}
            {i < tranches.length - 1 ? (
              <span aria-hidden className={cn('absolute top-[7px] left-[15px] hidden h-px sm:block', s ? 'bg-foreground/50' : 'bg-border')} style={{ right: 0 }} />
            ) : null}
            <span
              className={cn(
                'relative z-10 mt-0.5 flex size-[15px] shrink-0 items-center justify-center rounded-full border-2 sm:mt-0',
                s ? 'border-foreground bg-foreground' : current ? 'border-primary bg-background ring-4 ring-primary/15' : 'border-border bg-background',
              )}
            >
              {s ? <CircleCheck className="size-2.5 text-background" strokeWidth={3} /> : null}
            </span>
            <div
              className={cn(
                'min-w-0 flex-1 sm:mt-3 sm:mr-3 sm:rounded-lg sm:p-3',
                current ? 'rounded-lg border border-primary/30 bg-primary/5 p-3' : 'sm:border sm:border-transparent',
              )}
            >
              <p className="flex items-baseline justify-between gap-2 text-sm">
                <span className="font-medium">{t.label}</span>
                <Money value={amount} decimals={0} className="text-sm" />
              </p>
              <p className="text-xs text-muted-foreground">
                {formatPercent(t.pct_of_budget, 0)} of budget ·{' '}
                {s ? `staged ${shortDate(s.staged_at)}` : t.trigger}
              </p>
              {current && onStage && play.status === 'active' ? (
                <Button size="sm" className="mt-2.5 h-7" onClick={() => onStage(i)} disabled={staging}>
                  {staging ? <Loader2 className="size-3.5 animate-spin" /> : null}
                  Stage {t.label.toLowerCase()}
                </Button>
              ) : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

// ── Reviews ─────────────────────────────────────────────────────────────────

export function ReviewsBlock({ play, reviewing, onReview }: { play: Play; reviewing: boolean; onReview: () => void }) {
  const [all, setAll] = useState(false)
  const history: ReviewEntry[] = play.review.history ?? []
  const [latest, ...older] = history
  return (
    <section className="space-y-3">
      <div className="flex items-end justify-between gap-3">
        <h2 className="text-sm font-semibold tracking-tight">Reviews</h2>
        <Button size="sm" variant="ghost" onClick={onReview} disabled={reviewing}>
          {reviewing ? <Loader2 className="size-3.5 animate-spin" /> : <ArchieIcon className="size-3.5" />}
          Run review
        </Button>
      </div>
      {latest ? (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <VerdictChip verdict={latest.verdict} />
              <span className="text-xs text-muted-foreground">
                {latest.source === 'archie' ? 'Archie' : 'Daily check'} · {shortDate(latest.at)}
              </span>
            </div>
            <p className="text-sm leading-relaxed text-foreground/90">{latest.notes}</p>
          </div>
          {older.length ? (
            <>
              {all ? (
                <ol className="space-y-3 border-t border-border pt-3">
                  {older.slice(0, 8).map((r, i) => (
                    <li key={i} className="space-y-1">
                      <div className="flex items-center gap-2">
                        <VerdictChip verdict={r.verdict} />
                        <span className="text-xs text-muted-foreground">
                          {r.source === 'archie' ? 'Archie' : 'Daily check'} · {shortDate(r.at)}
                        </span>
                      </div>
                      <p className="text-sm leading-relaxed text-muted-foreground">{r.notes}</p>
                    </li>
                  ))}
                </ol>
              ) : null}
              <button type="button" onClick={() => setAll((a) => !a)} className="text-xs text-muted-foreground hover:text-foreground">
                {all ? 'Hide earlier reviews' : `${older.length} earlier review${older.length === 1 ? '' : 's'}`}
              </button>
            </>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          No reviews yet. The daily check runs after the close; run a review for Archie’s read on the thesis now.
        </p>
      )}
    </section>
  )
}
