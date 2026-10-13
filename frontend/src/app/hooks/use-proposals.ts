import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import { approveIntent, executeIntent, getEvents, getIntents, rejectIntent, stageIntentTomorrow } from '@/api/client'
import { toastApiError } from '@/lib/api-error'
import type { AppConfig, ExecutionEvent, PositionItem, TradeIntent } from '@/types'
import { accountLabel } from '@/utils/format'
import type { BrokerMode } from './use-app-config'

export type ExecAccount = 'invest' | 'stocks_isa' | 'crypto'

export const PENDING_STATUSES = ['proposed', 'approved', 'executing'] as const

function accountName(kind?: string | null) {
  return kind === 'stocks_isa' ? 'Stocks ISA' : kind === 'invest' ? 'Invest' : kind === 'crypto' ? accountLabel('crypto') : undefined
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
    window.addEventListener('openpf:portfolio-changed', tick)
    document.addEventListener('visibilitychange', tick)
    return () => {
      window.clearInterval(id)
      window.removeEventListener('hashchange', tick)
      window.removeEventListener('openpf:portfolio-changed', tick)
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
    async (intent: TradeIntent, account: ExecAccount): Promise<boolean> => {
      setWorking(intent.id)
      try {
        await executeIntent(intent.id, false, account)
        toast.success(paper ? 'Paper fill recorded' : 'Order sent to broker', {
          description: `${intent.side.toUpperCase()} ${intent.symbol} · ${accountName(account)}`,
        })
        onExecuted?.()
        return true
      } catch (err) {
        toastApiError(err, 'Could not place the order')
        return false
      } finally {
        setWorking(null)
        await reload()
      }
    },
    [paper, onExecuted, reload],
  )

  /** A held-back ask → a proposal for tomorrow's limit window (same id). */
  const stageTomorrow = useCallback(
    async (intent: TradeIntent): Promise<boolean> => {
      setWorking(intent.id)
      try {
        await stageIntentTomorrow(intent.id)
        toast.success(`${intent.symbol} proposed for tomorrow`, {
          description: 'It counts against tomorrow\'s daily limit and can be approved from tomorrow.',
        })
        return true
      } catch (err) {
        toastApiError(err, 'Could not stage it for tomorrow')
        return false
      } finally {
        setWorking(null)
        await reload()
      }
    },
    [reload],
  )

  /** Drop a held-back ask (nothing was proposed, so nothing to reject). */
  const dropHeld = useCallback(
    async (id: string) => {
      setWorking(id)
      try {
        await rejectIntent(id, 'dropped held-back ask')
      } catch (err) {
        toastApiError(err, 'Could not drop it')
      } finally {
        setWorking(null)
        await reload()
      }
    },
    [reload],
  )

  return { intents, events, pending, history, loaded, working, reload, approve, reject, execute, approveAndExecute, stageTomorrow, dropHeld }
}

export type ProposalsState = ReturnType<typeof useProposals>

/**
 * The shell's shared proposals state plus what a proposal card needs to run
 * the same rail check as Decisions. Provided to surfaces outside Home/Activity
 * (Archie's inline proposal cards) so approving anywhere updates everywhere.
 */
export type DecisionsContextValue = {
  proposals: ProposalsState
  config: AppConfig | null
  brokerMode: BrokerMode | null
  positions: PositionItem[]
  total: number
}

export const DecisionsContext = createContext<DecisionsContextValue | null>(null)

export function useDecisions(): DecisionsContextValue | null {
  return useContext(DecisionsContext)
}
