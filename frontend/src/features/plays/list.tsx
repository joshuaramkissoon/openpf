import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import { ArrowRight, Plus, Target } from 'lucide-react'

import { Money, MoneyDelta, PageHeader, PctDelta } from '@/components/kit'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { WatchlistBoard } from '@/components/WatchlistBoard'
import { useAppActions } from '@/lib/app-actions'
import { cn } from '@/lib/utils'
import { formatPercent } from '@/utils/format'
import { getPerformance, listPlays } from './api'
import { ArchieMark, Swatch, useBook } from './book'
import { AllocationBar } from './book-card'
import { SortHoldingsSheet } from './suggestions'
import { PLAY_TEMPLATES } from './templates'
import type { BookGroup, Play, PlayPerformance } from './types'
import { LegName } from './names'
import { Sparkline, StatusChip, VerdictChip } from './ui'

type Tab = 'plays' | 'watchlist'

export function firstLine(md: string): string {
  const line =
    md
      .split('\n')
      .map((l) => l.replace(/^#+\s*|[*_`>]/g, '').trim())
      .find((l) => l.length > 0) ?? ''
  const sentence = line.split(/(?<=[.!?])\s/)[0]
  return sentence.length > 160 ? `${sentence.slice(0, 157)}…` : sentence
}

export function usePerformance(play: Play) {
  const [perf, setPerf] = useState<PlayPerformance | null>(null)
  useEffect(() => {
    if (play.status === 'draft') return
    let active = true
    getPerformance(play.id)
      .then((p) => active && setPerf(p))
      .catch(() => {})
    return () => {
      active = false
    }
  }, [play.id, play.status])
  return perf
}

export function PlaysList({ tab }: { tab: Tab }) {
  const { navigate } = useAppActions()
  const [plays, setPlays] = useState<Play[] | null>(null)
  const [sorting, setSorting] = useState(false)
  const { book } = useBook()

  useEffect(() => {
    listPlays()
      .then(setPlays)
      .catch(() => setPlays([]))
  }, [book?.as_of])

  const order = { active: 0, paused: 1, draft: 2, closed: 3 }
  const sorted = (plays ?? []).slice().sort((a, b) => order[a.status] - order[b.status])

  return (
    <div className="space-y-6">
      <PageHeader
        title="Plays"
        description="Theme bets, sized against your book, staged in and watched for you."
        actions={
          <Button size="sm" onClick={() => navigate('/plays?new=1')}>
            <Plus className="size-4" />
            New play
          </Button>
        }
      />
      <div role="tablist" className="flex gap-5 border-b border-border">
        {(
          [
            ['plays', 'Plays'],
            ['watchlist', 'Watchlist'],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            onClick={() => navigate(value === 'plays' ? '/plays' : '/plays?tab=watchlist')}
            className={cn(
              '-mb-px border-b-2 pb-2.5 text-sm transition-colors',
              tab === value ? 'border-foreground font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {label}
            {value === 'plays' && plays?.length ? <span className="ml-1.5 text-xs text-muted-foreground">{plays.length}</span> : null}
          </button>
        ))}
      </div>

      {tab === 'plays' && book && book.holdings.length > 0 ? (
        <div className="space-y-2">
          <AllocationBar book={book} />
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>
              {formatPercent(book.groups.filter((g) => g.kind === 'play').reduce((s, g) => s + g.weight, 0), 0)} of your book is in a play
              {' · '}
              {formatPercent(book.groups.find((g) => g.kind === 'core')?.weight ?? 0, 0)} Core
              {book.unsorted_count ? ` · ${formatPercent(book.unsorted_weight, 0)} unsorted` : ''}
            </span>
            {book.unsorted_count ? (
              <button type="button" onClick={() => setSorting(true)} className="inline-flex items-center gap-1 text-foreground hover:underline">
                <ArchieMark className="size-3" />
                Sort {book.unsorted_count} unsorted with Archie
              </button>
            ) : null}
          </div>
          <SortHoldingsSheet open={sorting} onOpenChange={setSorting} />
        </div>
      ) : null}

      {tab === 'watchlist' ? (
        <WatchlistBoard onError={() => {}} />
      ) : plays === null ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-56 rounded-xl" />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {sorted.map((p) => (
            <PlayCard key={p.id} play={p} />
          ))}
        </div>
      )}
    </div>
  )
}

function PlayCard({ play }: { play: Play }) {
  const { navigate } = useAppActions()
  const perf = usePerformance(play)
  const { book } = useBook()
  const group = book?.groups.find((g) => g.play_id === play.id)
  const open = () => navigate(`/plays/${play.id}`)

  if (play.status === 'draft') return <DraftCard play={play} onOpen={open} />

  const series = perf?.points.map((p) => p.basket) ?? []
  // Only cues that ask for a decision: kill rules, due tranches, size drift, orders to approve.
  const need = group?.needs.find((n) => n.tone === 'critical' || n.tone === 'warn')
  const verdict = play.review.verdict && play.review.verdict !== 'on_track' ? play.review.verdict : null

  return (
    <button
      type="button"
      onClick={open}
      className={cn(
        'group flex min-h-56 flex-col overflow-hidden rounded-xl border border-border bg-card text-left transition-colors hover:border-foreground/25',
        play.status === 'closed' && 'opacity-70',
      )}
    >
      <div className="flex w-full flex-1 flex-col p-5">
        <div className="flex items-start justify-between gap-3">
          <h3 className="flex min-w-0 items-center gap-2 text-base font-semibold tracking-tight">
            {group ? <Swatch kind="play" colorIndex={group.color_index} className="size-2.5" /> : null}
            <span className="truncate">{play.title}</span>
          </h3>
          {verdict ? <VerdictChip verdict={verdict} /> : play.status !== 'active' ? <StatusChip status={play.status} /> : null}
        </div>
        <p className="mt-1 truncate text-sm text-muted-foreground" title={firstLine(play.thesis)}>
          {firstLine(play.thesis) || 'No thesis written yet'}
        </p>

        {/* Owned value and P&L, with the basket's shape since activation */}
        <div className="mt-5 flex items-end justify-between gap-4">
          <div className="min-w-0">
            {group ? (
              <>
                <Money value={group.value_gbp} decimals={0} className="text-xl font-semibold tracking-tight" />
                <p className="mt-0.5 text-xs">
                  <MoneyDelta value={group.ppl_gbp} decimals={0} />
                  {group.ppl_pct != null ? <PctDelta value={group.ppl_pct} className="ml-1.5" /> : null}
                  <span className="ml-1.5 text-muted-foreground">P&amp;L</span>
                </p>
              </>
            ) : (
              <Skeleton className="h-10 w-28" />
            )}
          </div>
          <Sparkline values={series} className="w-24 shrink-0" />
        </div>

        <div className="mt-auto space-y-3 pt-5">
          {group ? <SizeToTarget weight={group.weight} target={group.target_pct ?? 0} /> : null}
          <div className="grid grid-cols-2 gap-3 border-t border-border/60 pt-3 text-xs">
            <NextTranche group={group} />
            <ExitRules perf={perf} />
          </div>
        </div>
      </div>
      {need ? (
        <div
          className={cn(
            'flex w-full items-center gap-2 border-t px-5 py-2.5 text-xs text-foreground',
            need.tone === 'critical' ? 'border-negative/25 bg-negative/5' : 'border-warning/25 bg-warning/5',
          )}
        >
          <span className={cn('size-1.5 shrink-0 rounded-full', need.tone === 'critical' ? 'bg-negative' : 'bg-warning')} />
          <span className="min-w-0 flex-1 truncate">{need.text}</span>
          <span className="inline-flex shrink-0 items-center gap-1 text-muted-foreground group-hover:text-foreground">
            Review <ArrowRight className="size-3" />
          </span>
        </div>
      ) : null}
    </button>
  )
}

/** Owned size against the target: fill to today's weight, a tick at the target. */
function SizeToTarget({ weight, target }: { weight: number; target: number }) {
  if (!target) {
    return (
      <p className="text-xs text-muted-foreground">
        <span className="font-mono text-foreground tabular-nums">{formatPercent(weight, 1)}</span> of your book · no target size
      </p>
    )
  }
  const scale = Math.max(target, weight) * 1.12
  const over = weight > target * 1.3
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground">
          <span className="font-mono text-foreground tabular-nums">{formatPercent(weight, 1)}</span> of book
        </span>
        <span className="text-muted-foreground">
          target <span className="font-mono tabular-nums">{formatPercent(target, 0)}</span>
        </span>
      </div>
      <div
        className="relative h-1.5 rounded-full bg-muted"
        role="img"
        aria-label={`${formatPercent(weight, 1)} of book against a ${formatPercent(target, 0)} target`}
      >
        <div
          className={cn('absolute inset-y-0 left-0 rounded-full', over ? 'bg-warning' : 'bg-foreground/70')}
          style={{ width: `${Math.min(100, (weight / scale) * 100)}%` }}
        />
        <div className="absolute -inset-y-1 w-0.5 rounded-full bg-foreground" style={{ left: `calc(${(target / scale) * 100}% - 1px)` }} />
      </div>
    </div>
  )
}

function NextTranche({ group }: { group?: BookGroup }) {
  let text = '—'
  let of = ''
  if (group) {
    const total = group.tranches_total ?? 0
    const staged = group.tranches_staged ?? 0
    const nt = group.next_tranche
    if (!total) text = 'No new money planned'
    else if (nt) {
      of = ` · ${nt.index + 1} of ${total}`
      text = `${nt.label}${nt.is_due ? ', due now' : nt.due ? `, ${dayjs(nt.due).format('D MMM')}` : ''}`
    } else if (staged >= total) text = `All ${total} staged`
  }
  return (
    <div className="min-w-0">
      <p className="text-[11px] text-muted-foreground">Next tranche{of}</p>
      <p className="mt-0.5 truncate text-foreground">{text}</p>
    </div>
  )
}

const RULE_DOT: Record<string, string> = {
  intact: 'bg-positive',
  near: 'bg-warning',
  tripped: 'bg-negative',
  manual: 'bg-muted-foreground/50',
  unknown: 'border border-muted-foreground/60',
}

function ExitRules({ perf }: { perf: PlayPerformance | null }) {
  const rules = perf?.kill_status ?? []
  const tripped = rules.filter((r) => r.state === 'tripped').length
  const near = rules.filter((r) => r.state === 'near').length
  const checked = rules.filter((r) => r.state === 'intact').length
  const text = !perf
    ? '…'
    : !rules.length
      ? 'None set'
      : tripped
        ? `${tripped} tripped`
        : near
          ? `${near} close to tripping`
          : checked
            ? 'All clear'
            : 'Not checked yet'
  return (
    <div className="min-w-0">
      <p className="text-[11px] text-muted-foreground">Exit rules</p>
      <p className="mt-0.5 flex items-center gap-2">
        {rules.length ? (
          <span className="inline-flex gap-1" aria-hidden>
            {rules.map((r, i) => (
              <span key={i} className={cn('size-1.5 rounded-full', RULE_DOT[r.state] ?? RULE_DOT.unknown)} />
            ))}
          </span>
        ) : null}
        <span className={cn('truncate', tripped ? 'text-negative' : near ? 'text-warning' : 'text-foreground')}>{text}</span>
      </p>
    </div>
  )
}

function DraftCard({ play, onOpen }: { play: Play; onOpen: () => void }) {
  const legs = play.legs.slice(0, 5)
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex min-h-56 flex-col rounded-xl border border-dashed border-border bg-card/50 p-5 text-left transition-colors hover:border-foreground/30"
    >
      <div className="flex w-full items-start justify-between gap-3">
        <h3 className="min-w-0 truncate text-base font-semibold tracking-tight">{play.title}</h3>
        <StatusChip status="draft" />
      </div>
      <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{firstLine(play.thesis)}</p>
      <div className="mt-4 flex flex-wrap gap-1.5">
        {legs.map((l) => (
          <LegName key={l.ticker} symbol={l.ticker} hint={l.name} className="max-w-40 truncate rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-foreground/80" />
        ))}
        {play.legs.length > legs.length ? <span className="px-1 text-[11px] text-muted-foreground">+{play.legs.length - legs.length}</span> : null}
      </div>
      <div className="mt-auto flex w-full items-center justify-between gap-3 border-t border-border/60 pt-3 text-xs">
        <span className="text-muted-foreground">
          <Money value={play.budget_gbp} decimals={0} className="text-foreground" /> suggested · {play.sizing.tranches?.length ?? 0} tranches
        </span>
        <span className="inline-flex items-center gap-1 font-medium text-foreground">
          Review draft <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
        </span>
      </div>
    </button>
  )
}

function EmptyState() {
  const { navigate } = useAppActions()
  const picks = PLAY_TEMPLATES.filter((t) => ['global-core', 'ai-infrastructure', 'defence'].includes(t.id))
  return (
    <div className="relative isolate mx-auto max-w-3xl py-10 text-center">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-72"
        style={{ background: 'radial-gradient(closest-side, color-mix(in oklab, var(--primary) 16%, transparent), transparent)' }}
      />
      <Target className="mx-auto size-6 text-muted-foreground" />
      <h2 className="mt-4 text-2xl font-semibold tracking-tight">What do you believe?</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
        Write it in a sentence. Archie finds the names, sizes them to your book, and you approve each buy.
      </p>
      <Button size="lg" className="mt-6 h-10 px-4" onClick={() => navigate('/plays?new=1')}>
        <Plus className="size-4" />
        New play
      </Button>
      <p className="mt-10 text-xs text-muted-foreground">Or start from a template</p>
      <div className="mt-3 grid gap-3 text-left sm:grid-cols-3">
        {picks.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => navigate(`/plays?new=1&template=${t.id}`)}
            className="group rounded-xl border border-border bg-card p-4 text-left transition-colors duration-150 hover:border-foreground/25 hover:bg-muted/40"
          >
            <span className="flex size-8 items-center justify-center rounded-lg bg-muted text-foreground/80">
              <t.Icon className="size-4" />
            </span>
            <p className="mt-3 text-sm font-medium">{t.name}</p>
            <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{t.pitch}</p>
            <p className="mt-3 inline-flex items-center gap-1 text-xs text-muted-foreground group-hover:text-foreground">
              Use template <ArrowRight className="size-3" />
            </p>
          </button>
        ))}
      </div>
    </div>
  )
}

/** Compact summary of active plays for Home. Renders nothing when there are none. */
export function PlaysHomeSummary() {
  const { navigate } = useAppActions()
  const [plays, setPlays] = useState<Play[]>([])
  useEffect(() => {
    listPlays('active')
      .then(setPlays)
      .catch(() => setPlays([]))
  }, [])
  if (!plays.length) return null
  return (
    <section className="min-w-0">
      <div className="mb-3 flex items-end justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">Plays</h2>
          <p className="text-xs text-muted-foreground">
            {plays.length} active · {formatPercent(plays.reduce((s, p) => s + p.budget_pct_of_book, 0), 0)} of book committed
          </p>
        </div>
        <button
          type="button"
          onClick={() => navigate('/plays')}
          className="rounded-md px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          All plays →
        </button>
      </div>
      <div className="divide-y divide-border rounded-xl border border-border">
        {plays.slice(0, 4).map((p) => (
          <HomeRow key={p.id} play={p} />
        ))}
      </div>
    </section>
  )
}

function HomeRow({ play }: { play: Play }) {
  const { navigate } = useAppActions()
  const perf = usePerformance(play)
  return (
    <button
      type="button"
      onClick={() => navigate(`/plays/${play.id}`)}
      className="flex w-full items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-muted/40"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{play.title}</p>
        <p className="text-xs text-muted-foreground">
          {(play.sizing.staged ?? []).length}/{(play.sizing.tranches ?? []).length} tranches · <Money value={play.budget_gbp} decimals={0} />
        </p>
      </div>
      <Sparkline values={perf?.points.map((x) => x.basket) ?? []} className="hidden w-24 sm:block" />
      <div className="w-16 text-right">
        {perf?.basket_return != null ? <PctDelta value={perf.basket_return} className="text-sm" /> : <span className="text-sm text-muted-foreground">—</span>}
      </div>
      <VerdictChip verdict={play.review.verdict} className="hidden sm:inline-flex" />
    </button>
  )
}
