import { useCallback, useEffect, useState } from 'react'
import { Loader2, Plus, Scale, TriangleAlert, X } from 'lucide-react'
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
import { DRIFT_FLAG } from './detail'
import { displayName, InstrumentLabel } from './names'
import type { BenchItem, BookGroup, OwnedHolding, Play, PlayAccount } from './types'
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

export type HoldingRow = {
  key: string
  ticker: string
  name: string
  account: PlayAccount | null
  held: OwnedHolding | null
  /** Share of the play now (0 for a leg not bought yet). */
  actual: number
  /** Target share; null for a holding outside the basket. */
  target: number | null
  why: string
}

/** Every leg of the basket (held or not yet bought) plus off-basket holdings the play owns. */
export function holdingRows(play: Play, g: BookGroup): HoldingRow[] {
  const legs = new Map(play.legs.map((l) => [l.ticker.toUpperCase(), l]))
  const leg = (t: string) => legs.get(t.toUpperCase()) ?? play.legs.find((l) => l.ticker.split('.')[0].toUpperCase() === t.toUpperCase())
  const rows: HoldingRow[] = g.owned.map((h) => {
    const l = leg(h.ticker)
    return {
      key: h.key, ticker: l?.ticker ?? h.ticker, name: h.name || l?.name || '', account: h.account_kind, held: h,
      actual: h.share_of_play ?? 0, target: h.is_leg && h.target_weight != null ? h.target_weight : null,
      why: l?.rationale ?? h.why ?? '',
    }
  })
  for (const b of g.bench) {
    if (!b.in_plan) continue
    const l = leg(b.ticker)
    rows.push({
      key: `plan:${b.ticker}`, ticker: b.ticker, name: b.name || l?.name || '', account: null, held: null, actual: 0,
      target: b.target_weight ?? l?.target_weight ?? null, why: b.why || l?.rationale || '',
    })
  }
  return rows.sort((a, b) => (b.target ?? -1) - (a.target ?? -1) || b.actual - a.actual)
}

export function driftedCount(rows: HoldingRow[], threshold = DRIFT_FLAG) {
  if (!rows.some((r) => r.held)) return 0
  return rows.filter((r) => r.target != null && Math.abs(r.actual - r.target) > threshold).length
}

/** The play's holdings: actual vs target weight per leg, then the quiet Related / Bench extras. */
export function PlayRings({ play, onPlayChange, onRebalance }: { play: Play; onPlayChange: (p: Play) => void; onRebalance?: () => void }) {
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

  const rows = holdingRows(play, g)
  const drifted = driftedCount(rows)
  return (
    <div className="space-y-8">
      <Block
        title="Holdings"
        description={
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>Share of the play</span>
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-1.5 w-4 rounded-full" style={{ background: ownerColor('play', g.color_index ?? 0) }} />
              now
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-3 w-0.5 rounded-full bg-foreground" />
              target
            </span>
          </span>
        }
        action={
          onRebalance && rows.some((r) => r.held) && play.status !== 'closed' ? (
            <Button size="sm" variant={drifted ? 'outline' : 'ghost'} onClick={onRebalance}>
              <Scale className="size-3.5" />
              Rebalance
            </Button>
          ) : null
        }
      >
        <WeightRows rows={rows} colorIndex={g.color_index ?? 0} onChanged={() => void refresh()} />
      </Block>
      <Extras play={play} g={g} onChanged={refresh} />
    </div>
  )
}

function WeightBar({ actual, target, max, color }: { actual: number; target: number | null; max: number; color: string }) {
  return (
    <div className="relative h-2 w-full rounded-full bg-muted">
      {actual > 0 ? <div className="h-full rounded-full" style={{ width: `${Math.max(1.5, (actual / max) * 100)}%`, background: color }} /> : null}
      {target != null ? (
        <span aria-hidden className="absolute -top-1 h-4 w-0.5 rounded-full bg-foreground" style={{ left: `calc(${(target / max) * 100}% - 1px)` }} />
      ) : null}
    </div>
  )
}

function WeightRows({ rows, colorIndex, onChanged }: { rows: HoldingRow[]; colorIndex: number; onChanged: () => void }) {
  const { openTicker } = useAppActions()
  const color = ownerColor('play', colorIndex)
  const max = Math.min(1, Math.max(0.1, ...rows.map((r) => Math.max(r.actual, r.target ?? 0))) * 1.12)
  if (!rows.length) {
    return <p className="py-2 text-sm text-muted-foreground">Nothing held yet. Stage a tranche, or move a holding you already own into this play.</p>
  }
  return (
    <ul className="divide-y divide-border/60">
      {rows.map((r) => {
        const h = r.held
        const drift = r.target != null && h ? r.actual - r.target : null
        const flag = drift != null && Math.abs(drift) > DRIFT_FLAG
        const cost = h ? h.value_gbp - h.ppl_gbp : 0
        return (
          <li
            key={r.key}
            className="grid grid-cols-[minmax(0,1fr)_auto_2rem] items-center gap-x-3 gap-y-2 py-3 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)_5.5rem_4rem_2rem]"
          >
            <InstrumentLabel
              symbol={r.ticker}
              hint={r.name}
              account={r.account}
              onOpen={() => openTicker(r.ticker)}
              sub={h ? (r.target == null ? 'off-basket' : undefined) : 'not bought yet'}
            />
            {/* weight: actual vs target */}
            <div className="order-last col-span-3 min-w-0 sm:order-none sm:col-span-1" title={r.why || undefined}>
              <div className="mb-1 flex items-baseline gap-1.5 text-xs">
                <span className="font-mono font-medium tabular-nums">{formatPercent(r.actual, 0)}</span>
                {r.target != null ? (
                  <span className="text-muted-foreground">
                    of play · target <span className="font-mono tabular-nums">{formatPercent(r.target, 0)}</span>
                  </span>
                ) : (
                  <span className="text-muted-foreground">of play · no target</span>
                )}
                {flag && drift != null ? (
                  <span className="ml-auto inline-flex items-center gap-1 font-mono text-warning tabular-nums">
                    <TriangleAlert className="size-3" aria-label="Drifted" />
                    {drift > 0 ? '+' : '−'}{Math.round(Math.abs(drift) * 100)} pts
                  </span>
                ) : drift != null && Math.abs(drift) >= 0.005 ? (
                  <span className="ml-auto font-mono text-muted-foreground tabular-nums">
                    {drift > 0 ? '+' : '−'}{Math.round(Math.abs(drift) * 100)} pts
                  </span>
                ) : null}
              </div>
              <WeightBar actual={r.actual} target={r.target} max={max} color={color} />
            </div>
            <div className="text-right">
              {h ? <Money value={h.value_gbp} decimals={0} className="block text-sm" /> : <span className="text-sm text-muted-foreground">—</span>}
              {h && cost > 0 ? <PctDelta value={h.ppl_gbp / cost} className="block text-[11px] sm:hidden" /> : null}
            </div>
            {h && cost > 0 ? <PctDelta value={h.ppl_gbp / cost} className="hidden text-right text-xs sm:block" /> : <span className="hidden sm:block" />}
            {h ? (
              <OwnerMenu
                ticker={h.ticker}
                owner={h.owner}
                relatedTo={h.related_to}
                asMore
                onOpen={() => openTicker(r.ticker)}
                onMoved={onChanged}
                className="size-8 justify-center justify-self-end px-0"
              />
            ) : (
              <span />
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** Related and Bench: quiet, collapsed when empty to a pair of small actions. */
function Extras({ play, g, onChanged }: { play: Play; g: BookGroup; onChanged: (p?: Play) => Promise<void> }) {
  const [adding, setAdding] = useState<null | 'related' | 'bench'>(null)
  const ideas = g.bench.filter((b) => !b.in_plan)
  const closed = play.status === 'closed'
  const showRelated = g.related.length > 0 || adding === 'related'
  const showBench = ideas.length > 0 || adding === 'bench'
  return (
    <div className="space-y-6">
      {showRelated ? (
        <RelatedRing play={play} g={g} colorIndex={g.color_index ?? 0} onChanged={onChanged} adding={adding === 'related'} setAdding={(v) => setAdding(v ? 'related' : null)} />
      ) : null}
      {showBench ? (
        <BenchRing play={play} ideas={ideas} onChanged={onChanged} adding={adding === 'bench'} setAdding={(v) => setAdding(v ? 'bench' : null)} />
      ) : null}
      {!closed && (!showRelated || !showBench) ? (
        <div className="flex flex-wrap gap-x-5 gap-y-1">
          {!showRelated ? (
            <button type="button" onClick={() => setAdding('related')} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" title="A holding owned by Core or another play that also rides this theme">
              <Plus className="size-3" /> Mark a holding as related
            </button>
          ) : null}
          {!showBench ? (
            <button type="button" onClick={() => setAdding('bench')} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" title="An idea you might buy into later">
              <Plus className="size-3" /> Add an idea
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function SubHeader({ ring, colorIndex, title, hint, right }: { ring: Ring; colorIndex: number; title: string; hint: string; right?: React.ReactNode }) {
  return (
    <div className="mb-1 flex items-baseline gap-2 border-b border-border/60 pb-1.5">
      <RingMark ring={ring} colorIndex={colorIndex} className="translate-y-px" />
      <h3 className="text-xs font-semibold tracking-tight text-muted-foreground">{title}</h3>
      <span className="truncate text-xs text-muted-foreground/80">{hint}</span>
      <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-muted-foreground">{right}</span>
    </div>
  )
}

function RelatedRing({
  play,
  g,
  colorIndex,
  onChanged,
  adding,
  setAdding,
}: {
  play: Play
  g: BookGroup
  colorIndex: number
  onChanged: (p?: Play) => Promise<void>
  adding: boolean
  setAdding: (v: boolean) => void
}) {
  const { book } = useBook()
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
      <SubHeader
        ring="related"
        colorIndex={colorIndex}
        title="Related"
        hint="owned elsewhere · not counted"
        right={g.related.length ? <Money value={value} decimals={0} /> : null}
      />
      {g.related.length ? (
        <ul>
          {g.related.map((r) => (
            <li key={r.key} className="group/rel flex items-center gap-3 py-2 text-sm">
              <InstrumentLabel symbol={r.ticker} hint={r.name} account={r.account_kind} iconSize={22} sub={r.why || undefined} className="min-w-0 flex-1 opacity-90" />
              <OwnerMenu ticker={r.ticker} owner={r.owner} compact onMoved={() => void onChanged()} className="hidden max-w-28 sm:inline-flex" />
              <Money value={r.value_gbp} decimals={0} className="w-20 shrink-0 text-right text-muted-foreground" />
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
      ) : null}
      {adding ? (
        <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
          <Select value={ticker ?? undefined} onValueChange={(v) => setTicker(v as string)}>
            <SelectTrigger size="sm" className="h-8 w-full text-xs sm:w-44">
              <SelectValue placeholder="Holding…" />
            </SelectTrigger>
            <SelectContent>
              {candidates.length === 0 ? <SelectItem value="__none" disabled>No other holdings</SelectItem> : null}
              {candidates.map((h) => (
                <SelectItem key={h.ticker} value={h.ticker}>
                  {h.name || h.ticker} <span className="text-muted-foreground">· {h.ticker} · {h.owner.title}</span>
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
          <Plus className="size-3" /> Mark another
        </button>
      )}
    </section>
  )
}

function BenchRing({
  play,
  ideas,
  onChanged,
  adding,
  setAdding,
}: {
  play: Play
  ideas: BenchItem[]
  onChanged: (p?: Play) => Promise<void>
  adding: boolean
  setAdding: (v: boolean) => void
}) {
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
      toast.success(`${displayName(t)} added to the plan`, { description: 'Re-sized — later tranches include it.' })
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
      <SubHeader ring="bench" colorIndex={0} title="Bench" hint="ideas for later tranches · not held" />
      {ideas.length ? (
        <ul>
          {ideas.map((b) => (
            <li key={b.ticker} className="group/bench flex items-center gap-3 py-2 text-sm">
              <InstrumentLabel symbol={b.ticker} hint={b.name} iconSize={22} sub={b.why || undefined} className="min-w-0 flex-1" />
              <Button size="sm" variant="ghost" className="h-7 shrink-0 px-2 text-xs" disabled={closed || busy !== null} onClick={() => void promote(b.ticker)}>
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
            </li>
          ))}
        </ul>
      ) : null}
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
          <Plus className="size-3" /> Add another idea
        </button>
      ) : null}
    </section>
  )
}
