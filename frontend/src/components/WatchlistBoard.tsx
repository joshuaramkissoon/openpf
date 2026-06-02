import { useCallback, useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  MoreHorizontal,
  Plus,
  Star,
  Target,
  User,
} from 'lucide-react'

import { SectionCard } from '@/components/kit'
import { StockChart } from '@/components/StockChart'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  createWatchlistItem,
  deleteWatchlistItem,
  getWatchlist,
  updateWatchlistItem,
} from '@/api/client'
import { formatMoney, formatSignedPercent } from '@/utils/format'
import type { WatchlistItem } from '@/types'

interface Props {
  onError: (message: string | null) => void
}

const SOURCE: Record<string, { label: string; Icon: typeof User }> = {
  manual: { label: 'You', Icon: User },
  archie: { label: 'Archie', Icon: Bot },
  agent_run: { label: 'Agent', Icon: Bot },
  watchlist_review: { label: 'Archie', Icon: Bot },
}

const CONVICTION_DOT: Record<string, string> = {
  high: 'bg-positive',
  medium: 'bg-[#dcb45c]',
  low: 'bg-muted-foreground',
}

const SEVERITY_CLASS: Record<string, string> = {
  critical: 'bg-negative/15 text-negative',
  warning: 'bg-[#dcb45c]/15 text-[#dcb45c]',
  info: 'bg-primary/15 text-primary',
}

function changeClass(pct: number | null): string {
  if (pct == null) return 'text-muted-foreground'
  if (pct > 0) return 'text-positive'
  if (pct < 0) return 'text-negative'
  return 'text-muted-foreground'
}

export function WatchlistBoard({ onError }: Props) {
  const [items, setItems] = useState<WatchlistItem[]>([])
  const [statusFilter, setStatusFilter] = useState<'watching' | 'acted' | 'archived' | 'all'>('watching')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [forecastOn, setForecastOn] = useState<Record<string, boolean>>({})
  const [editing, setEditing] = useState<WatchlistItem | null>(null)

  // Add bar
  const [newSymbol, setNewSymbol] = useState('')
  const [newNote, setNewNote] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
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
    void load()
  }, [load])

  const totalFlags = useMemo(() => items.reduce((n, i) => n + (i.open_flags || 0), 0), [items])

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

  async function remove(id: string) {
    setBusy(true)
    try {
      await deleteWatchlistItem(id)
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to remove item')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <SectionCard
        title="Watchlist"
        description={
          statusFilter === 'watching'
            ? `${items.length} tracked${totalFlags ? ` · ${totalFlags} flagged` : ''} — ideas Archie watches and resurfaces`
            : `${items.length} ${statusFilter}`
        }
        action={
          <Select value={statusFilter} onValueChange={(v) => v && setStatusFilter(v as typeof statusFilter)}>
            <SelectTrigger className="h-8 w-[130px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="watching">Watching</SelectItem>
              <SelectItem value="acted">Acted</SelectItem>
              <SelectItem value="archived">Archived</SelectItem>
              <SelectItem value="all">All</SelectItem>
            </SelectContent>
          </Select>
        }
      >
        {/* Add bar */}
        <div className="mb-4 flex flex-col gap-2 sm:flex-row">
          <Input
            value={newSymbol}
            onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === 'Enter' && void handleAdd()}
            placeholder="Symbol (e.g. KEYS)"
            className="sm:w-44 font-mono uppercase"
          />
          <Input
            value={newNote}
            onChange={(e) => setNewNote(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void handleAdd()}
            placeholder="Why watch it? (optional note)"
            className="flex-1"
          />
          <Button onClick={() => void handleAdd()} disabled={busy || !newSymbol.trim()} className="gap-1.5">
            <Plus className="size-4" /> Add
          </Button>
        </div>

        {loading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Loading…</p>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-center">
            <Star className="size-5 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {statusFilter === 'watching'
                ? 'Nothing on the watchlist yet. Add a symbol above, or ask Archie to track one.'
                : `No ${statusFilter} items.`}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-2.5">
            {items.map((item) => {
              const src = SOURCE[item.source] ?? SOURCE.manual
              const SrcIcon = src.Icon
              const expanded = expandedId === item.id
              return (
                <div
                  key={item.id}
                  className="rounded-lg border border-border/60 bg-muted/20 transition-colors hover:border-border"
                >
                  {/* Header row */}
                  <div className="flex items-start gap-3 p-3.5">
                    <button
                      onClick={() => setExpandedId(expanded ? null : item.id)}
                      className="mt-0.5 text-muted-foreground hover:text-foreground"
                      aria-label={expanded ? 'Collapse' : 'Expand chart'}
                    >
                      {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                    </button>

                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                        <span className="font-semibold">{item.symbol}</span>
                        {item.name ? (
                          <span className="truncate text-xs text-muted-foreground">{item.name}</span>
                        ) : null}
                        {item.price != null ? (
                          <span className="font-mono text-xs tabular-nums">
                            {formatMoney(item.price, item.currency || 'USD')}
                          </span>
                        ) : null}
                        {item.change_pct != null ? (
                          <span className={`font-mono text-xs tabular-nums ${changeClass(item.change_pct)}`}>
                            {formatSignedPercent(item.change_pct)}
                          </span>
                        ) : null}
                        {item.conviction ? (
                          <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                            <span className={`size-2 rounded-full ${CONVICTION_DOT[item.conviction] ?? 'bg-muted-foreground'}`} />
                            {item.conviction}
                          </span>
                        ) : null}
                        {item.target_price != null && item.target_direction ? (
                          <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                            <Target className="size-3" />
                            {item.target_direction} {item.target_price}
                          </span>
                        ) : null}
                        {!item.monitor ? (
                          <span className="flex items-center gap-1 text-[11px] text-muted-foreground" title="Monitoring muted">
                            <EyeOff className="size-3" /> muted
                          </span>
                        ) : null}
                        {item.open_flags > 0 ? (
                          <span
                            className={`flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${SEVERITY_CLASS[item.latest_severity ?? 'info']}`}
                            title={item.latest_flag ?? undefined}
                          >
                            <AlertTriangle className="size-3" />
                            {item.open_flags}
                          </span>
                        ) : null}
                      </div>

                      {item.note ? <p className="mt-1 text-sm text-muted-foreground">{item.note}</p> : null}
                      {item.open_flags > 0 && item.latest_flag ? (
                        <p className="mt-1 truncate text-xs text-foreground/80">↳ {item.latest_flag}</p>
                      ) : null}

                      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 text-[11px] text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <SrcIcon className="size-3" /> {src.label}
                          {item.created_at ? ` · ${dayjs(item.created_at).format('MMM D')}` : ''}
                        </span>
                        {item.last_reviewed_at ? (
                          <span>reviewed {dayjs(item.last_reviewed_at).format('MMM D')}</span>
                        ) : null}
                      </div>
                    </div>

                    <DropdownMenu>
                      <DropdownMenuTrigger className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
                        <MoreHorizontal className="size-4" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => setEditing(item)}>Edit…</DropdownMenuItem>
                        <DropdownMenuItem onClick={() => void patch(item.id, { monitor: !item.monitor })}>
                          {item.monitor ? 'Mute monitoring' : 'Resume monitoring'}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        {item.status !== 'acted' ? (
                          <DropdownMenuItem onClick={() => void patch(item.id, { status: 'acted' })}>
                            Mark as acted
                          </DropdownMenuItem>
                        ) : null}
                        {item.status !== 'archived' ? (
                          <DropdownMenuItem onClick={() => void patch(item.id, { status: 'archived' })}>
                            Archive
                          </DropdownMenuItem>
                        ) : (
                          <DropdownMenuItem onClick={() => void patch(item.id, { status: 'watching' })}>
                            Move back to watching
                          </DropdownMenuItem>
                        )}
                        <DropdownMenuItem
                          className="text-negative focus:text-negative"
                          onClick={() => void remove(item.id)}
                        >
                          Remove
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>

                  {/* Expanded chart + Kronos */}
                  {expanded ? (
                    <div className="border-t border-border/60 p-3.5">
                      <div className="mb-3 flex items-center justify-between rounded-lg border border-border/60 bg-card/40 px-3 py-2">
                        <div>
                          <Label htmlFor={`fc-${item.id}`} className="text-xs font-medium">Kronos forecast</Label>
                          <p className="text-[11px] text-muted-foreground">Probabilistic 30-day cone (p10–p50–p90)</p>
                        </div>
                        <Switch
                          id={`fc-${item.id}`}
                          checked={!!forecastOn[item.id]}
                          onCheckedChange={(v) => setForecastOn((m) => ({ ...m, [item.id]: v }))}
                        />
                      </div>
                      <StockChart
                        key={item.symbol}
                        ticker={item.symbol}
                        period="6mo"
                        interval="1d"
                        chartType="line"
                        indicators={['sma20', 'sma50']}
                        height={300}
                        forecast={!!forecastOn[item.id]}
                        forecastHorizon={30}
                        forecastSamples={12}
                        forecastLookback={180}
                      />
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        )}
      </SectionCard>

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
  const [targetDirection, setTargetDirection] = useState<string>(item.target_direction ?? 'none')
  const [monitor, setMonitor] = useState(item.monitor)

  function save() {
    const body: Parameters<typeof updateWatchlistItem>[1] = {
      note,
      conviction: conviction === 'none' ? null : (conviction as 'low' | 'medium' | 'high'),
      monitor,
    }
    const tp = parseFloat(targetPrice)
    body.target_price = targetPrice.trim() && !Number.isNaN(tp) ? tp : null
    body.target_direction = targetDirection === 'none' ? null : (targetDirection as 'above' | 'below')
    void onSave(body)
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit {item.symbol}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wl-note">Why watch it</Label>
            <Textarea
              id="wl-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="The reason — this is the condition Archie's review checks against."
              rows={3}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>Conviction</Label>
              <Select value={conviction} onValueChange={(v) => setConviction(v ?? 'none')}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">None</SelectItem>
                  <SelectItem value="low">Low</SelectItem>
                  <SelectItem value="medium">Medium</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between gap-2 rounded-lg border border-border/60 px-3">
              <Label htmlFor="wl-monitor" className="flex items-center gap-1.5">
                <Eye className="size-3.5" /> Monitor
              </Label>
              <Switch id="wl-monitor" checked={monitor} onCheckedChange={setMonitor} />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wl-target">Alert level</Label>
              <Input
                id="wl-target"
                value={targetPrice}
                onChange={(e) => setTargetPrice(e.target.value)}
                placeholder="e.g. 150"
                inputMode="decimal"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Direction</Label>
              <Select value={targetDirection} onValueChange={(v) => setTargetDirection(v ?? 'none')}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">—</SelectItem>
                  <SelectItem value="below">Falls below</SelectItem>
                  <SelectItem value="above">Rises above</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={busy}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
