import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Loader2, MoreHorizontal, Scale, TriangleAlert } from 'lucide-react'
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { toast } from 'sonner'

import { ArchieIcon } from '@/components/kit'
import { RichMarkdown } from '@/components/RichMarkdown'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
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
import { accountLabel } from '@/utils/format'
import { deletePlay, getPerformance, getPlay, runReview, stageTranche, updatePlay } from './api'
import { reloadBook, useBook } from './book'
import { BasketEditor, Block, KillList, OverlapCallout, SizingPanel, TranchePlan } from './parts'
import { EntryStepper, HealthRow, nextActions, PlayHero, ReviewsBlock, ThesisQuote } from './detail'
import { LegName } from './names'
import { RebalanceDialog } from './rebalance'
import { driftedCount, holdingRows, PlayRings } from './rings'
import type { Play, PlayLeg, PlayPerformance, StageResult } from './types'
import { ConvictionDots, Eyebrow, StatusChip } from './ui'

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
        void reloadBook()
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
            {play.meta.dropped_legs?.length ? (
              <p className="mt-3 flex items-start gap-2 rounded-lg bg-warning/10 px-3 py-2 text-xs text-warning">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
                <span>
                  Left out, not listed on Trading 212:{' '}
                  {play.meta.dropped_legs.map((t, i) => (
                    <span key={t}>
                      {i ? ', ' : ''}
                      <LegName symbol={t} className="font-medium" />
                    </span>
                  ))}
                  .
                </span>
              </p>
            ) : null}
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
          <Block title="Entry plan" description={`Max loss at a ${Math.round(((play.sizing as { stress_drawdown?: number }).stress_drawdown ?? 0.4) * 100)}% drawdown: £${Math.round(play.sizing.max_loss_gbp ?? 0).toLocaleString()}`}>
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
              <ArchieIcon className="size-4" />
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
  const [rebalancing, setRebalancing] = useState<null | 'saved' | 'edited'>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [legs, setLegs] = useState<PlayLeg[]>(play.legs)
  const { book } = useBook(play.updated_at)
  const group = book?.groups.find((g) => g.play_id === play.id) ?? null

  useEffect(() => setLegs(play.legs), [play.legs])

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
      void reloadBook()
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
      void reloadBook()
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

  async function persistLegs() {
    onChange(await updatePlay(play.id, { legs, resize: true }))
  }

  async function saveLegs() {
    try {
      await persistLegs()
      setEditing(false)
      toast.success('Basket updated', { description: 'Future tranches use the new weights.' })
    } catch (e) {
      toastApiError(e, 'Could not save the basket')
    }
  }

  async function remove() {
    try {
      const n = group?.holdings_count ?? 0
      await deletePlay(play.id)
      void reloadBook()
      toast.success(`Deleted “${play.title}”`, {
        description: n ? `${n} holding${n === 1 ? '' : 's'} moved to Unsorted. Nothing was sold.` : undefined,
      })
      navigate('/plays')
    } catch (e) {
      toastApiError(e, 'Could not delete')
    }
  }

  const rows = group ? holdingRows(play, group) : []
  const drifted = driftedCount(rows)
  const actions = nextActions({
    play, perf, drifted, staging, reviewing,
    onStage: (i) => void stage(i),
    onRebalance: () => setRebalancing('saved'),
    onReview: () => void review(),
  })
  const horizon = play.horizon_months >= 12 ? `${+(play.horizon_months / 12).toFixed(1)}y horizon` : `${play.horizon_months}m horizon`

  return (
    <div className="space-y-8">
      <header className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <BackLink />
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => askArchie(playContextPrompt(play))}>
              <ArchieIcon className="size-4" />
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
                  <ArchieIcon className="size-4" />
                  Run review
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setEditing(true)} disabled={play.status === 'closed'}>
                  Edit basket
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setRebalancing('saved')} disabled={play.status === 'closed' || !rows.some((r) => r.held)}>
                  Rebalance
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
                <DropdownMenuItem variant="destructive" onClick={() => setConfirmDelete(true)}>
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete “{play.title}”?</AlertDialogTitle>
                  <AlertDialogDescription>
                    {group?.holdings_count
                      ? `Its ${group.holdings_count} holding${group.holdings_count === 1 ? '' : 's'} move to Unsorted — nothing is sold. `
                      : ''}
                    Proposals it already staged stay in your queue. To keep the history, close the play instead.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction variant="destructive" onClick={() => void remove()}>
                    Delete play
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <StatusChip status={play.status} />
            <ConvictionDots conviction={play.conviction} />
            <span className="text-xs text-muted-foreground">{accountLabel(play.account_kind)}</span>
            <span className="text-xs text-muted-foreground">{horizon}</span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-balance">{play.title}</h1>
        </div>
        <ThesisQuote thesis={play.thesis} />
      </header>

      <PlayHero play={play} group={group} perf={perf} actions={actions} />
      <HealthRow play={play} perf={perf} />
      {perfError && !perf ? <p className="-mt-4 text-xs text-muted-foreground">Live performance is unavailable right now.</p> : null}

      {editing ? (
        <Block
          title="Basket plan"
          description="Adjust target weights — future tranches use the new mix"
          action={
            <>
              <Button size="sm" variant="ghost" onClick={() => { setLegs(play.legs); setEditing(false) }}>
                Cancel
              </Button>
              <Button size="sm" variant="outline" onClick={() => setRebalancing('edited')} disabled={!rows.some((r) => r.held)}>
                <Scale className="size-3.5" />
                Rebalance
              </Button>
              <Button size="sm" onClick={() => void saveLegs()}>
                Save
              </Button>
            </>
          }
        >
          <BasketEditor legs={legs} allocations={play.sizing.allocations} onChange={setLegs} />
        </Block>
      ) : null}

      <PlayRings play={play} onPlayChange={onChange} onRebalance={() => setRebalancing('saved')} />

      {perf && perf.points.length > 1 ? (
        <Block title="Basket vs benchmark" description={`Target-weighted, since activation · ${perf.benchmark} dashed`}>
          <PerfChart points={perf.points} />
        </Block>
      ) : null}

      <Block
        title="Entry plan"
        description={(() => {
          const t = play.sizing.tranches ?? []
          const left = t.length - (play.sizing.staged ?? []).length
          return t.length ? (left > 0 ? `New money in ${t.length} steps · ${left} to go` : 'Fully staged') : undefined
        })()}
      >
        <EntryStepper play={play} onStage={(i) => void stage(i)} staging={staging} />
      </Block>

      <div className="grid gap-x-10 gap-y-10 lg:grid-cols-2">
        <Block title="Kill criteria" description="Checked after every close">
          <KillList items={perf?.kill_status ?? play.kill_criteria} wwcmm={play.what_would_change_my_mind} />
        </Block>
        <ReviewsBlock play={play} reviewing={reviewing} onReview={() => void review()} />
      </div>

      <RebalanceDialog
        play={play}
        open={rebalancing !== null}
        onOpenChange={(v) => !v && setRebalancing(null)}
        legs={rebalancing === 'edited' ? legs : null}
        colorIndex={group?.color_index ?? 0}
        saveTargets={async () => {
          await persistLegs()
          setEditing(false)
        }}
        onDone={() => void reloadBook()}
      />
    </div>
  )
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
