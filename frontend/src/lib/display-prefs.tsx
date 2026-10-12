import { createContext, useContext, type ReactNode } from 'react'

/**
 * The shell's current display selection (Home's account + currency switches),
 * for features that render money — e.g. agent-built views.
 */
export type DisplayPrefs = {
  currency: 'GBP' | 'USD'
  accountView: 'all' | 'invest' | 'stocks_isa'
}

const DisplayPrefsContext = createContext<DisplayPrefs>({ currency: 'GBP', accountView: 'all' })

export function DisplayPrefsProvider({ value, children }: { value: DisplayPrefs; children: ReactNode }) {
  return <DisplayPrefsContext.Provider value={value}>{children}</DisplayPrefsContext.Provider>
}

export function useDisplayPrefs(): DisplayPrefs {
  return useContext(DisplayPrefsContext)
}
