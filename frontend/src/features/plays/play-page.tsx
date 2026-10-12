import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Loader2, MessageCircle, MoreHorizontal, Sparkles } from 'lucide-react'
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { toast } from 'sonner'

import { Money, PctDelta } from '@/components/kit'
import { RichMarkdown } from '@/components/RichMarkdown'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { toastApiError } from '@/lib/api-error'
import { useAppActions } from '@/lib/app-actions'
import { cn } from '@/lib/utils'
import { accountLabel, formatPercent } from '@/utils/format'
import { deletePlay, getPerformance, getPlay, runReview, stageTranche, updatePlay } from './api'
import { BasketEditor, Block, KillList, LegsTable, OverlapCallout, SizingPanel, TranchePlan } from './parts'
import type { Play, PlayLeg, PlayPerformance, StageResult } from './types'
import { ConvictionDots, Eyebrow, Meter, StatusChip, VerdictChip } from './ui'

export function playContextPrompt(play: Play): string {
  const legs = play.legs.map((l) => `${l.ticker} ${Math.round(l.target_weight * 100)}%`).join(', ')
  return [
    `Let's talk about my play "${play.title}" (${play.status}, ${play.conviction} conviction, ${accountLabel(play.account_kind)}, budget £${Math.round(play.budget_gbp)}).`,
    `Basket: ${legs}.`,
    `Kill criteria: ${play.kill_criteria.map((k) => k.text).join('; ')}.`,
    `What would change my mind: ${play.what_would_change_my_mind}`,
    `Play id: ${play.id}.`,
    '',
    'Pressure-test this: ',
  ].join('\n')
}

function stageToast(res: StageResult, navigate: (p: string) => void) {
  const n = res.intents.length
  if (n === 0) {
    const why = [...res.rail_breaches.map((b) => `${b.ticker}: ${b.breaches[0]}`), ...res.skipped.map((x) => `${x.ticker}: ${x.reason}`)]
    toast.warning('Nothing was staged', { description: why.slice(0, 3).join(' · ') || undefined, duration: 8000 })
    return
  }
  const breaches = res.rail_breaches.length
  toast.success(`${n} proposal${n === 1 ? '' : 's'} ready for approval`, {
    description:
      `${res.tranche.label} tranche · £${Math.round(res.notional_gbp).toLocaleString()}` +
      (breaches ? ` · ${res.rail_breaches.map((b) => b.ticker).join(', ')} held back by risk rails` : '') +
      (res.skipped.length ? ` · skipped ${res.skipped.map((s) => s.ticker).join(', ')}` : ''),
    action: { label: 'Review on Home', onClick: () => navigate('/home') },
    duration: 8000,
  })
  for (const w of res.warnings) toast.warning(w)
}

function BackLink() {
  const { navigate } = useAppActions()
  return (
    <button
      type="button"
      onClick={() => navigate('/plays')}
      className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-3.5" />
      Plays
    </button>
  )
}

export function PlayPage({ id }: { id: string }) {
  const [play, setPlay] = useState<Play | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setPlay(await getPlay(id))
    } catch {
      setError('This play does not exist or was deleted.')
    }
  }, [id])

  useEffect(() => {
    void load()
  }, [load])

  if (error) {
    return (
      <div className="space-y-4">
        <BackLink />
        <p className="text-sm text-muted-foreground">{error}</p>
      </div>
    )
  }
  if (!play) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-4 w-16" />
        <Skeleton className="h-8 w-2/3" />
        <div className="grid gap-10 lg:grid-cols-5">
          <Skeleton className="h-72 lg:col-span-3" />
          <Skeleton className="h-72 lg:col-span-2" />
        </div>
      </div>
    )
  }
  return play.status === 'draft' ? <DraftReview play={play} onChange={setPlay} /> : <PlayDetail play={play} onChange={setPlay} />
}

// ── Draft review ────────────────────────────────────────────────────────────

function DraftReview({ play, onChange }: { play: Play; onChange: (p: Play) => void }) {
  const { navigate, askArchie, refresh } = useAppActions()
  const [legs, setLegs] = useState<PlayLeg[]>(play.legs)
  const [busy, setBusy] = useState<null | 'save' | 'activate' | 'stage' | 'discard'>(null)
  const dirty = useMemo(() => JSON.stringify(legs) !== JSON.stringify(play.legs), [legs, play.legs])

  useEffect(() => setLegs(play.legs), [play.legs])

  async function save(): Promise<Play> {
    const next = await updatePlay(play.id, { legs, resize: true })
    onChange(next)
    return next
  }

  async function run(kind: 'save' | 'activate' | 'stage') {
    setBusy(kind)
    try {
      let p = play
      if (dirty) p = await save()
      if (kind === 'save') {
        toast.success('Basket updated and re-sized')
        return
      }
      p = await updatePlay(p.id, { status: 'active' })
      if (kind === 'stage') {
        const res = await stageTranche(p.id, 0)
        stageToast(res, navigate)
        refresh()
        p = await getPlay(p.id)
      } else {
        toast.success('Play activated', { description: 'Stage the first tranche when you are ready.' })
      }
      onChange(p)
    } catch (e) {
      toastApiError(e, 'Could not update the play')
    } finally {
      setBusy(null)
    }
  }

  async function discard() {
    setBusy('discard')
    try {
      await deletePlay(play.id)
      navigate('/plays')
    } catch (e) {
      toastApiError(e, 'Could not discard')
      setBusy(null)
    }
  }

  return (
    <div className="space-y-8 pb-28">
      <div className="space-y-3">
        <BackLink />
        <div className="space-y-2">
          <Eyebrow>
            Draft · {accountLabel(play.account_kind)} · {play.horizon_months >= 12 ? `${play.horizon_months / 12}y` : `${play.horizon_months}m`} horizon
          </Eyebrow>
          <h1 className="text-2xl font-semibold tracking-tight text-balance">{play.title}</h1>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <ConvictionDots conviction={play.conviction} />
            {play.meta.conviction_source === 'archie' ? <span className="text-xs text-muted-foreground">suggested by Archie</span> : null}
          </div>
        </div>
        {play.meta.idea ? (
          <p className="max-w-2xl border-l-2 border-border pl-3 text-sm text-muted-foreground italic">“{play.meta.idea}”</p>
        ) : null}
      </div>

      <div className="grid gap-x-10 gap-y-10 lg:grid-cols-5">
        <div className="min-w-0 space-y-10 lg:col-span-3">
          <Block title="Thesis">
            <div className="text-sm leading-relaxed">
              <RichMarkdown markdown={play.thesis} />
            </div>
          </Block>
          <Block
            title="Basket"
            description={`${legs.length} legs · edit weights or remove a leg, then re-size`}
            action={
              dirty ? (
                <Button size="sm" variant="outline" onClick={() => void run('save')} disabled={busy !== null}>
                  {busy === 'save' ? <Loader2 className="size-3.5 animate-spin" /> : null}
                  Re-size
                </Button>
              ) : null
            }
          >
            <BasketEditor legs={legs} allocations={play.sizing.allocations} onChange={setLegs} />
          </Block>
          <Block title="Kill criteria" description="Checked daily. Archie flags it on Home when one trips.">
            <KillList items={play.kill_criteria} wwcmm={play.what_would_change_my_mind} />
          </Block>
          {play.meta.key_risks?.length ? (
            <Block title="Key risks">
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                {play.meta.key_risks.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </Block>
          ) : null}
        </div>
        <div className="min-w-0 space-y-10 lg:col-span-2">
          <Block title="Sizing" description="Why this much">
            <div className={cn('space-y-4 transition-opacity', dirty && 'opacity-50')}>
              <SizingPanel play={play} />
              <OverlapCallout play={play} />
            </div>
          </Block>
          <Block title="Entry plan" description={`Max loss at a 40% drawdown: £${Math.round(play.sizing.max_loss_gbp ?? 0).toLocaleString()}`}>
            <TranchePlan play={play} staged={play.sizing.staged ?? []} />
          </Block>
        </div>
      </div>

      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/75 lg:left-[var(--sidebar-width,0px)]">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" onClick={() => void discard()} disabled={busy !== null}>
              Discard
            </Button>
            <Button variant="ghost" size="sm" onClick={() => askArchie(playContextPrompt(play))}>
              <MessageCircle className="size-4" />
              <span className="hidden sm:inline">Discuss with Archie</span>
              <span className="sm:hidden">Discuss</span>
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => void run('activate')} disabled={busy !== null}>
              {busy === 'activate' ? <Loader2 className="size-3.5 animate-spin" /> : null}
              Activate
            </Button>
            <Button size="sm" onClick={() => void run('stage')} disabled={busy !== null || play.budget_gbp <= 0}>
              {busy === 'stage' ? <Loader2 className="size-3.5 animate-spin" /> : null}
              Activate & stage first tranche
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Active / paused / closed detail ─────────────────────────────────────────

function PlayDetail({ play, onChange }: { play: Play; onChange: (p: Play) => void }) {
  const { navigate, askArchie, refresh } = useAppActions()
  const [perf, setPerf] = useState<PlayPerformance | null>(null)
  const [perfError, setPerfError] = useState(false)
  const [staging, setStaging] = useState(false)
  const [reviewing, setReviewing] = useState(false)
  const [editing, setEditing] = useState(false)
  const [legs, setLegs] = useState<PlayLeg[]>(play.legs)

  const loadPerf = useCallback(async () => {
    try {
      setPerf(await getPerformance(play.id))
      setPerfError(false)
    } catch {
      setPerfError(true)
    }
  }, [play.id])

  useEffect(() => {
    void loadPerf()
  }, [loadPerf, play.updated_at])

  async function stage(index?: number) {
    setStaging(true)
    try {
      const res = await stageTranche(play.id, index)
      stageToast(res, navigate)
      refresh()
      onChange(await getPlay(play.id))
    } catch (e) {
      toastApiError(e, 'Could not stage the tranche')
    } finally {
      setStaging(false)
    }
  }

  async function setStatus(status: Play['status']) {
    try {
      onChange(await updatePlay(play.id, { status }))
    } catch (e) {
      toastApiError(e, 'Could not update the play')
    }
  }

  async function review() {
    setReviewing(true)
    try {
      onChange(await runReview(play.id))
      toast.success('Review updated')
    } catch (e) {
      toastApiError(e, 'Review failed')
    } finally {
      setReviewing(false)
    }
  }

  async function saveLegs() {
    try {
      onChange(await updatePlay(play.id, { legs, resize: true }))
      setEditing(false)
      toast.success('Basket updated', { description: 'Future tranches use the new weights.' })
    } catch (e) {
      toastApiError(e, 'Could not save the basket')
    }
  }

  async function remove() {
    try {
      await deletePlay(play.id)
      navigate('/plays')
    } catch (e) {
      toastApiError(e, 'Could not delete')
    }
  }

  const staged = play.sizing.staged ?? []
  const tranches = play.sizing.tranches ?? []
  const remaining = tranches.length - staged.length
  const verdict = play.review.verdict
  const deployed = perf ? (perf.target_exposure_gbp ? perf.held_gbp / perf.target_exposure_gbp : 0) : null
  const excess = perf && perf.basket_return != null && perf.benchmark_return != null ? perf.basket_return - perf.benchmark_return : null

  return (
    <div className="space-y-10">
      <div className="space-y-3">
        <BackLink />
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 space-y-2">
            <h1 className="text-2xl font-semibold tracking-tight text-balance">{play.title}</h1>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <StatusChip status={play.status} />
              <ConvictionDots conviction={play.conviction} />
              <span className="text-xs text-muted-foreground">{accountLabel(play.account_kind)}</span>
              {play.activated_at ? (
                <span className="text-xs text-muted-foreground">
                  since {new Date(play.activated_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}
                </span>
              ) : null}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {play.status === 'active' && remaining > 0 ? (
              <Button size="sm" onClick={() => void stage()} disabled={staging}>
                {staging ? <Loader2 className="size-3.5 animate-spin" /> : null}
                Stage next tranche
              </Button>
            ) : null}
            <Button size="sm" variant="outline" onClick={() => askArchie(playContextPrompt(play))}>
              <MessageCircle className="size-4" />
              <span className="hidden sm:inline">Ask Archie</span>
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger
                aria-label="More actions"
                className="inline-flex size-8 items-center justify-center rounded-lg border border-border text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                <MoreHorizontal className="size-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-44">
                <DropdownMenuItem onClick={() => void review()} disabled={reviewing}>
                  <Sparkles className="size-4" />
                  Run review
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setEditing(true)} disabled={play.status === 'closed'}>
                  Edit basket
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                {play.status === 'active' ? (
                  <DropdownMenuItem onClick={() => void setStatus('paused')}>Pause</DropdownMenuItem>
                ) : null}
                {play.status === 'paused' ? (
                  <DropdownMenuItem onClick={() => void setStatus('active')}>Resume</DropdownMenuItem>
                ) : null}
                {play.status !== 'closed' ? (
                  <DropdownMenuItem onClick={() => void setStatus('closed')}>Close play</DropdownMenuItem>
                ) : (
                  <DropdownMenuItem onClick={() => void setStatus('active')}>Reopen</DropdownMenuItem>
                )}
                <DropdownMenuItem variant="destructive" onClick={() => void remove()}>
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-8 gap-y-6 border-y border-border py-5 lg:grid-cols-4">
        <Stat label="New money">
          <Money value={play.budget_gbp} decimals={0} />
          <Sub>{formatPercent(play.budget_pct_of_book, 1)} of book</Sub>
        </Stat>
        <Stat label="Exposure">
          {perf ? <Money value={perf.held_gbp} decimals={0} /> : <Skeleton className="h-7 w-24" />}
          {deployed != null ? <Meter value={deployed} className="mt-2" /> : null}
          <Sub>
            {perf ? <>of <Money value={perf.target_exposure_gbp} decimals={0} /> target · </> : null}
            {staged.length}/{tranches.length} tranches
          </Sub>
        </Stat>
        <Stat label={`Return vs ${perf?.benchmark ?? 'SPY'}`}>
          {perf ? (
            perf.basket_return != null ? <PctDelta value={perf.basket_return} /> : <span className="text-muted-foreground">—</span>
          ) : (
            <Skeleton className="h-7 w-20" />
          )}
          {excess != null ? (
            <Sub>
              {excess >= 0 ? 'Ahead' : 'Behind'} by {formatPercent(Math.abs(excess), 1)}
            </Sub>
          ) : perf && perf.basket_return == null ? (
            <Sub>Tracks from the first full day</Sub>
          ) : null}
        </Stat>
        <Stat label="Verdict">
          {verdict ? <VerdictChip verdict={verdict} className="text-sm" /> : <span className="font-sans text-sm font-normal text-muted-foreground">Not reviewed yet</span>}
          {play.review.last_reviewed_at ? (
            <Sub>{new Date(play.review.last_reviewed_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}</Sub>
          ) : null}
        </Stat>
      </div>

      {perf && perf.points.length > 1 ? (
        <Block title="Basket vs benchmark" description={`Target-weighted, since activation · ${perf.benchmark} dashed`}>
          <PerfChart points={perf.points} />
        </Block>
      ) : null}

      <Block
        title="Basket"
        description={editing ? 'Adjust weights — future tranches use the new mix' : 'Target vs what you hold in this account'}
        action={
          editing ? (
            <>
              <Button size="sm" variant="ghost" onClick={() => { setLegs(play.legs); setEditing(false) }}>
                Cancel
              </Button>
              <Button size="sm" onClick={() => void saveLegs()}>
                Save
              </Button>
            </>
          ) : null
        }
      >
        {editing ? (
          <BasketEditor legs={legs} allocations={play.sizing.allocations} onChange={setLegs} />
        ) : perf ? (
          <LegsTable legs={perf.legs} built={perf.basket_built} />
        ) : perfError ? (
          <p className="text-sm text-muted-foreground">Live positions are unavailable right now.</p>
        ) : (
          <Skeleton className="h-40 w-full" />
        )}
      </Block>

      <div className="grid gap-x-10 gap-y-10 lg:grid-cols-2">
        <Block title="Entry plan" description={remaining > 0 ? `${remaining} tranche${remaining === 1 ? '' : 's'} to go` : 'Fully staged'}>
          <TranchePlan play={play} staged={staged} onStage={(i) => void stage(i)} staging={staging} />
        </Block>
        <Block title="Kill criteria" description="Live status, checked daily">
          <KillList items={perf?.kill_status ?? play.kill_criteria} wwcmm={play.what_would_change_my_mind} />
        </Block>
      </div>

      <div className="grid gap-x-10 gap-y-10 lg:grid-cols-5">
        <Block title="Thesis" className="lg:col-span-3">
          <div className="text-sm leading-relaxed">
            <RichMarkdown markdown={play.thesis} />
          </div>
        </Block>
        <Block
          title="Reviews"
          className="lg:col-span-2"
          action={
            <Button size="sm" variant="ghost" onClick={() => void review()} disabled={reviewing}>
              {reviewing ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
              Run review
            </Button>
          }
        >
          {play.review.history?.length ? (
            <ol className="space-y-4">
              {play.review.history.slice(0, 8).map((r, i) => (
                <li key={i} className="space-y-1">
                  <div className="flex items-center gap-2">
                    <VerdictChip verdict={r.verdict} />
                    <span className="text-xs text-muted-foreground">
                      {r.source === 'archie' ? 'Archie' : 'Daily check'} ·{' '}
                      {new Date(r.at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
                    </span>
                  </div>
                  <p className="text-sm leading-relaxed text-muted-foreground">{r.notes}</p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-muted-foreground">
              The daily check runs after the close. Run a review for Archie's read on the thesis now.
            </p>
          )}
        </Block>
      </div>
    </div>
  )
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <Eyebrow className="mb-1">{label}</Eyebrow>
      <div className="font-mono text-xl font-semibold tracking-tight tabular-nums">{children}</div>
    </div>
  )
}

function Sub({ children }: { children: React.ReactNode }) {
  return <span className="mt-1 block font-sans text-xs font-normal tracking-normal text-muted-foreground">{children}</span>
}

function PerfChart({ points }: { points: PlayPerformance['points'] }) {
  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <XAxis
            dataKey="date"
            tickLine={false}
            axisLine={false}
            minTickGap={40}
            tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
            tickFormatter={(d: string) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            width={48}
            tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
            tickFormatter={(v: number) => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`}
          />
          <Tooltip
            contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: 'var(--muted-foreground)' }}
            formatter={(v: number, name: string) => [`${v > 0 ? '+' : ''}${v.toFixed(2)}%`, name === 'basket' ? 'Basket' : 'Benchmark']}
          />
          <Line type="monotone" dataKey="benchmark" stroke="var(--muted-foreground)" strokeDasharray="4 4" strokeWidth={1.25} dot={false} />
          <Line type="monotone" dataKey="basket" stroke="var(--foreground)" strokeWidth={1.75} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
