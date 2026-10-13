import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { Ban, Check, CheckCheck, Clock, FileQuestion, Hourglass, Loader2, ShieldCheck, X } from 'lucide-react'

import { getIntentsByIds } from '@/api/client'
import { useDecisions } from '@/app/hooks/use-proposals'
import type { ExecAccount } from '@/app/hooks/use-proposals'
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
import { ProposalRow, ago, railCheck } from '@/features/home/decision-rows'
import { proposalPrompt } from '@/features/home/decisions-card'
import { useAppActions } from '@/lib/app-actions'
import { privacyBlurClass, usePrivacyMode } from '@/lib/privacy'
import { cn } from '@/lib/utils'
import { formatMoney } from '@/utils/format'
import type { TradeIntent } from '@/types'

/**
 * Proposals referenced in a transcript or report — `[label](proposal:<id>)`.
 *
 * Only the id is stored; the card always shows the intent's *current* state
 * from the shell's shared proposals hook (so approving here updates Home and
 * Activity, and vice versa). Ids outside the hook's recent list are fetched
 * directly. Pending proposals are prominent; resolved ones collapse to a
 * one-line receipt.
 */

/** All proposal ids in the current message, so several render as one group. */
export const ProposalGroupContext = createContext<string[] | null>(null)

const PENDING = new Set(['proposed', 'approved', 'executing'])

function useIntents(ids: string[]): { byId: Map<string, TradeIntent>; missing: Set<string> } {
  const decisions = useDecisions()
  const listed = decisions?.proposals.intents
  const [fetched, setFetched] = useState<Map<string, TradeIntent>>(new Map())
  const [notFound, setNotFound] = useState<Set<string>>(new Set())
  const key = ids.join(',')
  const reloadRequested = useRef(false)

  const listedMap = useMemo(() => new Map((listed ?? []).map((i) => [i.id, i])), [listed])
  const absent = ids.filter((id) => !listedMap.has(id))
  const absentKey = absent.join(',')

  useEffect(() => {
    if (!absentKey) return
    let alive = true
    const want = absentKey.split(',')
    // A proposal Archie just made isn't in the shared list yet: nudge the
    // shared hook once so Home/Activity pick it up too.
    if (decisions && !reloadRequested.current) {
      reloadRequested.current = true
      void decisions.proposals.reload()
    }
    getIntentsByIds(want)
      .then((rows) => {
        if (!alive) return
        setFetched((prev) => {
          const next = new Map(prev)
          for (const r of rows) next.set(r.id, r)
          return next
        })
        const got = new Set(rows.map((r) => r.id))
        setNotFound(new Set(want.filter((id) => !got.has(id))))
      })
      .catch(() => {
        /* keep whatever we have; the card shows a neutral fallback */
      })
    return () => {
      alive = false
    }
    // Re-check out-of-list ids whenever the shared list refreshes (after an action).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [absentKey, listed])

  return useMemo(() => {
    const byId = new Map<string, TradeIntent>()
    for (const id of key ? key.split(',') : []) {
      const hit = listedMap.get(id) ?? fetched.get(id)
      if (hit) byId.set(id, hit)
    }
    return { byId, missing: notFound }
  }, [key, listedMap, fetched, notFound])
}

function accountName(kind?: string | null) {
  return kind === 'stocks_isa' ? 'ISA' : kind === 'invest' ? 'Invest' : null
}

const RECEIPT: Record<string, { label: (i: TradeIntent) => string; icon: typeof Check; tone: string }> = {
  executed: {
    label: (i) => (i.side === 'buy' ? 'Bought' : 'Sold'),
    icon: CheckCheck,
    tone: 'text-positive',
  },
  rejected: { label: () => 'Rejected', icon: X, tone: 'text-muted-foreground' },
  expired: { label: () => 'Expired', icon: Hourglass, tone: 'text-muted-foreground' },
  superseded: { label: () => 'Superseded', icon: Clock, tone: 'text-muted-foreground' },
  failed: { label: () => 'Failed', icon: Ban, tone: 'text-negative' },
}

/** One-line receipt for a resolved proposal. */
function Receipt({ intent }: { intent: TradeIntent }) {
  const { navigate, openTicker } = useAppActions()
  const blur = privacyBlurClass(usePrivacyMode())
  const meta = RECEIPT[intent.status] ?? { label: () => intent.status, icon: Clock, tone: 'text-muted-foreground' }
  const Icon = meta.icon
  const executed = intent.status === 'executed'
  return (
    <div className="rc-receipt">
      <Icon className={cn('size-3.5 shrink-0', meta.tone)} />
      <span className={cn('font-medium', executed ? 'text-foreground' : 'text-muted-foreground')}>
        {executed ? meta.label(intent) : `${meta.label(intent)} ·`}
      </span>
      {!executed ? <span className="uppercase text-[11px] tracking-wide text-muted-foreground">{intent.side}</span> : null}
      <button type="button" className="rc-ticker" onClick={() => openTicker(intent.symbol)}>
        {intent.symbol}
      </button>
      <span className={cn('rc-fig', blur)}>{formatMoney(intent.estimated_notional, 'GBP', 0)}</span>
      {accountName(intent.account_kind) ? <span className="text-muted-foreground">· {accountName(intent.account_kind)}</span> : null}
      {executed && intent.execution_price ? (
        <span className="hidden text-muted-foreground sm:inline">
          · filled at <span className={cn('rc-fig', blur)}>{intent.execution_price.toLocaleString(undefined, { maximumFractionDigits: 4 })}</span>
        </span>
      ) : null}
      {intent.status === 'failed' && intent.failure_reason ? (
        <span className="min-w-0 truncate text-muted-foreground" title={intent.failure_reason}>
          · {intent.failure_reason}
        </span>
      ) : null}
      <span className="ml-auto hidden shrink-0 text-[11px] text-muted-foreground sm:inline">
        {ago(intent.executed_at || intent.approved_at || intent.created_at)} ago
      </span>
      <button type="button" className="ml-auto shrink-0 text-[11px] text-muted-foreground hover:text-foreground sm:ml-0" onClick={() => navigate('/activity/proposals')}>
        Details
      </button>
    </div>
  )
}

function Unavailable({ id, loading }: { id: string; loading: boolean }) {
  const { navigate } = useAppActions()
  return (
    <div className="rc-receipt">
      {loading ? <Loader2 className="size-3.5 animate-spin text-muted-foreground" /> : <FileQuestion className="size-3.5 text-muted-foreground" />}
      <span className="text-muted-foreground">{loading ? 'Loading proposal…' : 'Proposal no longer available'}</span>
      <span className="font-mono text-[11px] text-muted-foreground/70">{id.slice(0, 8)}</span>
      <button type="button" className="ml-auto text-[11px] text-muted-foreground hover:text-foreground" onClick={() => navigate('/activity/proposals')}>
        Decisions →
      </button>
    </div>
  )
}

/** The inline card for one or more proposals from a single reply. */
export function ProposalGroup({ ids }: { ids: string[] }) {
  const decisions = useDecisions()
  const { askArchie, openTicker, navigate } = useAppActions()
  const { byId, missing } = useIntents(ids)
  const [confirmAll, setConfirmAll] = useState(false)
  const [bulkWorking, setBulkWorking] = useState(false)

  const intents = ids.map((id) => byId.get(id))
  const pending = intents.filter((i): i is TradeIntent => Boolean(i && PENDING.has(i.status)))
  const resolved = intents.filter((i): i is TradeIntent => Boolean(i && !PENDING.has(i.status)))
  const unknown = ids.filter((id) => !byId.has(id))

  const rails = new Map(
    pending.map((i) => [i.id, decisions ? railCheck(i, decisions.config, decisions.positions, decisions.total) : { ok: true, label: '' }]),
  )
  const approvable = pending.filter(
    (i) => i.status !== 'executing' && rails.get(i.id)?.ok && (i.account_kind === 'invest' || i.account_kind === 'stocks_isa'),
  )
  const live = decisions?.brokerMode === 'live'

  async function approveAll() {
    if (!decisions) return
    setBulkWorking(true)
    try {
      for (const i of approvable) await decisions.proposals.approveAndExecute(i, i.account_kind as ExecAccount)
    } finally {
      setBulkWorking(false)
    }
  }

  const prominent = pending.length > 0
  const total = approvable.reduce((a, i) => a + i.estimated_notional, 0)

  return (
    <div className={cn('rc-proposals', prominent ? 'is-pending' : 'is-resolved')}>
      {prominent ? (
        <div className="rc-proposals-head">
          <ShieldCheck className="size-4 text-primary" />
          <span className="font-medium text-foreground">
            {pending.length === 1 ? 'Archie proposes a trade' : `Archie proposes ${pending.length} trades`}
          </span>
          <span className="hidden text-muted-foreground sm:inline">· nothing happens until you approve</span>
          {decisions && approvable.length > 1 ? (
            <Button
              size="sm"
              variant="outline"
              className="ml-auto h-7 gap-1.5 px-2.5 text-xs"
              disabled={bulkWorking || decisions.proposals.working !== null}
              onClick={() => (live ? setConfirmAll(true) : void approveAll())}
            >
              {bulkWorking ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
              Approve all within limits ({approvable.length})
            </Button>
          ) : null}
        </div>
      ) : null}

      {pending.length > 0 ? (
        <div className="rc-proposals-rows">
          {pending.map((intent) =>
            decisions ? (
              <ProposalRow
                key={intent.id}
                intent={intent}
                rail={rails.get(intent.id)!}
                brokerMode={decisions.brokerMode}
                working={decisions.proposals.working === intent.id || bulkWorking}
                onPlace={(acct) => void decisions.proposals.approveAndExecute(intent, acct)}
                onReject={() => void decisions.proposals.reject(intent.id)}
                onAsk={() => askArchie(proposalPrompt(intent))}
                onViewTicker={openTicker}
                askLabel="Ask why"
              />
            ) : (
              <div key={intent.id} className="flex items-center gap-2 py-2 text-sm">
                <span className="text-xs font-semibold uppercase">{intent.side}</span>
                <span className="font-medium">{intent.symbol}</span>
                <span className="rc-fig">{formatMoney(intent.estimated_notional, 'GBP', 0)}</span>
                <button type="button" className="ml-auto text-xs text-primary hover:underline" onClick={() => navigate('/activity/proposals')}>
                  Review in Decisions →
                </button>
              </div>
            ),
          )}
        </div>
      ) : null}

      {resolved.length > 0 || unknown.length > 0 ? (
        <div className={cn('rc-receipts', prominent && 'has-divider')}>
          {resolved.map((i) => (
            <Receipt key={i.id} intent={i} />
          ))}
          {unknown.map((id) => (
            <Unavailable key={id} id={id} loading={!missing.has(id)} />
          ))}
        </div>
      ) : null}

      <AlertDialog open={confirmAll} onOpenChange={setConfirmAll}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Place {approvable.length} orders ({formatMoney(total, 'GBP', 0)})?
            </AlertDialogTitle>
            <AlertDialogDescription>
              These send real orders to Trading 212:{' '}
              {approvable.map((i) => `${i.side.toUpperCase()} ${i.symbol} ${formatMoney(i.estimated_notional, 'GBP', 0)} (${accountName(i.account_kind)})`).join(', ')}.
              Proposals outside your limits are left for you to review.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmAll(false)
                void approveAll()
              }}
            >
              Place orders
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

/** A proposal-only paragraph: the group leader renders the whole group. */
export function ProposalBlock({ id }: { id: string }) {
  const group = useContext(ProposalGroupContext)
  if (group && group.length && group.includes(id)) {
    return group[0] === id ? <ProposalGroup ids={group} /> : null
  }
  return <ProposalGroup ids={[id]} />
}

/** An inline mention of a proposal inside a sentence. */
export function ProposalChip({ id, label }: { id: string; label: string }) {
  const { navigate } = useAppActions()
  return (
    <button type="button" className="rc-proposal-chip" onClick={() => navigate('/activity/proposals')} title={`Proposal ${id}`}>
      <ShieldCheck className="size-3" />
      {label}
    </button>
  )
}

/** Proposal ids referenced as their own line (card blocks), in order, de-duplicated. */
export function proposalIdsIn(md: string): string[] {
  const out: string[] = []
  for (const m of md.matchAll(/^\s*\[[^\]]+\]\((?:proposal:|#?\/activity\/proposals\?id=)([\w-]+)\)\s*$/gm)) {
    if (!out.includes(m[1])) out.push(m[1])
  }
  return out
}
