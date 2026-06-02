import { useEffect, useMemo, useRef, useState } from "react"
import { Clock, Search, Star, TrendingUp } from "lucide-react"

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command"
import { Badge } from "@/components/ui/badge"
import { PctDelta } from "@/components/kit"
import { accountTag } from "@/utils/format"
import { cn } from "@/lib/utils"
import { getWatchlist } from "@/api/client"
import { searchInstruments, type InstrumentSearchRow } from "@/api/instruments"
import type { PositionItem, WatchlistItem } from "@/types"
import type { InstrumentHint } from "./instrument-detail-sheet"

export interface RecentInstrument {
  ticker: string
  name?: string | null
}

const CONVICTION_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 }

function matches(query: string, ...fields: (string | null | undefined)[]): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return fields.some((f) => (f || "").toLowerCase().includes(q))
}

export function InstrumentSpotlight({
  open,
  onOpenChange,
  positions,
  recent,
  onSelect,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  positions: PositionItem[]
  recent: RecentInstrument[]
  onSelect: (ticker: string, hint?: InstrumentHint) => void
}) {
  const [query, setQuery] = useState("")
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([])
  const [results, setResults] = useState<InstrumentSearchRow[]>([])
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Reset the query each time the palette opens, and pull the watchlist once.
  useEffect(() => {
    if (!open) return
    setQuery("")
    setResults([])
    let cancelled = false
    void getWatchlist("watching")
      .then((rows) => {
        if (!cancelled) setWatchlist(rows)
      })
      .catch(() => {
        /* watchlist enrichment is optional */
      })
    return () => {
      cancelled = true
    }
  }, [open])

  // Debounced server resolution for arbitrary tickers (≥2 chars).
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    const q = query.trim()
    if (q.length < 2) {
      setResults([])
      return
    }
    debounceRef.current = setTimeout(() => {
      void searchInstruments(q, 8)
        .then(setResults)
        .catch(() => setResults([]))
    }, 220)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [query])

  const heldSymbols = useMemo(() => new Set(positions.map((p) => p.ticker.toUpperCase())), [positions])
  const watchSymbols = useMemo(() => new Set(watchlist.map((w) => w.symbol.toUpperCase())), [watchlist])

  const holdings = useMemo(
    () =>
      positions
        .filter((p) => matches(query, p.ticker, p.name))
        .slice()
        .sort((a, b) => b.weight - a.weight),
    [positions, query]
  )

  const watched = useMemo(
    () =>
      watchlist
        .filter((w) => matches(query, w.symbol, w.name))
        .slice()
        .sort((a, b) => {
          if ((b.open_flags || 0) !== (a.open_flags || 0)) return (b.open_flags || 0) - (a.open_flags || 0)
          const ca = CONVICTION_RANK[a.conviction || ""] ?? 3
          const cb = CONVICTION_RANK[b.conviction || ""] ?? 3
          if (ca !== cb) return ca - cb
          return a.symbol.localeCompare(b.symbol)
        }),
    [watchlist, query]
  )

  // Server results, minus anything already shown in holdings/watchlist.
  const extraResults = useMemo(
    () =>
      results.filter(
        (r) => !heldSymbols.has(r.ticker.toUpperCase()) && !watchSymbols.has(r.ticker.toUpperCase())
      ),
    [results, heldSymbols, watchSymbols]
  )

  // Recents surface only on an empty query — they'd be noise once you start typing.
  const recentRows = useMemo(() => (query.trim() ? [] : recent.slice(0, 6)), [recent, query])

  const isEmpty =
    holdings.length === 0 && watched.length === 0 && extraResults.length === 0 && recentRows.length === 0

  const resetKey = `${query}:${holdings.length}:${watched.length}:${extraResults.length}:${recentRows.length}`

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <Command value={query} resetKey={resetKey}>
        <CommandInput
          placeholder="Jump to an instrument — ticker or name…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <CommandList>
          {isEmpty ? (
            <CommandEmpty>
              {query.trim() ? `No instruments match “${query.trim()}”.` : "Start typing a ticker or name."}
            </CommandEmpty>
          ) : null}

          {recentRows.length > 0 ? (
            <CommandGroup heading="Recently viewed">
              {recentRows.map((r) => (
                <CommandItem
                  key={`recent:${r.ticker}`}
                  value={`recent:${r.ticker}`}
                  onSelect={() => onSelect(r.ticker, { name: r.name })}
                >
                  <Clock className="size-3.5 text-muted-foreground" />
                  <Row ticker={r.ticker} name={r.name} />
                </CommandItem>
              ))}
            </CommandGroup>
          ) : null}

          {holdings.length > 0 ? (
            <CommandGroup heading="Your holdings">
              {holdings.map((p) => (
                <CommandItem
                  key={`held:${p.account_kind}:${p.ticker}`}
                  value={`held:${p.ticker}`}
                  onSelect={() => onSelect(p.ticker, { name: p.name, price: p.current_price })}
                >
                  <TrendingUp className="size-3.5 text-muted-foreground" />
                  <Row ticker={p.ticker} name={p.name} />
                  <Badge variant="outline" className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                    {accountTag(p.account_kind)}
                  </Badge>
                </CommandItem>
              ))}
            </CommandGroup>
          ) : null}

          {watched.length > 0 ? (
            <CommandGroup heading="Watchlist">
              {watched.map((w) => (
                <CommandItem
                  key={`watch:${w.id}`}
                  value={`watch:${w.symbol}`}
                  onSelect={() =>
                    onSelect(w.symbol, {
                      name: w.name,
                      price: w.price,
                      change_pct: w.change_pct,
                      currency: w.currency,
                    })
                  }
                >
                  <Star className={cn("size-3.5", w.open_flags ? "text-warning" : "text-muted-foreground")} />
                  <Row ticker={w.symbol} name={w.name} />
                  <span className="ml-auto flex shrink-0 items-center gap-2">
                    {w.open_flags ? (
                      <span className="rounded bg-warning/15 px-1.5 py-0.5 text-[10px] font-medium text-warning">
                        {w.open_flags} flag{w.open_flags > 1 ? "s" : ""}
                      </span>
                    ) : null}
                    {w.change_pct != null ? <PctDelta value={w.change_pct} className="text-xs" /> : null}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          ) : null}

          {extraResults.length > 0 ? (
            <CommandGroup heading="Search results">
              {extraResults.map((r) => (
                <CommandItem
                  key={`search:${r.instrument_code || r.ticker}`}
                  value={`search:${r.ticker}`}
                  onSelect={() => onSelect(r.ticker, { name: r.name, currency: r.currency })}
                >
                  <Search className="size-3.5 text-muted-foreground" />
                  <Row ticker={r.ticker} name={r.name} />
                </CommandItem>
              ))}
            </CommandGroup>
          ) : null}
        </CommandList>

        <div className="flex items-center gap-3 border-t border-border/60 px-3 py-2 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <CommandShortcut>↑↓</CommandShortcut> navigate
          </span>
          <span className="flex items-center gap-1">
            <CommandShortcut>↵</CommandShortcut> open
          </span>
          <span className="flex items-center gap-1">
            <CommandShortcut>esc</CommandShortcut> close
          </span>
        </div>
      </Command>
    </CommandDialog>
  )
}

function Row({ ticker, name }: { ticker: string; name?: string | null }) {
  return (
    <span className="flex min-w-0 items-baseline gap-2">
      <span className="font-medium">{ticker}</span>
      {name ? <span className="truncate text-xs text-muted-foreground">{name}</span> : null}
    </span>
  )
}
