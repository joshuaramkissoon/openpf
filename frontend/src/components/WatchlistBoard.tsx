import { useCallback, useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'
import { Bot, Eye, EyeOff, MessageSquareText, MoreHorizontal, Plus, Target, Trash2, User } from 'lucide-react'
import { toast } from 'sonner'

import { Segmented } from '@/components/kit'
import { useInstrument, WATCHLIST_CHANGED_EVENT } from '@/components/instrument/instrument-provider'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { createWatchlistItem, deleteWatchlistItem, dismissWatchlistFlag, getWatchlist, updateWatchlistItem } from '@/api/client'
import { useAppActions } from '@/lib/app-actions'
import { cn } from '@/lib/utils'
import { formatMoney, formatSignedPercent } from '@/utils/format'
import type { WatchlistItem } from '@/types'

interface Props {
  onError: (message: string | null) => void
}

type StatusFilter = 'watching' | 'acted' | 'archived'

const SOURCE: Record<string, { label: string; Icon: typeof User }> = {
  manual: { label: 'You', Icon: User },
  archie: { label: 'Archie', Icon: Bot },
  agent_run: { label: 'Archie', Icon: Bot },
  watchlist_review: { label: 'Archie', Icon: Bot },
}

/** Within this distance of the trigger an idea reads as "close". */
const CLOSE_BAND = 0.05
/** Further than this, the proximity bar is empty. */
const FAR_BAND = 0.25

type TriggerState = 'hit' | 'close' | 'waiting' | 'none'

function triggerState(item: WatchlistItem): { state: TriggerState; distance: number | null } {
  const { price, target_price: target, target_direction: dir } = item
  if (target == null || !dir || price == null || target <= 0) return { state: 'none', distance: null }
  // Signed so that positive = still to go before the trigger.
  const distance = dir === 'below' ? (price - target) / target : (target - price) / target
  if (distance <= 0) return { state: 'hit', distance }
  return { state: distance <= CLOSE_BAND ? 'close' : 'waiting', distance }
}

function changeTone(v: number | null | undefined) {
  if (v == null || v === 0) return 'text-muted-foreground'
  return v > 0 ? 'text-positive' : 'text-negative'
}

export function WatchlistBoard({ onError }: Props) {
  const [items, setItems] = useState<WatchlistItem[]>([])
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('watching')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<WatchlistItem | null>(null)
  const [newSymbol, setNewSymbol] = useState('')
  const [newNote, setNewNote] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await getWatchlist(statusFilter)
      setItems(data)
      onError(null)
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to load watchlist')
    } finally {
      setLoading(false)
    }
  }, [statusFilter, onError])

  useEffect(() => {
    setLoading(true)
    void load()
  }, [load])

  // Refresh when the watchlist is mutated elsewhere (e.g. the stock sheet).
  useEffect(() => {
    const onChanged = () => void load()
    window.addEventListener(WATCHLIST_CHANGED_EVENT, onChanged)
    return () => window.removeEventListener(WATCHLIST_CHANGED_EVENT, onChanged)
  }, [load])

  const summary = useMemo(() => {
    const hit = items.filter((i) => triggerState(i).state === 'hit').length
    const close = items.filter((i) => triggerState(i).state === 'close').length
    const flagged = items.filter((i) => i.open_flags > 0).length
    return { hit, close, flagged }
  }, [items])

  async function handleAdd() {
    const symbol = newSymbol.trim().toUpperCase()
    if (!symbol || busy) return
    setBusy(true)
    try {
      await createWatchlistItem({ symbol, note: newNote.trim() || undefined })
      setNewSymbol('')
      setNewNote('')
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to add to watchlist')
      toast.error(`Couldn't add ${symbol}`)
    } finally {
      setBusy(false)
    }
  }

  async function patch(id: string, body: Parameters<typeof updateWatchlistItem>[1]) {
    setBusy(true)
    try {
      await updateWatchlistItem(id, body)
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to update item')
    } finally {
      setBusy(false)
    }
  }

  async function remove(item: WatchlistItem) {
    setItems((xs) => xs.filter((x) => x.id !== item.id))
    try {
      await deleteWatchlistItem(item.id)
      toast(`Stopped watching ${item.symbol}`, {
        action: {
          label: 'Undo',
          onClick: () =>
            void createWatchlistItem({
              symbol: item.symbol,
              note: item.note || undefined,
              conviction: item.conviction,
              target_price: item.target_price,
              target_direction: item.target_direction,
            }).then(load),
        },
      })
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to remove item')
      await load()
    }
  }

  async function dismissFlag(alertId: string) {
    try {
      await dismissWatchlistFlag(alertId)
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to dismiss flag')
    }
  }

  // Decision-first: ideas at their trigger, then flagged, then close, then the rest.
  const rank = (i: WatchlistItem) => {
    const st = triggerState(i).state
    return st === 'hit' ? 0 : i.open_flags > 0 ? 1 : st === 'close' ? 2 : st === 'waiting' ? 3 : 4
  }
  const ordered = items.slice().sort((a, b) => rank(a) - rank(b))

  const bits = [
    `${items.length} ${items.length === 1 ? 'idea' : 'ideas'}`,
    summary.hit ? `${summary.hit} at trigger` : null,
    summary.close ? `${summary.close} close` : null,
    summary.flagged ? `${summary.flagged} flagged` : null,
  ].filter(Boolean)

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          <Segmented<StatusFilter>
            ariaLabel="Show"
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: 'watching', label: 'Watching' },
              { value: 'acted', label: 'Acted on' },
              { value: 'archived', label: 'Archived' },
            ]}
          />
          {!loading ? <span className="text-xs text-muted-foreground">{bits.join(' · ')}</span> : null}
        </div>
        <form
          className="flex w-full gap-2 lg:w-auto"
          onSubmit={(e) => {
            e.preventDefault()
            void handleAdd()
          }}
        >
          <Input
            value={newSymbol}
            onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
            placeholder="Ticker"
            aria-label="Ticker to watch"
            className="h-8 w-24 shrink-0 font-mono text-xs uppercase"
          />
          <Input
            value={newNote}
            onChange={(e) => setNewNote(e.target.value)}
            placeholder="Why watch it?"
            aria-label="Why watch it"
            className="h-8 min-w-0 flex-1 text-xs lg:w-64"
          />
          <Button type="submit" size="sm" variant="outline" disabled={busy || !newSymbol.trim()}>
            <Plus className="size-3.5" />
            Watch
          </Button>
        </form>
      </div>

      {loading ? (
        <div className="divide-y divide-border rounded-xl border border-border">
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex items-center gap-4 px-5 py-4">
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-3 w-2/3" />
              </div>
              <Skeleton className="hidden h-8 w-28 md:block" />
              <Skeleton className="h-8 w-20" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border px-6 py-14 text-center">
          <Eye className="size-5 text-muted-foreground" />
          <p className="text-sm font-medium">
            {statusFilter === 'watching' ? 'Nothing on the watchlist' : `Nothing ${statusFilter === 'acted' ? 'acted on' : 'archived'} yet`}
          </p>
          {statusFilter === 'watching' ? (
            <p className="max-w-sm text-xs text-muted-foreground">
              Add a ticker with the reason you're watching it. Set a trigger price and Archie flags it when the setup arrives.
            </p>
          ) : null}
        </div>
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
          {ordered.map((item) => (
            <WatchRow
              key={item.id}
              item={item}
              onEdit={() => setEditing(item)}
              onPatch={(body) => void patch(item.id, body)}
              onRemove={() => void remove(item)}
              onDismissFlag={(id) => void dismissFlag(id)}
            />
          ))}
        </ul>
      )}

      {editing ? (
        <EditDialog
          item={editing}
          busy={busy}
          onClose={() => setEditing(null)}
          onSave={async (body) => {
            await patch(editing.id, body)
            setEditing(null)
          }}
        />
      ) : null}
    </div>
  )
}

function WatchRow({
  item,
  onEdit,
  onPatch,
  onRemove,
  onDismissFlag,
}: {
  item: WatchlistItem
  onEdit: () => void
  onPatch: (body: Parameters<typeof updateWatchlistItem>[1]) => void
  onRemove: () => void
  onDismissFlag: (alertId: string) => void
}) {
  const { openInstrument } = useInstrument()
  const { askArchie, navigate } = useAppActions()
  const [flagsOpen, setFlagsOpen] = useState(false)
  const src = SOURCE[item.source] ?? SOURCE.manual
  const SrcIcon = src.Icon
  const trig = triggerState(item)
  const spark = item.spark ?? []
  const periodChange = spark.length > 1 ? spark[spark.length - 1] / spark[0] - 1 : null
  const label = `${item.symbol}${item.name ? ` (${item.name})` : ''}`

  const open = () =>
    openInstrument(item.symbol, { name: item.name, price: item.price, change_pct: item.change_pct, currency: item.currency })

  const ask = () => {
    const ctx = [
      item.price != null ? `trading at ${formatMoney(item.price, item.currency || 'USD')}` : null,
      item.note ? `I'm watching it because: ${item.note}` : null,
      item.target_price != null && item.target_direction
        ? `my trigger is ${item.target_direction} ${formatMoney(item.target_price, item.currency || 'USD')}`
        : null,
    ]
      .filter(Boolean)
      .join('; ')
    askArchie(`Is ${label} still worth watching? ${ctx ? `Context: ${ctx}.` : ''} Has the setup changed, and what would make it actionable?`)
  }

  const startPlay = () => {
    const idea = item.note ? `${item.note} (${item.symbol})` : `A play built around ${label}`
    navigate(`/plays?new=1&idea=${encodeURIComponent(idea)}`)
  }

  return (
    <li className={cn('group/row transition-colors hover:bg-muted/25', !item.monitor && 'opacity-80')}>
      <div className="grid gap-x-6 gap-y-3 px-4 pt-3.5 pb-2 sm:px-5 md:grid-cols-[minmax(0,1fr)_11rem_8rem_6rem] md:items-center">
        {/* What and why */}
        <div className="min-w-0 space-y-1.5">
          <div className="flex min-w-0 items-baseline gap-2">
            <button type="button" onClick={open} className="text-sm font-semibold tracking-tight hover:underline" title={`Open ${item.symbol}`}>
              {item.symbol}
            </button>
            {item.name ? <span className="min-w-0 truncate text-xs text-muted-foreground">{item.name}</span> : null}
            {/* Price sits with the name on phones; its own column from md up. */}
            <span className="ml-auto shrink-0 text-right md:hidden">
              <PriceBlock item={item} inline />
            </span>
          </div>
          {item.note ? (
            <p className="line-clamp-2 text-sm leading-snug text-foreground/85">{item.note}</p>
          ) : (
            <button type="button" onClick={onEdit} className="text-left text-sm text-muted-foreground italic hover:text-foreground">
              Add why you're watching it
            </button>
          )}
          {item.open_flags > 0 && item.latest_flag ? (
            <button
              type="button"
              onClick={() => setFlagsOpen((v) => !v)}
              aria-expanded={flagsOpen}
              className="flex max-w-full items-center gap-1.5 text-left text-xs text-foreground/80 hover:text-foreground"
            >
              <span
                className={cn(
                  'size-1.5 shrink-0 rounded-full',
                  item.latest_severity === 'critical' ? 'bg-negative' : item.latest_severity === 'warning' ? 'bg-warning' : 'bg-muted-foreground',
                )}
              />
              <span className="truncate">{item.latest_flag}</span>
              {item.open_flags > 1 ? <span className="shrink-0 text-muted-foreground">+{item.open_flags - 1}</span> : null}
            </button>
          ) : null}
        </div>

        {/* The setup */}
        <TriggerBlock item={item} trig={trig} onEdit={onEdit} />

        {/* Recent shape */}
        <div className="flex items-center gap-3 md:block">
          <WatchSpark values={spark} trigger={trig.state === 'none' ? null : item.target_price} />
          <p className="shrink-0 text-[11px] text-muted-foreground md:mt-0.5">
            3m{' '}
            <span className={cn('font-mono tabular-nums', changeTone(periodChange))}>
              {periodChange != null ? formatSignedPercent(periodChange) : '—'}
            </span>
          </p>
        </div>

        <div className="hidden text-right md:block">
          <PriceBlock item={item} />
        </div>
      </div>

      {/* Next actions */}
      <div className="flex flex-wrap items-center gap-x-1 gap-y-1 px-2.5 pb-2 transition-opacity sm:px-3.5 md:opacity-60 md:group-focus-within/row:opacity-100 md:group-hover/row:opacity-100">
        <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground" onClick={ask}>
          <MessageSquareText className="size-3.5" />
          Ask Archie
        </Button>
        <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground" onClick={startPlay}>
          <Target className="size-3.5" />
          Start a play
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-negative"
          onClick={onRemove}
          aria-label={`Stop watching ${item.symbol}`}
        >
          <Trash2 className="size-3.5" />
          Remove
        </Button>
        <span className="ml-auto flex items-center gap-2 pr-1.5 text-[11px] text-muted-foreground">
          {!item.monitor ? (
            <span className="inline-flex items-center gap-1 text-warning">
              <EyeOff className="size-3" /> Muted
            </span>
          ) : null}
          <span className="hidden items-center gap-1 sm:inline-flex">
            <SrcIcon className="size-3" />
            {src.label}
            {item.created_at ? ` · ${dayjs(item.created_at).format('D MMM')}` : ''}
          </span>
          <DropdownMenu>
            <DropdownMenuTrigger
              className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              aria-label={`More for ${item.symbol}`}
            >
              <MoreHorizontal className="size-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={onEdit}>Edit why and trigger…</DropdownMenuItem>
              <DropdownMenuItem onClick={() => onPatch({ monitor: !item.monitor })}>
                {item.monitor ? 'Mute Archie’s checks' : 'Resume Archie’s checks'}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              {item.status !== 'acted' ? (
                <DropdownMenuItem onClick={() => onPatch({ status: 'acted' })}>Mark as acted on</DropdownMenuItem>
              ) : null}
              {item.status !== 'archived' ? (
                <DropdownMenuItem onClick={() => onPatch({ status: 'archived' })}>Archive</DropdownMenuItem>
              ) : (
                <DropdownMenuItem onClick={() => onPatch({ status: 'watching' })}>Move back to watching</DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </span>
      </div>

      {flagsOpen && item.flags.length ? (
        <div className="space-y-2 border-t border-border/60 bg-muted/20 px-4 py-3 sm:px-5">
          {item.flags.map((f) => (
            <div key={f.id} className="flex items-start gap-2.5 text-xs">
              <span
                className={cn(
                  'mt-1 size-1.5 shrink-0 rounded-full',
                  f.severity === 'critical' ? 'bg-negative' : f.severity === 'warning' ? 'bg-warning' : 'bg-muted-foreground',
                )}
              />
              <div className="min-w-0 flex-1">
                <p className="font-medium text-foreground">{f.title}</p>
                {f.detail ? <p className="mt-0.5 line-clamp-2 text-muted-foreground">{f.detail}</p> : null}
              </div>
              <span className="shrink-0 text-muted-foreground">{f.created_at ? dayjs(f.created_at).format('D MMM') : ''}</span>
              {f.url ? (
                <a href={f.url} target="_blank" rel="noreferrer" className="shrink-0 text-muted-foreground hover:text-foreground">
                  Open
                </a>
              ) : null}
              <button type="button" onClick={() => onDismissFlag(f.id)} className="shrink-0 text-muted-foreground hover:text-foreground">
                Dismiss
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </li>
  )
}

function PriceBlock({ item, inline }: { item: WatchlistItem; inline?: boolean }) {
  if (item.price == null) return <span className="text-xs text-muted-foreground">No price</span>
  return (
    <span className={cn('font-mono tabular-nums', inline ? 'inline-flex items-baseline gap-1.5' : 'flex flex-col items-end')}>
      <span className="text-sm text-foreground">{formatMoney(item.price, item.currency || 'USD')}</span>
      {item.change_pct != null ? (
        <span className={cn('text-xs', changeTone(item.change_pct))}>{formatSignedPercent(item.change_pct)}</span>
      ) : null}
    </span>
  )
}

const TRIGGER_COPY: Record<Exclude<TriggerState, 'none'>, { label: string; cls: string; bar: string }> = {
  hit: { label: 'At trigger', cls: 'bg-warning/15 text-warning', bar: 'bg-warning' },
  close: { label: 'Close', cls: 'bg-foreground/10 text-foreground', bar: 'bg-foreground/70' },
  waiting: { label: 'Waiting', cls: 'bg-muted text-muted-foreground', bar: 'bg-muted-foreground/50' },
}

function TriggerBlock({
  item,
  trig,
  onEdit,
}: {
  item: WatchlistItem
  trig: { state: TriggerState; distance: number | null }
  onEdit: () => void
}) {
  if (trig.state === 'none') {
    return (
      <div className="text-xs">
        <p className="text-muted-foreground">No trigger set</p>
        <button type="button" onClick={onEdit} className="mt-0.5 text-foreground/80 underline-offset-2 hover:text-foreground hover:underline">
          Set a price to wait for
        </button>
      </div>
    )
  }
  const copy = TRIGGER_COPY[trig.state]
  const dist = trig.distance ?? 0
  const proximity = trig.state === 'hit' ? 1 : Math.max(0.04, 1 - Math.min(1, dist / FAR_BAND))
  return (
    <div className="min-w-0 space-y-1.5 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-muted-foreground">
          {item.target_direction === 'below' ? 'Below' : 'Above'}{' '}
          <span className="font-mono text-foreground tabular-nums">{formatMoney(item.target_price ?? 0, item.currency || 'USD')}</span>
        </span>
        <span className={cn('shrink-0 rounded px-1.5 py-px text-[10px] font-medium', copy.cls)}>{copy.label}</span>
      </div>
      <div className="h-1 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
        <div className={cn('h-full rounded-full transition-[width] duration-500', copy.bar)} style={{ width: `${proximity * 100}%` }} />
      </div>
      <p className="text-[11px] text-muted-foreground">
        {trig.state === 'hit' ? (
          <>
            Crossed by <span className="font-mono tabular-nums">{Math.abs(dist * 100).toFixed(1)}%</span>
          </>
        ) : (
          <>
            <span className="font-mono tabular-nums">{(dist * 100).toFixed(1)}%</span> to go
          </>
        )}
      </p>
    </div>
  )
}

/** Sparkline of recent closes, with the trigger level as a dashed rule when it's in view. */
function WatchSpark({ values, trigger }: { values: number[]; trigger: number | null | undefined }) {
  const w = 128
  const h = 32
  if (values.length < 2) return <div className="h-8 w-full max-w-32 rounded bg-muted/40" aria-hidden />
  let min = Math.min(...values)
  let max = Math.max(...values)
  // Pull the trigger into frame only when it's near the range, so the line keeps its shape.
  const showTrigger = trigger != null && trigger > min * 0.8 && trigger < max * 1.2
  if (showTrigger) {
    min = Math.min(min, trigger)
    max = Math.max(max, trigger)
  }
  const span = max - min || 1
  const pad = 2
  const y = (v: number) => pad + (h - pad * 2) * (1 - (v - min) / span)
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const up = values[values.length - 1] >= values[0]
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-8 w-full max-w-32" aria-hidden>
      {showTrigger ? (
        <line x1={0} x2={w} y1={y(trigger)} y2={y(trigger)} className="stroke-warning/70" strokeDasharray="3 3" strokeWidth={1} vectorEffect="non-scaling-stroke" />
      ) : null}
      <polyline
        points={pts}
        fill="none"
        className={up ? 'stroke-positive' : 'stroke-negative'}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={w} cy={y(values[values.length - 1])} r={1.8} className={up ? 'fill-positive' : 'fill-negative'} />
    </svg>
  )
}

function EditDialog({
  item,
  busy,
  onClose,
  onSave,
}: {
  item: WatchlistItem
  busy: boolean
  onClose: () => void
  onSave: (body: Parameters<typeof updateWatchlistItem>[1]) => Promise<void>
}) {
  const [note, setNote] = useState(item.note)
  const [conviction, setConviction] = useState<string>(item.conviction ?? 'none')
  const [targetPrice, setTargetPrice] = useState(item.target_price != null ? String(item.target_price) : '')
  const [targetDirection, setTargetDirection] = useState<string>(item.target_direction ?? 'below')
  const [monitor, setMonitor] = useState(item.monitor)

  function save() {
    const tp = parseFloat(targetPrice)
    const hasTarget = targetPrice.trim() !== '' && !Number.isNaN(tp)
    void onSave({
      note,
      conviction: conviction === 'none' ? null : (conviction as 'low' | 'medium' | 'high'),
      monitor,
      target_price: hasTarget ? tp : null,
      target_direction: hasTarget ? (targetDirection as 'above' | 'below') : null,
    })
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Watching {item.symbol}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wl-note">Why watch it</Label>
            <Textarea
              id="wl-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="The reason. Archie's reviews check the stock against it."
              rows={3}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>Trigger</Label>
              <Select value={targetDirection} onValueChange={(v) => setTargetDirection(v ?? 'below')}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="below">Falls to or below</SelectItem>
                  <SelectItem value="above">Breaks above</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wl-target">Price{item.currency ? ` (${item.currency})` : ''}</Label>
              <Input
                id="wl-target"
                value={targetPrice}
                onChange={(e) => setTargetPrice(e.target.value)}
                placeholder={item.price != null ? `now ${item.price.toFixed(2)}` : 'e.g. 150'}
                inputMode="decimal"
                className="font-mono"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>Conviction</Label>
              <Select value={conviction} onValueChange={(v) => setConviction(v ?? 'none')}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Not set</SelectItem>
                  <SelectItem value="low">Low</SelectItem>
                  <SelectItem value="medium">Medium</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between gap-2 self-end rounded-lg border border-border px-3 py-2">
              <Label htmlFor="wl-monitor" className="text-xs">
                Archie checks it
              </Label>
              <Switch id="wl-monitor" checked={monitor} onCheckedChange={setMonitor} />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
