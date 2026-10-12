import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import { approveIntent, executeIntent, getEvents, getIntents, rejectIntent } from '@/api/client'
import { toastApiError } from '@/lib/api-error'
import type { ExecutionEvent, TradeIntent } from '@/types'

export type ExecAccount = 'invest' | 'stocks_isa'

export const PENDING_STATUSES = ['proposed', 'approved', 'executing'] as const

function accountName(kind?: string | null) {
  return kind === 'stocks_isa' ? 'Stocks ISA' : kind === 'invest' ? 'Invest' : undefined
}

/**
 * Trade proposals (intents) + the execution audit trail, with the human
 * approval actions. Every action toasts its outcome and re-pulls the queue;
 * `onExecuted` lets the shell refresh the portfolio after an order goes out.
 */
export function useProposals({ paper, onExecuted }: { paper: boolean; onExecuted?: () => void }) {
  const [intents, setIntents] = useState<TradeIntent[]>([])
  const [events, setEvents] = useState<ExecutionEvent[]>([])
  const [loaded, setLoaded] = useState(false)
  const [working, setWorking] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      const [rows, evts] = await Promise.all([getIntents(), getEvents()])
      setIntents(rows)
      setEvents(evts)
    } catch {
      /* keep the last good queue */
    } finally {
      setLoaded(true)
    }
  }, [])

  // Archie (chat, plays, scheduled runs) creates proposals out-of-band, so keep the
  // queue fresh: re-pull on navigation, on tab focus, and every 20s while visible.
  useEffect(() => {
    void reload()
    const tick = () => {
      if (document.visibilityState === 'visible') void reload()
    }
    const id = window.setInterval(tick, 20_000)
    window.addEventListener('hashchange', tick)
    document.addEventListener('visibilitychange', tick)
    return () => {
      window.clearInterval(id)
      window.removeEventListener('hashchange', tick)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [reload])

  const pending = useMemo(
    () => intents.filter((i) => (PENDING_STATUSES as readonly string[]).includes(i.status)),
    [intents],
  )
  const history = useMemo(() => intents.filter((i) => !pending.includes(i)), [intents, pending])

  const approve = useCallback(
    async (id: string) => {
      setWorking(id)
      try {
        await approveIntent(id)
        toast.success('Proposal approved')
      } catch (err) {
        toastApiError(err, 'Approve failed')
      } finally {
        setWorking(null)
        await reload()
      }
    },
    [reload],
  )

  const reject = useCallback(
    async (id: string) => {
      setWorking(id)
      try {
        await rejectIntent(id)
        toast('Proposal rejected')
      } catch (err) {
        toastApiError(err, 'Reject failed')
      } finally {
        setWorking(null)
        await reload()
      }
    },
    [reload],
  )

  const execute = useCallback(
    async (id: string, account?: ExecAccount) => {
      setWorking(id)
      try {
        await executeIntent(id, false, account)
        toast.success(paper ? 'Paper fill recorded' : 'Order sent to broker', {
          description: accountName(account) ? `Account: ${accountName(account)}` : undefined,
        })
        onExecuted?.()
      } catch (err) {
        toastApiError(err, 'Execution failed')
      } finally {
        setWorking(null)
        await reload()
      }
    },
    [paper, onExecuted, reload],
  )

  /** The one-tap path from Home: execute with `approve: true` — one audited call. */
  const approveAndExecute = useCallback(
    async (intent: TradeIntent, account: ExecAccount) => {
      setWorking(intent.id)
      try {
        await executeIntent(intent.id, false, account)
        toast.success(paper ? 'Paper fill recorded' : 'Order sent to broker', {
          description: `${intent.side.toUpperCase()} ${intent.symbol} · ${accountName(account)}`,
        })
        onExecuted?.()
      } catch (err) {
        toastApiError(err, 'Could not place the order')
      } finally {
        setWorking(null)
        await reload()
      }
    },
    [paper, onExecuted, reload],
  )

  return { intents, events, pending, history, loaded, working, reload, approve, reject, execute, approveAndExecute }
}

export type ProposalsState = ReturnType<typeof useProposals>
