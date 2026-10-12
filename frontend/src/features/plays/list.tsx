import { useEffect, useState } from 'react'
import { ArrowRight, Plus, Target } from 'lucide-react'

import { Money, PageHeader, PctDelta } from '@/components/kit'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { WatchlistBoard } from '@/components/WatchlistBoard'
import { useAppActions } from '@/lib/app-actions'
import { cn } from '@/lib/utils'
import { formatPercent } from '@/utils/format'
import { getPerformance, listPlays } from './api'
import { EXAMPLE_IDEAS } from './new-play'
import type { Play, PlayPerformance } from './types'
import { ConvictionDots, Meter, Sparkline, StatusChip, VerdictChip } from './ui'

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

  useEffect(() => {
    listPlays()
      .then(setPlays)
      .catch(() => setPlays([]))
  }, [])

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

      {tab === 'watchlist' ? (
        <WatchlistBoard onError={() => {}} />
      ) : plays === null ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-48 rounded-xl" />
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
  const deployed = perf && perf.target_exposure_gbp ? perf.held_gbp / perf.target_exposure_gbp : 0
  const series = perf?.points.map((p) => p.basket) ?? []

  return (
    <button
      type="button"
      onClick={() => navigate(`/plays/${play.id}`)}
      className={cn(
        'group flex min-h-48 flex-col rounded-xl border border-border bg-card p-5 text-left transition-colors hover:border-foreground/25',
        play.status === 'closed' && 'opacity-70',
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <StatusChip status={play.status} />
        <VerdictChip verdict={play.status === 'draft' ? null : play.review.verdict} />
      </div>
      <h3 className="mt-3 text-base font-semibold tracking-tight text-balance">{play.title}</h3>
      <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{firstLine(play.thesis)}</p>

      <div className="mt-auto space-y-3 pt-5">
        {play.status === 'draft' ? (
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {play.legs.length} legs · <Money value={play.budget_gbp} decimals={0} /> suggested
            </span>
            <span className="inline-flex items-center gap-1 text-foreground opacity-0 transition-opacity group-hover:opacity-100">
              Review <ArrowRight className="size-3" />
            </span>
          </div>
        ) : (
          <>
            <div className="flex items-end justify-between gap-4">
              <div className="min-w-0">
                <p className="text-[11px] text-muted-foreground">Since activation</p>
                {perf ? (
                  perf.basket_return != null ? (
                    <PctDelta value={perf.basket_return} className="text-lg font-semibold" />
                  ) : (
                    <span className="font-mono text-lg text-muted-foreground">—</span>
                  )
                ) : (
                  <Skeleton className="mt-1 h-6 w-16" />
                )}
              </div>
              <Sparkline values={series} className="w-28" />
            </div>
            <div className="space-y-1.5">
              <Meter value={deployed} />
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  {perf ? <Money value={perf.held_gbp} decimals={0} /> : '—'} of {perf ? <Money value={perf.target_exposure_gbp} decimals={0} /> : '—'}
                </span>
                <ConvictionDots conviction={play.conviction} />
              </div>
            </div>
          </>
        )}
      </div>
    </button>
  )
}

function EmptyState() {
  const { navigate } = useAppActions()
  return (
    <div className="mx-auto max-w-3xl py-10 text-center">
      <Target className="mx-auto size-6 text-muted-foreground" />
      <h2 className="mt-4 text-lg font-semibold tracking-tight">Bet on what you believe — sized like a professional</h2>
      <p className="mx-auto mt-2 max-w-lg text-sm text-muted-foreground">
        Write a thesis in plain English. Archie turns it into a basket sized against your book, staged in over weeks, with kill
        criteria it watches for you. Every order waits for your approval.
      </p>
      <div className="mt-8 grid gap-3 text-left sm:grid-cols-3">
        {EXAMPLE_IDEAS.map((ex) => (
          <button
            key={ex.title}
            type="button"
            onClick={() => navigate(`/plays?new=1&idea=${encodeURIComponent(ex.idea)}`)}
            className="group rounded-xl border border-border bg-card p-4 transition-colors hover:border-foreground/25"
          >
            <p className="text-sm font-medium">{ex.title}</p>
            <p className="mt-1 line-clamp-3 text-xs leading-relaxed text-muted-foreground">{ex.idea}</p>
            <p className="mt-3 inline-flex items-center gap-1 text-xs text-muted-foreground group-hover:text-foreground">
              Draft this <ArrowRight className="size-3" />
            </p>
          </button>
        ))}
      </div>
      <Button className="mt-8" variant="outline" onClick={() => navigate('/plays?new=1')}>
        Start from your own idea
      </Button>
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
