import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react"

import type { PositionItem } from "@/types"
import { InstrumentSpotlight, type RecentInstrument } from "./instrument-spotlight"
import { InstrumentDetailSheet, type InstrumentHint } from "./instrument-detail-sheet"

type InstrumentContextValue = {
  /** Open the unified detail sheet for a ticker (optionally with an instant hint). */
  openInstrument: (ticker: string, hint?: InstrumentHint) => void
  /** Open the Cmd+K command palette. */
  openPalette: () => void
}

const InstrumentContext = createContext<InstrumentContextValue | null>(null)

export function useInstrument() {
  const ctx = useContext(InstrumentContext)
  if (!ctx) throw new Error("useInstrument must be used within <InstrumentProvider>")
  return ctx
}

const RECENT_KEY = "openpf:recent-instruments"
const RECENT_LIMIT = 8

/** Fired when the watchlist is mutated from the Spotlight sheet, so independently
 *  data-fetching surfaces (e.g. the Watchlist board) can refresh. */
export const WATCHLIST_CHANGED_EVENT = "mypf:watchlist-changed"

function loadRecent(): RecentInstrument[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((r) => r && typeof r.ticker === "string") : []
  } catch {
    return []
  }
}

export function InstrumentProvider({
  positions,
  currency,
  onAskArchie,
  children,
}: {
  positions: PositionItem[]
  currency: "GBP" | "USD"
  onAskArchie?: (ticker: string, name?: string | null) => void
  children: React.ReactNode
}) {
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [ticker, setTicker] = useState<string | null>(null)
  const [hint, setHint] = useState<InstrumentHint | null>(null)
  const [recent, setRecent] = useState<RecentInstrument[]>(() => loadRecent())

  const pushRecent = useCallback((t: string, name?: string | null) => {
    setRecent((prev) => {
      const upper = t.toUpperCase()
      const keptName = name ?? prev.find((r) => r.ticker === upper)?.name ?? null
      const next = [{ ticker: upper, name: keptName }, ...prev.filter((r) => r.ticker !== upper)].slice(0, RECENT_LIMIT)
      try {
        localStorage.setItem(RECENT_KEY, JSON.stringify(next))
      } catch {
        /* localStorage may be unavailable — recents are best-effort */
      }
      return next
    })
  }, [])

  const openInstrument = useCallback(
    (t: string, h?: InstrumentHint) => {
      setTicker(t)
      setHint(h ?? null)
      setDetailOpen(true)
      setPaletteOpen(false)
      pushRecent(t, h?.name)
    },
    [pushRecent]
  )

  const openPalette = useCallback(() => setPaletteOpen(true), [])

  // Cmd/Ctrl+K toggles the palette from anywhere in the app.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault()
        setPaletteOpen((v) => !v)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  const value = useMemo(() => ({ openInstrument, openPalette }), [openInstrument, openPalette])

  return (
    <InstrumentContext.Provider value={value}>
      {children}
      <InstrumentSpotlight
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        positions={positions}
        recent={recent}
        onSelect={openInstrument}
      />
      <InstrumentDetailSheet
        ticker={ticker}
        currency={currency}
        open={detailOpen}
        onOpenChange={setDetailOpen}
        hint={hint}
        onAskArchie={onAskArchie}
        onWatchlistChanged={() => window.dispatchEvent(new Event(WATCHLIST_CHANGED_EVENT))}
      />
    </InstrumentContext.Provider>
  )
}
