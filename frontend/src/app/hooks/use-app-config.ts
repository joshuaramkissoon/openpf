import { useCallback, useEffect, useState } from 'react'

import { getConfig } from '@/api/client'
import type { AppConfig } from '@/types'

/** App config (broker mode, risk rails, labs flags). Reload after settings saves. */
export function useAppConfig() {
  const [config, setConfig] = useState<AppConfig | null>(null)

  const reload = useCallback(async () => {
    try {
      setConfig(await getConfig())
    } catch {
      /* the shell renders without config; settings surface their own errors */
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  return { config, reload }
}

export type BrokerMode = 'sandbox' | 'paper' | 'demo' | 'live'

/** What kind of money an order would touch — drives the persistent badge. */
export function brokerModeOf(config: AppConfig | null): BrokerMode | null {
  if (!config) return null
  const env = String(config.broker.t212_base_env)
  if (env === 'sandbox') return 'sandbox'
  if (config.broker.broker_mode === 'paper') return 'paper'
  if (env === 'demo') return 'demo'
  return 'live'
}
