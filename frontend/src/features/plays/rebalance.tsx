import { useEffect, useState } from 'react'
import { ArrowDownRight, ArrowUpRight, Info, Loader2, ShieldCheck, TriangleAlert } from 'lucide-react'
import { toast } from 'sonner'

import { AccountChip, Money, Segmented } from '@/components/kit'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { toastApiError } from '@/lib/api-error'
import { useAppActions } from '@/lib/app-actions'
import { cn } from '@/lib/utils'
import { formatPercent } from '@/utils/format'
import { placeRebalance, previewRebalance, proposeRebalance } from './api'
import { ownerColor, reloadBook } from './book'
import { displayName, InstrumentLabel, useInstrumentNames } from './names'
import type { Play, PlayLeg, RebalancePlan } from './types'

type Mode = 'sell' | 'cash'

/**
 * Rebalance preview: before → after weight per leg, the per-account trades,
 * fees, then "Propose these trades" (proposals to approve) or "Place them
 * myself" (manual orders, advisory limits, a live confirm). Unsaved weights
 * from the basket editor are previewed as-is and saved before acting.
 */
export function RebalanceDialog({
  play,
  open,
  onOpenChange,
  legs,
  colorIndex = 0,
  saveTargets,
  onDone,
}: {
  play: Play
  open: boolean
  onOpenChange: (v: boolean) => void
  /** Edited legs, when opened from the basket editor. */
  legs?: PlayLeg[] | null
  colorIndex?: number
  /** Persist the edited targets (called before proposing/placing when they differ). */
  saveTargets?: () => Promise<void>
  onDone?: () => void
}) {
  const { navigate, refresh } = useAppActions()
  const [mode, setMode] = useState<Mode>('sell')
  const [plan, setPlan] = useState<RebalancePlan | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<null | 'propose' | 'place'>(null)
  const [confirming, setConfirming] = useState(false)
  const [liveOk, setLiveOk] = useState(false)
  const edited = legs && JSON.stringify(legs.map((l) => [l.ticker, l.target_weight])) !== JSON.stringify(play.legs.map((l) => [l.ticker, l.target_weight]))
  const weights = edited ? Object.fromEntries(legs!.map((l) => [l.ticker, l.target_weight])) : undefined
  const weightsKey = JSON.stringify(weights ?? null)
  useInstrumentNames(plan?.legs.map((l) => l.ticker) ?? [])

  useEffect(() => {
    if (!open) return
    let live = true
    setPlan(null)
    setError(null)
    setConfirming(false)
    previewRebalance(play.id, { weights, use_cash: mode === 'cash' })
      .then((p) => live && setPlan(p))
      .catch((e) => live && setError(e?.response?.data?.detail ?? 'Could not work out the trades right now.'))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, play.id, play.updated_at, mode, weightsKey])

  async function act(kind: 'propose' | 'place') {
    setBusy(kind)
    try {
      if (edited && saveTargets) await saveTargets()
      if (kind === 'propose') {
        const res = await proposeRebalance(play.id, mode === 'cash')
        const n = res.proposed.length
        if (n) {
          toast.success(`${n} rebalance proposal${n === 1 ? '' : 's'} ready for approval`, {
            description: res.failed.length ? `Not proposed: ${res.failed.map((f) => `${displayName(f.ticker)} (${f.reason})`).join('; ')}` : undefined,
            action: { label: 'Review on Home', onClick: () => navigate('/home') },
            duration: 8000,
          })
        } else {
          toast.warning('Nothing was proposed', { description: res.failed.map((f) => `${displayName(f.ticker)}: ${f.reason}`).join('; ') || undefined })
        }
      } else {
        const res = await placeRebalance(play.id, { liveConfirmed: liveOk, useCash: mode === 'cash' })
        const n = res.placed.length
        if (n) toast.success(`${n} order${n === 1 ? '' : 's'} placed`, { description: res.failed.length ? `${res.failed.length} didn't go through.` : undefined })
        for (const f of res.failed) toast.warning(`${displayName(f.ticker)} ${f.side} not placed`, { description: f.reason, duration: 10000 })
        void reloadBook()
      }
      refresh()
      onDone?.()
      onOpenChange(false)
    } catch (e) {
      toastApiError(e, kind === 'propose' ? 'Could not propose the trades' : 'Could not place the orders')
    } finally {
      setBusy(null)
    }
  }

  const color = ownerColor('play', colorIndex)
  const live = plan?.broker_mode === 'live'
  const trades = plan?.trades ?? []
  const max = plan ? Math.max(0.1, ...plan.legs.map((l) => Math.max(l.current_weight, l.after_weight, l.target_weight))) * 1.1 : 1

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-x-hidden overflow-y-auto sm:max-w-2xl [&>*]:min-w-0">
        <DialogHeader>
          <DialogTitle>Rebalance to target</DialogTitle>
          <DialogDescription>
            Brings each holding back to its target share of the play. Drift under 1 point, and any order under £25, is left alone.
            {edited ? ' Previewing your edited targets; they’re saved when you act.' : ''}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <Segmented
            ariaLabel="How to fund the buys"
            value={mode}
            onChange={setMode}
            options={[
              { value: 'sell', label: 'Sell overweight to fund buys' },
              { value: 'cash', label: 'Top up with cash' },
            ]}
          />
          {plan && mode === 'cash' ? (
            <span className="text-xs text-muted-foreground">
              <Money value={plan.free_cash_gbp} decimals={0} /> free in the play’s account
            </span>
          ) : null}
        </div>

        {error ? (
          <p className="rounded-lg bg-warning/10 px-3 py-2 text-sm text-warning">{String(error)}</p>
        ) : !plan ? (
          <div className="space-y-3">
            <Skeleton className="h-36 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : plan.empty_reason ? (
          <p className="text-sm text-muted-foreground">{plan.empty_reason}</p>
        ) : (
          <div className="space-y-6">
            <section>
              <div className="mb-2 flex items-center justify-between gap-2">
                <h3 className="text-xs font-semibold tracking-tight">Weights</h3>
                <span className="flex items-center gap-3 text-[11px] text-muted-foreground">
                  <span className="inline-flex items-center gap-1"><span className="inline-block h-1.5 w-3 rounded-full bg-muted-foreground/35" />now</span>
                  <span className="inline-flex items-center gap-1"><span className="inline-block h-1.5 w-3 rounded-full" style={{ background: color }} />after</span>
                  <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-0.5 rounded-full bg-foreground" />target</span>
                </span>
              </div>
              <ul className="space-y-3">
                {plan.legs.map((l) => (
                  <li key={l.ticker} className="grid grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)_5.5rem] items-center gap-3 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)_6.5rem]">
                    <InstrumentLabel symbol={l.ticker} hint={l.name} iconSize={20} />
                    <div className="space-y-1" aria-label={`${l.ticker}: ${formatPercent(l.current_weight, 0)} now, ${formatPercent(l.after_weight, 0)} after, target ${formatPercent(l.target_weight, 0)}`}>
                      {[{ v: l.current_weight, c: 'color-mix(in oklab, var(--color-muted-foreground) 35%, transparent)' }, { v: l.after_weight, c: color }].map((b, i) => (
                        <div key={i} className="relative h-1.5 rounded-full bg-muted">
                          {b.v > 0 ? <div className="h-full rounded-full" style={{ width: `${Math.max(1.5, (b.v / max) * 100)}%`, background: b.c }} /> : null}
                          <span className="absolute -top-0.5 h-2.5 w-0.5 rounded-full bg-foreground" style={{ left: `calc(${(l.target_weight / max) * 100}% - 1px)` }} />
                        </div>
                      ))}
                    </div>
                    <span className="text-right font-mono text-xs tabular-nums">
                      <span className="text-muted-foreground">{formatPercent(l.current_weight, 0)}</span>
                      <span className="mx-1 text-muted-foreground">→</span>
                      <span className={cn(l.action !== 'hold' && 'font-medium')}>{formatPercent(l.after_weight, 0)}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>

            <section>
              <h3 className="mb-1 text-xs font-semibold tracking-tight">Trades</h3>
              {trades.length ? (
                <ul className="divide-y divide-border/60">
                  {trades.map((t, i) => (
                    <li key={i} className="flex items-center gap-2 py-2.5 sm:gap-3">
                      <span
                        className={cn(
                          'inline-flex w-11 shrink-0 items-center gap-1 text-xs font-medium sm:w-14',
                          t.side === 'sell' ? 'text-negative' : 'text-positive',
                        )}
                      >
                        {t.side === 'sell' ? <ArrowDownRight className="size-3.5" /> : <ArrowUpRight className="size-3.5" />}
                        {t.side === 'sell' ? 'Sell' : 'Buy'}
                      </span>
                      <InstrumentLabel
                        symbol={t.ticker}
                        hint={t.name}
                        iconSize={20}
                        className="min-w-0 flex-1"
                        sub={t.note ?? (t.shares ? `${t.sell_all ? 'all ' : '≈'}${t.shares < 10 ? t.shares.toFixed(3) : t.shares.toFixed(1)} shares` : undefined)}
                      />
                      {t.over_order_limit ? (
                        <span className="hidden shrink-0 items-center gap-1 text-[11px] text-warning sm:inline-flex">
                          <TriangleAlert className="size-3" /> over limit
                        </span>
                      ) : null}
                      <AccountChip kind={t.account_kind} />
                      <Money value={t.notional_gbp} decimals={0} className="w-16 shrink-0 text-right text-sm sm:w-20" />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="py-2 text-sm text-muted-foreground">Nothing to trade: every holding is within 1 point of its target.</p>
              )}
              {trades.length ? (
                <dl className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-3 rounded-lg bg-muted/50 px-3 py-2.5 text-xs">
                  <div>
                    <dt className="text-muted-foreground">Traded</dt>
                    <dd><Money value={plan.turnover_gbp} decimals={0} className="text-sm" /></dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Est. fees</dt>
                    <dd><Money value={plan.est_fees_gbp} decimals={2} className="text-sm" /> <span className="text-muted-foreground">FX 0.15%</span></dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Cash used</dt>
                    <dd><Money value={plan.cash_used_gbp} decimals={0} className="text-sm" /></dd>
                  </div>
                </dl>
              ) : null}
              {plan.ignored.length ? (
                <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
                  <Info className="mt-0.5 size-3 shrink-0" />
                  Left alone: {plan.ignored.map((x) => `${displayName(x.ticker)} (${x.drift > 0 ? '+' : '−'}${Math.abs(x.drift * 100).toFixed(1)} pts, ${x.reason})`).join('; ')}.
                </p>
              ) : null}
              {plan.warnings.map((w, i) => (
                <p key={i} className="mt-2 flex items-start gap-1.5 rounded-lg bg-warning/10 px-3 py-2 text-xs text-warning">
                  <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
                  {w}
                </p>
              ))}
            </section>

            {confirming ? (
              <section className={cn('space-y-2 rounded-lg border px-3 py-3', live ? 'border-warning/50 bg-warning/5' : 'border-border')}>
                <p className="text-sm font-medium">
                  Place {trades.length} order{trades.length === 1 ? '' : 's'} now{live ? ' with real money' : ` (${plan.broker_mode ?? 'paper'})`}?
                </p>
                <p className="text-xs text-muted-foreground">
                  Sells go first, then buys. Your limits are shown as warnings, not refusals; cash and live checks still apply.
                </p>
                {live ? (
                  <label className="flex items-center gap-2 text-xs">
                    <Checkbox checked={liveOk} onCheckedChange={(v) => setLiveOk(Boolean(v))} />
                    I understand these are live orders on my Trading 212 account.
                  </label>
                ) : null}
              </section>
            ) : null}
          </div>
        )}

        <DialogFooter className="items-center gap-2 sm:justify-between">
          <p className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex">
            <ShieldCheck className="size-3.5 text-positive" />
            Proposals wait for your approval.
          </p>
          <div className="flex flex-wrap justify-end gap-2">
            {confirming ? (
              <>
                <Button variant="ghost" size="sm" onClick={() => setConfirming(false)} disabled={busy !== null}>
                  Back
                </Button>
                <Button size="sm" onClick={() => void act('place')} disabled={busy !== null || (live && !liveOk)}>
                  {busy === 'place' ? <Loader2 className="size-3.5 animate-spin" /> : null}
                  Place {trades.length} order{trades.length === 1 ? '' : 's'}
                </Button>
              </>
            ) : (
              <>
                <Button variant="outline" size="sm" onClick={() => setConfirming(true)} disabled={!trades.length || busy !== null}>
                  Place them myself
                </Button>
                <Button size="sm" onClick={() => void act('propose')} disabled={!trades.length || busy !== null}>
                  {busy === 'propose' ? <Loader2 className="size-3.5 animate-spin" /> : null}
                  Propose these trades
                </Button>
              </>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
