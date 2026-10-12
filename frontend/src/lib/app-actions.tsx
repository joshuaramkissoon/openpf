import { createContext, useContext, type ReactNode } from 'react'

import { navigate } from '@/lib/router'

/**
 * Cross-feature actions the app shell provides to every page (and to
 * agent-built views via the views bridge). Features call these instead of
 * reaching into the shell's state.
 */
export type AppActions = {
  /** Open Archie with the composer pre-filled (sent if `send` is true). */
  askArchie: (prompt: string, opts?: { send?: boolean }) => void
  /** Open the instrument detail sheet for a ticker (e.g. `NVDA`). */
  openTicker: (ticker: string) => void
  /** Navigate to an app route, e.g. `/plays/abc`. */
  navigate: (path: string) => void
  /** Ask the shell to re-pull portfolio + proposals (after an action). */
  refresh: () => void
}

const noop = () => {}

const AppActionsContext = createContext<AppActions>({
  askArchie: noop,
  openTicker: noop,
  navigate,
  refresh: noop,
})

export function AppActionsProvider({ value, children }: { value: AppActions; children: ReactNode }) {
  return <AppActionsContext.Provider value={value}>{children}</AppActionsContext.Provider>
}

export function useAppActions(): AppActions {
  return useContext(AppActionsContext)
}
