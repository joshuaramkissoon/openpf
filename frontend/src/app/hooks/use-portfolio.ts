import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { getSnapshot, refreshPortfolio } from '@/api/client'
import type { PrivacyMode } from '@/lib/privacy'
import type { PortfolioSnapshot } from '@/types'

import {
  aggregatePositionsByTicker,
  obfuscateSnapshot,
  parseApiError,
  type AccountView,
  type DisplayCurrency,
} from '../portfolio-utils'

const ACCOUNT_KEY = 'openpf:account-view'
const CURRENCY_KEY = 'openpf:display-currency'

function loadPref<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key) as T | null
    return v && allowed.includes(v) ? v : fallback
  } catch {
    return fallback
  }
}

function savePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* best-effort */
  }
}

/**
 * The portfolio snapshot for the selected account + display currency.
 *
 * Loads on mount with a non-blocking live refresh, then polls every 60s while
 * `pollActive` and the tab is visible (and immediately on tab focus). In
 * `scramble` privacy mode the returned snapshot/positions are obfuscated.
 */
export function usePortfolio({ privacyMode, pollActive }: { privacyMode: PrivacyMode; pollActive: boolean }) {
  const [accountView, setAccountViewState] = useState<AccountView>(() =>
    loadPref(ACCOUNT_KEY, ['all', 'invest', 'stocks_isa'] as const, 'all'),
  )
  const [currency, setCurrencyState] = useState<DisplayCurrency>(() =>
    loadPref(CURRENCY_KEY, ['GBP', 'USD'] as const, 'GBP'),
  )
  const [snapshot, setSnapshot] = useState<PortfolioSnapshot | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdate, setLastUpdate] = useState<string | null>(null)

  // Guards for the background (fire-and-forget) re-pull: don't setState after
  // unmount, and don't let a stale refresh clobber a newer selection.
  const mountedRef = useRef(true)
  const loadIdRef = useRef(0)
  useEffect(() => () => { mountedRef.current = false }, [])

  const load = useCallback(
    async (opts: { live?: boolean; force?: boolean; account?: AccountView; currency?: DisplayCurrency } = {}) => {
      const acct = opts.account ?? accountView
      const ccy = opts.currency ?? currency
      const myLoadId = ++loadIdRef.current
      setBusy(true)
      setError(null)
      if (opts.live) {
        // Kick the live refresh off WITHOUT blocking the render: the snapshot
        // endpoint serves the last stored snapshot, and when the refresh lands
        // we re-pull to show fresh numbers.
        void refreshPortfolio(Boolean(opts.force))
          .then(async () => {
            const fresh = await getSnapshot(acct, ccy)
            if (!mountedRef.current || loadIdRef.current !== myLoadId) return
            setSnapshot(fresh)
            setLastUpdate(new Date().toISOString())
          })
          .catch(() => {
            /* non-fatal — the poll / next load retries */
          })
      }
      try {
        const snap = await getSnapshot(acct, ccy)
        if (!mountedRef.current || loadIdRef.current !== myLoadId) return
        setSnapshot(snap)
        setLastUpdate(new Date().toISOString())
      } catch (err) {
        setError(parseApiError(err))
      } finally {
        if (mountedRef.current) setBusy(false)
      }
    },
    [accountView, currency],
  )

  useEffect(() => {
    void load({ live: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const refreshSilently = useCallback(async () => {
    try {
      await refreshPortfolio()
      const snap = await getSnapshot(accountView, currency)
      if (!mountedRef.current) return
      setSnapshot(snap)
      setLastUpdate(new Date().toISOString())
    } catch {
      /* silent — poll failures are non-fatal */
    }
  }, [accountView, currency])

  // A trade placed by hand (the stock sheet) changes holdings now, not in a minute.
  useEffect(() => {
    const onTraded = () => void refreshSilently()
    window.addEventListener('openpf:portfolio-changed', onTraded)
    return () => window.removeEventListener('openpf:portfolio-changed', onTraded)
  }, [refreshSilently])

  useEffect(() => {
    if (!pollActive) return
    function maybeRefresh() {
      if (document.visibilityState === 'visible') void refreshSilently()
    }
    const id = window.setInterval(maybeRefresh, 60_000)
    document.addEventListener('visibilitychange', maybeRefresh)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', maybeRefresh)
    }
  }, [pollActive, refreshSilently])

  const setAccountView = useCallback(
    (next: AccountView) => {
      setAccountViewState(next)
      savePref(ACCOUNT_KEY, next)
      void load({ account: next })
    },
    [load],
  )

  const setCurrency = useCallback(
    (next: DisplayCurrency) => {
      setCurrencyState(next)
      savePref(CURRENCY_KEY, next)
      void load({ currency: next })
    },
    [load],
  )

  // Scramble swaps in non-real numbers upstream; blur/off render the real
  // snapshot (blur is applied visually by the figure components downstream).
  const displaySnapshot = useMemo(() => {
    if (!snapshot) return null
    return privacyMode === 'scramble' ? obfuscateSnapshot(snapshot) : snapshot
  }, [snapshot, privacyMode])

  const positions = useMemo(() => {
    if (!displaySnapshot) return []
    if (accountView === 'all') return aggregatePositionsByTicker(displaySnapshot.positions, displaySnapshot.account.total)
    return displaySnapshot.positions.slice().sort((a, b) => b.value - a.value)
  }, [displaySnapshot, accountView])

  return {
    snapshot: displaySnapshot,
    positions,
    accountView,
    setAccountView,
    currency,
    setCurrency,
    busy,
    error,
    lastUpdate,
    reload: load,
  }
}

export type PortfolioState = ReturnType<typeof usePortfolio>
