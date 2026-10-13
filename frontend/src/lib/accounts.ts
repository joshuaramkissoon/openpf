import { useEffect, useState } from 'react'

import { getAccountDirectory, type AccountInfo } from '@/api/connections'

/**
 * Every connected account's label and capability (Trading 212, crypto
 * exchanges, IBKR, SnapTrade, …), fetched once and shared. `accountLabel` /
 * `accountTag` in utils/format read it, so holdings anywhere show the right
 * source chip, and read-only accounts can be marked.
 */
let directory: Record<string, AccountInfo> = {}
let loaded = false
let inflight: Promise<void> | null = null
const listeners = new Set<() => void>()

export function accountInfo(kind: string | null | undefined): AccountInfo | undefined {
  return kind ? directory[kind] : undefined
}

export function isCryptoAccount(kind: string | null | undefined): boolean {
  return kind === 'crypto' || (kind ?? '').startsWith('crypto:')
}

export function reloadAccounts(): Promise<void> {
  inflight = getAccountDirectory()
    .then((d) => {
      directory = d ?? {}
      loaded = true
      listeners.forEach((l) => l())
    })
    .catch(() => {})
    .finally(() => {
      inflight = null
    })
  return inflight
}

/** Re-renders the caller once the directory loads (or reloads). */
export function useAccounts(): Record<string, AccountInfo> {
  const [, setTick] = useState(0)
  useEffect(() => {
    const l = () => setTick((t) => t + 1)
    listeners.add(l)
    if (!loaded && !inflight) void reloadAccounts()
    return () => {
      listeners.delete(l)
    }
  }, [])
  return directory
}
