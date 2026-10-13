/**
 * The book by play — shared client state + the small primitives every surface
 * uses to show and change who owns a holding.
 *
 *   useBook()        one cached /plays/book fetch shared by Home, Holdings, Plays
 *   ownerColor()     the swatch for a play / Core / Unsorted / cash
 *   <OwnerMenu/>     a holding's owner as a quiet chip; click to move it
 */
import { useCallback, useEffect, useSyncExternalStore } from 'react'
import { ArrowRightLeft, Check, ChevronDown, Layers } from 'lucide-react'
import { ArchieIcon } from '@/components/kit'
import { toast } from 'sonner'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { toastApiError } from '@/lib/api-error'
import { cn } from '@/lib/utils'
import { assignOwner, getBook } from './api'
import type { Book, BookGroup, HoldingOwner, OwnerKind, PlayAccount } from './types'

// ── Store ───────────────────────────────────────────────────────────────────

type BookState = { book: Book | null; error: string | null; loading: boolean; at: number }

let state: BookState = { book: null, error: null, loading: false, at: 0 }
let inflight: Promise<void> | null = null
const listeners = new Set<() => void>()

function set(next: Partial<BookState>) {
  state = { ...state, ...next }
  listeners.forEach((l) => l())
}

/** Re-fetch the book (deduped). Call after anything that changes ownership or holdings. */
export function reloadBook(): Promise<void> {
  if (inflight) return inflight
  set({ loading: true })
  inflight = getBook()
    .then((book) => set({ book, error: null, loading: false, at: Date.now() }))
    .catch((e) => {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      set({ error: typeof detail === 'string' ? detail : 'Your book is unavailable right now.', loading: false, at: Date.now() })
    })
    .finally(() => {
      inflight = null
    })
  return inflight
}

const STALE_MS = 30_000

/** Shared book. `bump` (e.g. the portfolio's last-update minute) forces a refetch when it changes. */
export function useBook(bump?: string | null) {
  const snap = useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    () => state,
  )
  useEffect(() => {
    if (!state.book || Date.now() - state.at > STALE_MS || bump) void reloadBook()
  }, [bump])
  const reload = useCallback(() => reloadBook(), [])
  return { ...snap, reload }
}

/** Owner lookup by `${account}:${TICKER}` for tables built from portfolio positions. */
export function holdingKey(account: string, ticker: string) {
  return `${account}:${ticker.toUpperCase()}`
}

// ── Colour ──────────────────────────────────────────────────────────────────

const PLAY_COLORS = Array.from({ length: 8 }, (_, i) => `var(--color-chart-${i + 1})`)
export const UNSORTED_FILL =
  'repeating-linear-gradient(135deg, color-mix(in oklab, var(--color-muted-foreground) 55%, transparent) 0 2px, transparent 2px 5px)'

export function ownerColor(kind: OwnerKind | 'cash', colorIndex = 0): string {
  if (kind === 'play') return PLAY_COLORS[colorIndex % PLAY_COLORS.length]
  if (kind === 'core') return 'color-mix(in oklab, var(--color-foreground) 38%, transparent)'
  if (kind === 'cash') return 'var(--color-border)'
  return UNSORTED_FILL
}

export function groupColor(g: Pick<BookGroup, 'kind' | 'color_index'>) {
  return ownerColor(g.kind, g.color_index ?? 0)
}

export function Swatch({ kind, colorIndex, className }: { kind: OwnerKind | 'cash'; colorIndex?: number; className?: string }) {
  const fill = ownerColor(kind, colorIndex)
  return (
    <span
      aria-hidden
      className={cn('inline-block size-2 shrink-0 rounded-[3px]', kind === 'unsorted' && 'ring-1 ring-muted-foreground/40 ring-inset', className)}
      style={{ background: fill }}
    />
  )
}

function colorIndexFor(book: Book | null, owner: HoldingOwner): number {
  if (!book || owner.kind !== 'play') return 0
  return book.groups.find((g) => g.play_id === owner.play_id)?.color_index ?? 0
}

// ── Owner chip + move menu ──────────────────────────────────────────────────

/**
 * A holding's owner as a quiet chip. Click to move the holding to another
 * play, Core or Unsorted. Shows "also relevant to …" when other plays list it.
 * Moving never trades — it only changes which bucket counts it.
 */
export function OwnerMenu({
  ticker,
  owner,
  relatedTo = [],
  accountKind,
  compact = false,
  asMove = false,
  onMoved,
  className,
}: {
  /** Inside a group the owner is already obvious — show a quiet "Move" trigger instead of the chip. */
  asMove?: boolean
  ticker: string
  owner: HoldingOwner
  relatedTo?: { play_id: string; title: string; why: string }[]
  /** Restrict the move to one account; omit to move it everywhere it's held. */
  accountKind?: PlayAccount
  compact?: boolean
  onMoved?: () => void
  className?: string
}) {
  const { book } = useBook()
  const plays = (book?.groups ?? []).filter((g) => g.kind === 'play')

  async function move(target: string, label: string) {
    try {
      await assignOwner(ticker, target, accountKind)
      await reloadBook()
      onMoved?.()
      toast.success(`${ticker} → ${label}`, { description: 'Bookkeeping only — nothing was traded.' })
    } catch (e) {
      toastApiError(e, `Could not move ${ticker}`)
    }
  }

  const current = owner.kind === 'play' ? owner.play_id : owner.kind
  const isCurrent = (id: string | null) => id === current

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        onClick={(e) => e.stopPropagation()}
        aria-label={`${ticker} belongs to ${owner.title}. Change`}
        className={cn(
          'group/owner inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none data-[popup-open]:bg-muted data-[popup-open]:text-foreground',
          className,
        )}
      >
        {asMove ? (
          <>
            <ArrowRightLeft className="size-3 shrink-0" />
            <span className="hidden sm:inline">Move</span>
          </>
        ) : (
          <>
            <Swatch kind={owner.kind} colorIndex={colorIndexFor(book, owner)} />
            <span className={cn('truncate', owner.kind === 'unsorted' && 'italic')}>{owner.title}</span>
            {!compact && relatedTo.length ? (
              <span className="hidden shrink-0 text-muted-foreground/70 sm:inline">+{relatedTo.length}</span>
            ) : null}
            <ChevronDown className="size-3 shrink-0 opacity-0 transition-opacity group-hover/owner:opacity-60 group-data-[popup-open]/owner:opacity-60" />
          </>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64" onClick={(e) => e.stopPropagation()}>
        {relatedTo.length ? (
          <>
            <DropdownMenuGroup>
              <DropdownMenuLabel>Also relevant to</DropdownMenuLabel>
              {relatedTo.map((r) => (
                <div key={r.play_id} className="px-1.5 pb-1.5 text-xs">
                  <span className="font-medium text-foreground">{r.title}</span>
                  {r.why ? <span className="block line-clamp-2 text-muted-foreground">{r.why}</span> : null}
                </div>
              ))}
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
          </>
        ) : null}
        <DropdownMenuGroup>
          <DropdownMenuLabel>Belongs to</DropdownMenuLabel>
          {plays.map((g) => (
            <DropdownMenuItem key={g.id} onClick={() => !isCurrent(g.play_id) && void move(g.play_id!, g.title)}>
              <Swatch kind="play" colorIndex={g.color_index} />
              <span className="min-w-0 flex-1 truncate">{g.title}</span>
              {isCurrent(g.play_id) ? <Check className="size-3.5" /> : null}
            </DropdownMenuItem>
          ))}
          {plays.length ? <DropdownMenuSeparator /> : null}
          <DropdownMenuItem onClick={() => !isCurrent('core') && void move('core', 'Core')}>
            <Swatch kind="core" />
            <span className="min-w-0 flex-1">
              Core
              <span className="block text-[11px] text-muted-foreground">Long-term, index, no thesis to track</span>
            </span>
            {isCurrent('core') ? <Check className="size-3.5" /> : null}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => !isCurrent('unsorted') && void move('unsorted', 'Unsorted')}>
            <Swatch kind="unsorted" />
            <span className="min-w-0 flex-1">Unsorted</span>
            {isCurrent('unsorted') ? <Check className="size-3.5" /> : null}
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <p className="flex items-center gap-1.5 px-1.5 py-1 text-[11px] text-muted-foreground">
          <Layers className="size-3" />
          Changes the bookkeeping only — never trades.
        </p>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** Archie's sparkle used for "sort with Archie" style actions. */
export function ArchieMark({ className }: { className?: string }) {
  return <ArchieIcon className={cn('size-3.5', className)} />
}
