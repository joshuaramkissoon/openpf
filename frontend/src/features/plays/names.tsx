import { useEffect, useState } from 'react'
import { CircleAlert, CircleCheck } from 'lucide-react'

import { AssetIcon } from '@/components/kit'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { accountLabel } from '@/utils/format'
import { getInstrumentNames, type InstrumentName } from './api'

/**
 * Readable instrument names for market symbols ("BA.L" → "BAE Systems · BA, UK").
 * One shared cache; requests for the symbols on screen are batched into a single
 * call, and names the backend is still looking up are asked for again once.
 */

const cache = new Map<string, InstrumentName>()
const listeners = new Set<() => void>()
let queued = new Set<string>()
let timer: number | null = null
const retried = new Set<string>()

function notify() {
  for (const l of listeners) l()
}

function flush() {
  timer = null
  const batch = [...queued]
  queued = new Set()
  for (let i = 0; i < batch.length; i += 40) {
    const chunk = batch.slice(i, i + 40)
    getInstrumentNames(chunk)
      .then((items) => {
        const pending: string[] = []
        for (const it of items) {
          cache.set(it.symbol, it)
          if (it.pending && !retried.has(it.symbol)) pending.push(it.symbol)
        }
        notify()
        if (pending.length) {
          for (const s of pending) retried.add(s)
          window.setTimeout(() => {
            for (const s of pending) cache.delete(s)
            request(pending)
          }, 4000)
        }
      })
      .catch(() => {
        // Names are a nicety: keep showing the ticker.
        for (const s of chunk) if (!cache.has(s)) cache.set(s, fallback(s))
        notify()
      })
  }
}

function request(symbols: string[]) {
  let added = false
  for (const s of symbols) {
    if (!s || cache.has(s) || queued.has(s)) continue
    queued.add(s)
    added = true
  }
  if (added && timer == null) timer = window.setTimeout(flush, 60)
}

/** Ticker without the exchange suffix — the client-side guess while a name loads. */
export function cleanTicker(symbol: string) {
  const m = symbol.match(/^(.+)\.([A-Z]{1,2})$/)
  const known = ['L', 'IL', 'DE', 'F', 'PA', 'AS', 'BR', 'MI', 'MC', 'LS', 'SW', 'VI', 'ST', 'CO', 'OL', 'HE', 'IR', 'TO', 'V', 'AX', 'HK', 'T', 'KS', 'TW']
  if (m && known.includes(m[2])) return m[1].replace(/-/g, ' ')
  return symbol
}

function fallback(symbol: string): InstrumentName {
  return { symbol, ticker: cleanTicker(symbol), exchange: null, name: null, tradable: null, accounts: [] }
}

export function useInstrumentNames(symbols: (string | null | undefined)[]) {
  const key = symbols.filter(Boolean).map((s) => s!.toUpperCase()).join(',')
  const [, bump] = useState(0)
  useEffect(() => {
    const l = () => bump((n) => n + 1)
    listeners.add(l)
    return () => {
      listeners.delete(l)
    }
  }, [])
  useEffect(() => {
    if (key) request(key.split(','))
  }, [key])
  // Rebuilt every render (cheap): the listener above re-renders when names land.
  const out = new Map<string, InstrumentName>()
  for (const s of key ? key.split(',') : []) out.set(s, cache.get(s) ?? fallback(s))
  return out
}

export function useInstrumentName(symbol: string, hint?: string | null): InstrumentName {
  const map = useInstrumentNames([symbol])
  const n = map.get(symbol.toUpperCase()) ?? fallback(symbol)
  return hint && !n.name ? { ...n, name: hint } : n
}

function formatPrice(price: number, currency?: string | null) {
  const c = (currency ?? '').trim()
  if (c === 'GBp' || c.toUpperCase() === 'GBX') return `${price.toLocaleString('en-GB', { maximumFractionDigits: 1 })}p`
  try {
    return price.toLocaleString('en-GB', { style: 'currency', currency: c.toUpperCase() || 'USD', maximumFractionDigits: price < 10 ? 3 : 2 })
  } catch {
    return `${price.toLocaleString('en-GB', { maximumFractionDigits: 2 })} ${c}`
  }
}

/** Two-letter exchange country, tiny and quiet. US listings show nothing. */
function ExchangeTag({ n, className }: { n: InstrumentName; className?: string }) {
  if (!n.exchange || n.exchange.code === 'US') return null
  return (
    <span
      title={n.exchange.venue}
      className={cn('rounded-[3px] border border-border px-[3px] font-mono text-[9px] leading-[13px] font-medium tracking-wide text-muted-foreground', className)}
    >
      {n.exchange.code}
    </span>
  )
}

function NameCard({ n, account }: { n: InstrumentName; account?: string | null }) {
  const notListed = n.tradable === false
  const wrongAccount = !notListed && account && n.accounts.length > 0 && !n.accounts.includes(account)
  return (
    <div className="space-y-2.5">
      <div className="flex items-start gap-2.5">
        <AssetIcon symbol={n.ticker} size={28} />
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">{n.name ?? n.ticker}</p>
          <p className="text-xs text-muted-foreground">
            <span className="font-mono">{n.ticker}</span>
            {n.exchange ? ` · ${n.exchange.venue === 'US' ? 'US listing' : n.exchange.venue}` : ''}
          </p>
        </div>
      </div>
      {n.sector || n.price ? (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          {n.sector ? (
            <>
              <dt className="text-muted-foreground">Sector</dt>
              <dd className="truncate text-right" title={n.industry ?? undefined}>{n.sector}</dd>
            </>
          ) : null}
          {n.price ? (
            <>
              <dt className="text-muted-foreground">Last price</dt>
              <dd className="text-right font-mono tabular-nums">{formatPrice(n.price, n.currency)}</dd>
            </>
          ) : null}
        </dl>
      ) : null}
      {n.tradable === true && !wrongAccount ? (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <CircleCheck className="size-3.5 text-positive" />
          On Trading 212 · {n.accounts.map((a) => accountLabel(a)).join(', ')}
        </p>
      ) : notListed ? (
        <p className="flex items-center gap-1.5 text-xs text-warning">
          <CircleAlert className="size-3.5" />
          Not listed on Trading 212
        </p>
      ) : wrongAccount ? (
        <p className="flex items-center gap-1.5 text-xs text-warning">
          <CircleAlert className="size-3.5" />
          Only in {n.accounts.map((a) => accountLabel(a)).join(', ')}
        </p>
      ) : null}
      <p className="font-mono text-[10px] text-muted-foreground/70">{n.symbol}</p>
    </div>
  )
}

/** Hover (desktop) / tap (touch) card with the name, venue, sector, price and T212 status. */
export function NamePopover({
  n,
  account,
  children,
  className,
}: {
  n: InstrumentName
  account?: string | null
  children: React.ReactNode
  className?: string
}) {
  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        delay={250}
        render={<span role="button" tabIndex={0} />}
        className={cn('cursor-default rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring', className)}
      >
        {children}
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="w-64">
        <NameCard n={n} account={account} />
      </PopoverContent>
    </Popover>
  )
}

/** A chip: monogram, company name first, clean ticker small, exchange country. */
export function InstrumentChip({
  symbol,
  hint,
  account,
  className,
}: {
  symbol: string
  /** A name we already know (e.g. from the draft), used until the lookup lands. */
  hint?: string | null
  account?: string | null
  className?: string
}) {
  const n = useInstrumentName(symbol, hint)
  const flagged = n.tradable === false
  return (
    <NamePopover n={n} account={account}>
      <span
        className={cn(
          'inline-flex max-w-56 items-center gap-1.5 rounded-md border bg-muted/40 py-0.5 pr-1.5 pl-0.5 text-[11px] text-foreground/90 transition-colors hover:bg-muted',
          flagged ? 'border-dashed border-warning/50' : 'border-border',
          className,
        )}
      >
        <AssetIcon symbol={n.ticker} size={16} />
        {n.name ? (
          <>
            <span className="truncate font-medium">{n.name}</span>
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{n.ticker}</span>
          </>
        ) : (
          <span className="font-mono">{n.ticker}</span>
        )}
        <ExchangeTag n={n} />
        {flagged ? <CircleAlert className="size-3 shrink-0 text-warning" aria-label="Not on Trading 212" /> : null}
      </span>
    </NamePopover>
  )
}

/** Two-line label for rows: name on top, clean ticker + exchange below. */
export function InstrumentLabel({
  symbol,
  hint,
  account,
  sub,
  iconSize = 28,
  className,
  onOpen,
}: {
  symbol: string
  hint?: string | null
  account?: string | null
  /** Extra meta after the ticker on the second line. */
  sub?: React.ReactNode
  iconSize?: number
  className?: string
  onOpen?: () => void
}) {
  const n = useInstrumentName(symbol, hint)
  return (
    <span className={cn('flex min-w-0 items-center gap-2.5', className)}>
      <AssetIcon symbol={n.ticker} size={iconSize} crypto={account === 'crypto'} />
      <span className="min-w-0">
        <NamePopover n={n} account={account} className="block max-w-full">
          {onOpen ? (
            <button type="button" onClick={onOpen} className="block max-w-full truncate text-left text-sm font-medium hover:underline">
              {n.name ?? n.ticker}
            </button>
          ) : (
            <span className="block truncate text-sm font-medium">{n.name ?? n.ticker}</span>
          )}
        </NamePopover>
        <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
          {n.name ? <span className="shrink-0 font-mono">{n.ticker}</span> : null}
          <ExchangeTag n={n} />
          {sub ? (
            <>
              {n.name ? <span aria-hidden>·</span> : null}
              <span className="min-w-0 truncate">{sub}</span>
            </>
          ) : null}
        </span>
      </span>
    </span>
  )
}

/** Plain-text display name for toasts and prompts. */
export function displayName(symbol: string, hint?: string | null) {
  const n = cache.get(symbol.toUpperCase())
  return n?.name ?? hint ?? cleanTicker(symbol)
}

/** Inline name for a leg inside another control (no popover): company name, ticker on hover. */
export function LegName({ symbol, hint, className }: { symbol: string; hint?: string | null; className?: string }) {
  const n = useInstrumentName(symbol, hint)
  return (
    <span className={className} title={`${n.ticker}${n.exchange && n.exchange.code !== 'US' ? ` · ${n.exchange.venue}` : ''}`}>
      {n.name ?? n.ticker}
    </span>
  )
}
