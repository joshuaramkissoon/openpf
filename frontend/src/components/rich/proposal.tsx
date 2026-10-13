import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { FileQuestion, Loader2, ShieldCheck } from 'lucide-react'

import { getIntentsByIds } from '@/api/client'
import { useDecisions } from '@/app/hooks/use-proposals'
import { ProposalCarousel, ProposalPair, useApproveAll } from '@/features/home/proposal-carousel'
import { ProposalReceipt, isSingleTranche } from '@/features/home/proposal-tiles'
import { proposalPrompt } from '@/features/home/decisions-card'
import { useAppActions } from '@/lib/app-actions'
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

  const intents = ids.map((id) => byId.get(id))
  const pending = intents.filter((i): i is TradeIntent => Boolean(i && PENDING.has(i.status)))
  // The part of an ask a limit stopped: muted cards beside the proposals.
  const held = intents.filter((i): i is TradeIntent => i?.status === 'held')
  const resolved = intents.filter((i): i is TradeIntent => Boolean(i && !PENDING.has(i.status) && i.status !== 'held'))
  const unknown = ids.filter((id) => !byId.has(id))
  const approveAll = useApproveAll(pending, decisions)

  const prominent = pending.length > 0 || held.length > 0
  const cards = pending.length + held.length
  const shared = decisions
    ? { ctx: decisions, onAsk: (i: TradeIntent) => askArchie(proposalPrompt(i)), onViewTicker: openTicker, askLabel: 'Ask why' }
    : null

  return (
    <div className={cn('rc-proposals', prominent ? 'is-pending' : 'is-resolved')}>
      {prominent ? (
        <div className="rc-proposals-head">
          <ShieldCheck className="size-4 text-primary" />
          <span className="font-medium text-foreground">
            {pending.length === 0
              ? 'Held back by your limits'
              : pending.length === 1
                ? 'Archie proposes a trade'
                : `Archie proposes ${pending.length} trades`}
          </span>
          {pending.length > 0 && held.length > 0 ? (
            <span className="text-muted-foreground">· {held.length} held back</span>
          ) : (
            <span className="hidden text-muted-foreground sm:inline">
              {pending.length > 0 ? '· nothing happens until you approve' : '· nothing was proposed'}
            </span>
          )}
          {!isSingleTranche(pending) && approveAll.button ? <span className="ml-auto">{approveAll.button}</span> : null}
        </div>
      ) : null}

      {cards > 0 ? (
        <div className="rc-proposals-rows">
          {shared ? (
            cards > 2 ? (
              <ProposalCarousel intents={pending} held={held} {...shared} />
            ) : (
              <ProposalPair intents={pending} held={held} {...shared} />
            )
          ) : (
            [...pending, ...held].map((intent) => (
              <div key={intent.id} className="flex items-center gap-2 py-2 text-sm">
                <span className="text-xs font-semibold uppercase">{intent.status === 'held' ? 'held' : intent.side}</span>
                <span className="font-medium">{intent.symbol}</span>
                <span className="rc-fig">{formatMoney(intent.estimated_notional, 'GBP', 0)}</span>
                <button type="button" className="ml-auto text-xs text-primary hover:underline" onClick={() => navigate('/activity/proposals')}>
                  Review in Decisions →
                </button>
              </div>
            ))
          )}
        </div>
      ) : null}

      {resolved.length > 0 || unknown.length > 0 ? (
        <div className={cn('rc-receipts', prominent && 'has-divider')}>
          {resolved.map((i) => (
            <ProposalReceipt
              key={i.id}
              intent={i}
              onViewTicker={openTicker}
              onDetails={() => navigate('/activity/proposals')}
              className="px-3.5"
            />
          ))}
          {unknown.map((id) => (
            <Unavailable key={id} id={id} loading={!missing.has(id)} />
          ))}
        </div>
      ) : null}

      {approveAll.dialog}
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
