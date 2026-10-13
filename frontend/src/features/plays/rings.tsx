import { useCallback, useEffect, useState } from 'react'
import { Loader2, Plus, X } from 'lucide-react'
import { toast } from 'sonner'

import { Money, PctDelta } from '@/components/kit'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { toastApiError } from '@/lib/api-error'
import { useAppActions } from '@/lib/app-actions'
import { cn } from '@/lib/utils'
import { formatPercent } from '@/utils/format'
import { addBench, addRelated, getRings, promoteBench, removeBench, removeRelated } from './api'
import { OwnerMenu, ownerColor, reloadBook, useBook } from './book'
import type { BookGroup, Play } from './types'
import { Block } from './ui'

type Ring = 'owned' | 'related' | 'bench'

/** Ring markers: filled = owned (counts), hollow = related (doesn't), dashed = bench (not held). */
export function RingMark({ ring, colorIndex = 0, className }: { ring: Ring; colorIndex?: number; className?: string }) {
  if (ring === 'owned') {
    return <span aria-hidden className={cn('inline-block size-2.5 shrink-0 rounded-full', className)} style={{ background: ownerColor('play', colorIndex) }} />
  }
  return (
    <span
      aria-hidden
      className={cn('inline-block size-2.5 shrink-0 rounded-full border-[1.5px]', ring === 'bench' && 'border-dashed', className)}
      style={{ borderColor: ring === 'related' ? ownerColor('play', colorIndex) : 'var(--color-muted-foreground)' }}
    />
  )
}

/** The play's holdings in three rings: owned, related (owned elsewhere), bench (not held). */
export function PlayRings({ play, onPlayChange }: { play: Play; onPlayChange: (p: Play) => void }) {
  const { book } = useBook()
  const fromBook = book?.groups.find((g) => g.play_id === play.id) ?? null
  const [fallback, setFallback] = useState<BookGroup | null>(null)
  const [failed, setFailed] = useState(false)

  const loadFallback = useCallback(async () => {
    try {
      setFallback(await getRings(play.id))
      setFailed(false)
    } catch {
      setFailed(true)
    }
  }, [play.id])

  useEffect(() => {
    if (book && !fromBook) void loadFallback()
  }, [book, fromBook, loadFallback, play.updated_at])

  const g = fromBook ?? fallback

  async function refresh(next?: Play) {
    if (next) onPlayChange(next)
    await reloadBook()
    if (!fromBook) await loadFallback()
  }

  if (!g) {
    return (
      <Block title="Holdings">
        {failed ? <p className="text-sm text-muted-foreground">Live holdings are unavailable right now.</p> : <Skeleton className="h-48 w-full" />}
      </Block>
    )
  }

  const ci = g.color_index ?? 0
  const relatedExtra = (g.effective_weight ?? g.weight) - g.weight
  return (
    <Block
      title="Holdings"
      description={
        <>
          <span className="text-foreground">{formatPercent(g.weight, 1)} of your book</span>
          {relatedExtra >= 0.001 ? ` direct · ${formatPercent(g.effective_weight ?? 0, 1)} incl. related` : ' direct'}
          {g.target_pct ? ` · target ${formatPercent(g.target_pct, 0)}` : ''}
        </>
      }
    >
      <div className="space-y-7">
        <OwnedRing g={g} colorIndex={ci} onChanged={() => void refresh()} />
        <RelatedRing play={play} g={g} colorIndex={ci} onChanged={refresh} />
        <BenchRing play={play} g={g} onChanged={refresh} />
      </div>
    </Block>
  )
}

function RingHeader({ ring, colorIndex, title, hint, right }: { ring: Ring; colorIndex: number; title: string; hint: string; right?: React.ReactNode }) {
  return (
    <div className="mb-1.5 flex items-baseline gap-2 border-b border-border/60 pb-1.5">
      <RingMark ring={ring} colorIndex={colorIndex} className="translate-y-px" />
      <h3 className="text-xs font-semibold tracking-tight">{title}</h3>
      <span className="truncate text-xs text-muted-foreground">{hint}</span>
      <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-muted-foreground">{right}</span>
    </div>
  )
}

function OwnedRing({ g, colorIndex, onChanged }: { g: BookGroup; colorIndex: number; onChanged: () => void }) {
  const { openTicker } = useAppActions()
  return (
    <section>
      <RingHeader
        ring="owned"
        colorIndex={colorIndex}
        title="Owned"
        hint="counts toward this play"
        right={g.owned.length ? <Money value={g.value_gbp} decimals={0} /> : null}
      />
      {g.owned.length === 0 ? (
        <p className="py-2 text-xs text-muted-foreground">
          Nothing held yet. Stage a tranche, or move a holding you already own into this play.
        </p>
      ) : (
        <ul>
          {g.owned.map((h) => {
            const cost = h.value_gbp - h.ppl_gbp
            return (
              <li key={h.key} className="flex items-center gap-3 py-2 text-sm">
                <button type="button" onClick={() => openTicker(h.ticker)} className="w-12 shrink-0 text-left font-mono text-[13px] font-medium hover:underline sm:w-16">
                  {h.ticker}
                </button>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-muted-foreground">{h.name}</span>
                  <span className="block text-[11px] text-muted-foreground/80">
                    {formatPercent(h.share_of_play ?? 0, 0)} of play
                    {h.is_leg && h.target_weight != null ? ` · target ${formatPercent(h.target_weight, 0)}` : ' · off-basket'}
                  </span>
                </span>
                <Money value={h.value_gbp} decimals={0} className="w-20 shrink-0 text-right" />
                <PctDelta value={cost > 0 ? h.ppl_gbp / cost : null} className="hidden w-14 shrink-0 text-right text-xs sm:inline" />
                <OwnerMenu ticker={h.ticker} owner={h.owner} relatedTo={h.related_to} asMove onMoved={onChanged} className="shrink-0" />
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

function RelatedRing({
  play,
  g,
  colorIndex,
  onChanged,
}: {
  play: Play
  g: BookGroup
  colorIndex: number
  onChanged: (p?: Play) => Promise<void>
}) {
  const { book } = useBook()
  const [adding, setAdding] = useState(false)
  const [ticker, setTicker] = useState<string | null>(null)
  const [why, setWhy] = useState('')
  const [busy, setBusy] = useState(false)
  const value = g.related.reduce((s, r) => s + r.value_gbp, 0)
  const listed = new Set(g.related.map((r) => r.ticker))
  const candidates = [...new Map((book?.holdings ?? [])
    .filter((h) => h.owner.play_id !== play.id && !listed.has(h.ticker))
    .map((h) => [h.ticker, h])).values()]

  async function add() {
    if (!ticker) return
    setBusy(true)
    try {
      await onChanged(await addRelated(play.id, ticker, why.trim()))
      setAdding(false)
      setTicker(null)
      setWhy('')
    } catch (e) {
      toastApiError(e, 'Could not add')
    } finally {
      setBusy(false)
    }
  }

  async function remove(t: string) {
    try {
      await onChanged(await removeRelated(play.id, t))
    } catch (e) {
      toastApiError(e, 'Could not remove')
    }
  }

  return (
    <section>
      <RingHeader
        ring="related"
        colorIndex={colorIndex}
        title="Related"
        hint="owned elsewhere · not counted in size"
        right={g.related.length ? <Money value={value} decimals={0} /> : null}
      />
      {g.related.length === 0 && !adding ? (
        <p className="py-2 text-xs text-muted-foreground">Holdings owned by Core or another play that also ride this theme show up here.</p>
      ) : (
        <ul>
          {g.related.map((r) => (
            <li key={r.key} className="group/rel flex items-center gap-3 py-2 text-sm text-muted-foreground">
              <span className="w-16 shrink-0 font-mono text-[13px]">{r.ticker}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs" title={r.why}>{r.why || r.name}</span>
              </span>
              <OwnerMenu ticker={r.ticker} owner={r.owner} compact onMoved={() => void onChanged()} className="max-w-28" />
              <Money value={r.value_gbp} decimals={0} className="hidden w-20 shrink-0 text-right sm:inline" />
              <button
                type="button"
                aria-label={`Stop showing ${r.ticker} as related`}
                onClick={() => void remove(r.ticker)}
                className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground/60 transition-colors hover:bg-accent hover:text-foreground sm:opacity-0 sm:group-hover/rel:opacity-100 sm:focus-visible:opacity-100"
              >
                <X className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
          <Select value={ticker ?? undefined} onValueChange={(v) => setTicker(v as string)}>
            <SelectTrigger size="sm" className="h-8 w-full text-xs sm:w-36">
              <SelectValue placeholder="Holding…" />
            </SelectTrigger>
            <SelectContent>
              {candidates.length === 0 ? <SelectItem value="__none" disabled>No other holdings</SelectItem> : null}
              {candidates.map((h) => (
                <SelectItem key={h.ticker} value={h.ticker}>
                  {h.ticker} <span className="text-muted-foreground">· {h.owner.title}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Why it matters to this theme" className="h-8 flex-1 text-xs" maxLength={280} />
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
            <Button size="sm" onClick={() => void add()} disabled={!ticker || busy}>
              {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
              Add
            </Button>
          </div>
        </div>
      ) : (
        <button type="button" onClick={() => setAdding(true)} className="mt-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <Plus className="size-3" /> Mark a holding as related
        </button>
      )}
    </section>
  )
}

function BenchRing({ play, g, onChanged }: { play: Play; g: BookGroup; onChanged: (p?: Play) => Promise<void> }) {
  const [adding, setAdding] = useState(false)
  const [ticker, setTicker] = useState('')
  const [why, setWhy] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const closed = play.status === 'closed'

  async function add() {
    if (!ticker.trim()) return
    setBusy('add')
    try {
      await onChanged(await addBench(play.id, ticker.trim().toUpperCase(), why.trim()))
      setAdding(false)
      setTicker('')
      setWhy('')
    } catch (e) {
      toastApiError(e, 'Could not add to the bench')
    } finally {
      setBusy(null)
    }
  }

  async function promote(t: string) {
    setBusy(t)
    try {
      await onChanged(await promoteBench(play.id, t))
      toast.success(`${t} added to the plan`, { description: 'Re-sized — later tranches include it.' })
    } catch (e) {
      toastApiError(e, 'Could not add to the plan')
    } finally {
      setBusy(null)
    }
  }

  async function remove(t: string) {
    try {
      await onChanged(await removeBench(play.id, t))
    } catch (e) {
      toastApiError(e, 'Could not remove')
    }
  }

  return (
    <section>
      <RingHeader ring="bench" colorIndex={0} title="Bench" hint="not held yet · ideas for later tranches" />
      {g.bench.length === 0 && !adding ? (
        <p className="py-2 text-xs text-muted-foreground">Every leg in the plan is held. Add ideas you might buy into later.</p>
      ) : (
        <ul>
          {g.bench.map((b) => (
            <li key={b.ticker} className="group/bench flex items-center gap-3 py-2 text-sm">
              <span className="w-16 shrink-0 font-mono text-[13px] text-muted-foreground">{b.ticker}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-muted-foreground" title={b.why}>{b.why || b.name || '—'}</span>
              </span>
              {b.in_plan ? (
                <span className="shrink-0 rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                  In plan{b.target_weight != null ? ` · ${formatPercent(b.target_weight, 0)}` : ''}
                </span>
              ) : (
                <>
                  <Button size="sm" variant="ghost" className="h-6 px-2 text-xs" disabled={closed || busy !== null} onClick={() => void promote(b.ticker)}>
                    {busy === b.ticker ? <Loader2 className="size-3 animate-spin" /> : null}
                    Add to plan
                  </Button>
                  <button
                    type="button"
                    aria-label={`Remove ${b.ticker} from the bench`}
                    onClick={() => void remove(b.ticker)}
                    className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground/60 transition-colors hover:bg-accent hover:text-foreground sm:opacity-0 sm:group-hover/bench:opacity-100 sm:focus-visible:opacity-100"
                  >
                    <X className="size-3.5" />
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
          <Input
            autoFocus
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase().replace(/[^A-Z0-9.\-_]/g, ''))}
            onKeyDown={(e) => e.key === 'Enter' && void add()}
            placeholder="Ticker, e.g. GEV"
            className="h-8 w-full font-mono text-xs sm:w-32"
            maxLength={16}
          />
          <Input value={why} onChange={(e) => setWhy(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void add()} placeholder="Why it fits" className="h-8 flex-1 text-xs" maxLength={280} />
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
            <Button size="sm" onClick={() => void add()} disabled={!ticker.trim() || busy !== null}>
              {busy === 'add' ? <Loader2 className="size-3.5 animate-spin" /> : null}
              Add
            </Button>
          </div>
        </div>
      ) : !closed ? (
        <button type="button" onClick={() => setAdding(true)} className="mt-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <Plus className="size-3" /> Add an idea
        </button>
      ) : null}
    </section>
  )
}
