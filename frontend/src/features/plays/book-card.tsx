import { useState } from 'react'
import { ArrowRight, ChevronRight } from 'lucide-react'

import { Money, Pct, PctDelta, Section, SectionLink } from '@/components/kit'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useAppActions } from '@/lib/app-actions'
import { cn } from '@/lib/utils'
import { formatPercent } from '@/utils/format'
import { ArchieMark, OwnerMenu, Swatch, groupColor, ownerColor, useBook } from './book'
import { SortHoldingsSheet } from './suggestions'
import type { Book, BookGroup, Need } from './types'

const NEED_DOT: Record<Need['tone'], string> = {
  critical: 'bg-negative',
  warn: 'bg-warning',
  info: 'bg-muted-foreground/50',
}

/** The one allocation bar: plays, Core, Unsorted, then cash — always sums to the whole book. */
export function AllocationBar({ book, className }: { book: Book; className?: string }) {
  const segs = [
    ...book.groups.filter((g) => g.value_gbp > 0).map((g) => ({ key: g.id, label: g.title, w: g.weight, fill: groupColor(g) })),
    ...(book.cash_gbp > 0 ? [{ key: 'cash', label: 'Cash', w: book.cash_weight, fill: ownerColor('cash') }] : []),
  ]
  return (
    <div className={cn('flex h-2.5 w-full gap-[2px] overflow-hidden rounded-full', className)} role="img" aria-label="Book by play">
      {segs.map((s) => (
        <span
          key={s.key}
          title={`${s.label} · ${formatPercent(s.w, 1)}`}
          className="h-full first:rounded-l-full last:rounded-r-full"
          style={{ width: `${Math.max(s.w * 100, 0.6)}%`, background: s.fill }}
        />
      ))}
    </div>
  )
}

/**
 * Home's lead section: the book by play. One bar, one calm row per play (+
 * Core, Unsorted, cash) with value, weight, P&L and the single thing that
 * needs you. Rows expand to the holdings inside, each re-assignable in place.
 */
export function BookByPlay({ bump }: { bump?: string | null }) {
  const { navigate } = useAppActions()
  const { book, error, loading } = useBook(bump)
  const [open, setOpen] = useState<string | null>(null)
  const [sorting, setSorting] = useState(false)

  if (!book) {
    return (
      <Section title="Your book">
        {error && !loading ? (
          <p className="text-sm text-muted-foreground">{error}</p>
        ) : (
          <div className="space-y-3">
            <Skeleton className="h-2.5 w-full rounded-full" />
            <Skeleton className="h-40 w-full rounded-xl" />
          </div>
        )}
      </Section>
    )
  }

  const plays = book.groups.filter((g) => g.kind === 'play')
  const core = book.groups.find((g) => g.kind === 'core')!
  const unsorted = book.groups.find((g) => g.kind === 'unsorted')!
  const rows = [...plays, ...(core.holdings_count ? [core] : []), ...(unsorted.holdings_count ? [unsorted] : [])]
  const inPlays = plays.reduce((s, g) => s + g.weight, 0)

  return (
    <Section
      title="Your book"
      description={
        plays.length
          ? `${plays.length} play${plays.length === 1 ? '' : 's'} · ${formatPercent(inPlays, 0)} of the book is in a thesis`
          : `${book.holdings.length} holdings — none in a play yet`
      }
      action={<SectionLink onClick={() => navigate('/plays')}>All plays →</SectionLink>}
    >
      {book.holdings.length === 0 ? (
        <p className="py-6 text-sm text-muted-foreground">No holdings yet. When you buy something it shows up here, grouped by play.</p>
      ) : (
        <>
          <AllocationBar book={book} />
          <div className="mt-4 divide-y divide-border/60 border-y border-border/60">
            {rows.map((g) => (
              <GroupRow
                key={g.id}
                group={g}
                open={open === g.id}
                onToggle={() => setOpen(open === g.id ? null : g.id)}
                onOpenPlay={() => g.play_id && navigate(`/plays/${g.play_id}`)}
              />
            ))}
            {book.cash_gbp > 0 ? (
              <div className="flex items-center gap-3 py-2.5 pr-2 pl-[26px] text-sm">
                <Swatch kind="cash" />
                <span className="min-w-0 flex-1 text-muted-foreground">Cash</span>
                <Money value={book.cash_gbp} decimals={0} className="w-20 sm:w-24 shrink-0 text-right text-muted-foreground" />
                <Pct value={book.cash_weight} className="w-12 shrink-0 text-right text-xs text-muted-foreground" />
                <span className="hidden w-14 shrink-0 sm:inline" />
              </div>
            ) : null}
          </div>

          {unsorted.holdings_count > 0 ? (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-muted/50 px-4 py-3">
              <p className="min-w-0 text-sm text-muted-foreground">
                <span className="text-foreground">
                  {unsorted.holdings_count} holding{unsorted.holdings_count === 1 ? '' : 's'} ({formatPercent(unsorted.weight, 0)})
                </span>{' '}
                {unsorted.holdings_count === 1 ? "isn't" : "aren't"} in any play yet.
              </p>
              <Button size="sm" variant="outline" onClick={() => setSorting(true)}>
                <ArchieMark />
                Sort with Archie
              </Button>
            </div>
          ) : null}
        </>
      )}
      <SortHoldingsSheet open={sorting} onOpenChange={setSorting} />
    </Section>
  )
}

function GroupRow({
  group: g,
  open,
  onToggle,
  onOpenPlay,
}: {
  group: BookGroup
  open: boolean
  onToggle: () => void
  onOpenPlay: () => void
}) {
  // High signal only: one cue when something needs you, otherwise nothing.
  const need = g.needs[0]
  const quiet = g.status === 'paused' ? 'Paused' : null
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="group flex min-h-12 w-full items-center gap-3 py-2.5 pr-2 text-left transition-colors hover:bg-muted/30"
      >
        <ChevronRight className={cn('size-3.5 shrink-0 text-muted-foreground/60 transition-transform', open && 'rotate-90')} />
        <Swatch kind={g.kind} colorIndex={g.color_index} className="size-2.5" />
        <span className="min-w-0 flex-1">
          <span className={cn('block truncate text-sm font-medium', g.kind === 'unsorted' && 'font-normal italic text-muted-foreground')}>
            {g.title}
          </span>
          {need ? (
            <span className="mt-0.5 flex items-center gap-1.5 text-xs text-foreground/80">
              <span className={cn('size-1.5 shrink-0 rounded-full', NEED_DOT[need.tone])} />
              <span className="truncate">{need.text}</span>
              {g.needs.length > 1 ? <span className="shrink-0 text-muted-foreground">+{g.needs.length - 1}</span> : null}
            </span>
          ) : quiet ? (
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">{quiet}</span>
          ) : null}
        </span>
        <Money value={g.value_gbp} decimals={0} className="w-20 shrink-0 text-right text-sm sm:w-24" />
        <Pct value={g.weight} className="w-12 shrink-0 text-right text-xs text-muted-foreground" />
        <PctDelta value={g.ppl_pct} className="hidden w-14 shrink-0 text-right text-xs sm:inline" />
      </button>
      {open ? (
        <div className="pb-3 pl-6 animate-in fade-in slide-in-from-top-1 duration-200">
          {g.owned.length === 0 ? (
            <p className="py-2 text-xs text-muted-foreground">
              Nothing held yet{g.kind === 'play' && g.tranches_total ? ` · ${g.tranches_staged}/${g.tranches_total} tranches staged` : ''}.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {g.owned.map((h) => (
                <HoldingLine key={h.key} h={h} />
              ))}
            </ul>
          )}
          {g.kind === 'play' ? (
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {g.needs.slice(1).map((n) => (
                <span key={n.text} className="inline-flex items-center gap-1.5">
                  <span className={cn('size-1.5 rounded-full', NEED_DOT[n.tone])} />
                  {n.text}
                </span>
              ))}
              {g.related.length ? <span>Related: {g.related.map((r) => r.ticker).join(', ')}</span> : null}
              <button type="button" onClick={onOpenPlay} className="ml-auto inline-flex items-center gap-1 text-foreground hover:underline">
                Open play <ArrowRight className="size-3" />
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function HoldingLine({ h }: { h: BookGroup['owned'][number] }) {
  const { openTicker } = useAppActions()
  const cost = h.value_gbp - h.ppl_gbp
  return (
    <li className="flex items-center gap-2 rounded-md py-1 pr-2 text-sm">
      <button type="button" onClick={() => openTicker(h.ticker)} className="w-12 shrink-0 text-left font-mono text-[13px] font-medium hover:underline sm:w-16">
        {h.ticker}
      </button>
      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
        {h.name}
        {h.related_to.length ? <span className="text-muted-foreground/70"> · also {h.related_to.map((r) => r.title).join(', ')}</span> : null}
      </span>
      <Money value={h.value_gbp} decimals={0} className="w-20 shrink-0 text-right text-xs" />
      <PctDelta value={cost > 0 ? h.ppl_gbp / cost : null} className="hidden w-14 shrink-0 text-right text-xs sm:inline" />
      <OwnerMenu ticker={h.ticker} owner={h.owner} relatedTo={h.related_to} asMove className="shrink-0" />
    </li>
  )
}
