import { useEffect, useState } from 'react'

import { getCryptoStatus, type CryptoStatus } from '@/api/crypto'
import { setCryptoAccountLabel } from '@/utils/format'

/**
 * The crypto exchange connection, fetched once and shared. Also feeds the
 * account chip label ("Kraken") used wherever holdings show their account.
 */
let cached: CryptoStatus | null = null
let inflight: Promise<CryptoStatus | null> | null = null
const listeners = new Set<(s: CryptoStatus | null) => void>()

function publish(s: CryptoStatus | null) {
  cached = s
  setCryptoAccountLabel(s?.label ? s.label.replace(/ \(sandbox\)$/, '') : '')
  listeners.forEach((l) => l(s))
}

export function reloadCryptoStatus(): Promise<CryptoStatus | null> {
  inflight = getCryptoStatus()
    .then((s) => {
      publish(s)
      return s
    })
    .catch(() => cached)
    .finally(() => {
      inflight = null
    })
  return inflight
}

export function setCryptoStatus(s: CryptoStatus | null) {
  publish(s)
}

export function useCryptoStatus(): CryptoStatus | null {
  const [status, setStatus] = useState<CryptoStatus | null>(cached)
  useEffect(() => {
    listeners.add(setStatus)
    if (!cached && !inflight) void reloadCryptoStatus()
    return () => {
      listeners.delete(setStatus)
    }
  }, [])
  return status
}
