import { useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, CircleSlash, RefreshCw } from 'lucide-react'

import { getViewHtml } from '@/api/views'
import type { HomeWidgetSize } from '@/api/home'
import { VizProvider, validateVizSpec, type VizSpec } from '@/components/viz'
import { BlockView } from '@/components/viz/BlockView'
import { num, resolveMeasure, resolveRows, type EngineCtx } from '@/components/viz/engine'
import { toastApiError } from '@/lib/api-error'
import { viewIcon } from '@/features/views/view-icons'
import type { ViewMeta } from '@/types/views'
import { useDisplayPrefs } from '@/lib/display-prefs'
import { cn } from '@/lib/utils'

import { VIZ_MIN_H } from './registry'

// ── saved views → spec (cached; refreshed when views change) ─────────────
const viewSpecs = new Map<string, Promise<VizSpec | null>>()
if (typeof window !== 'undefined') window.addEventListener('openpf:views-changed', () => viewSpecs.clear())

export function loadViewSpec(id: string): Promise<VizSpec | null> {
  let hit = viewSpecs.get(id)
  if (!hit) {
    hit = getViewHtml(id)
      .then((doc) => (doc.kind === 'spec' && doc.spec ? doc.spec : null))
      .catch(() => {
        viewSpecs.delete(id)
        return null
      })
    viewSpecs.set(id, hit)
  }
  return hit
}

export function useViewSpec(id: string | null): { spec: VizSpec | null; loading: boolean } {
  const [state, setState] = useState<{ id: string | null; spec: VizSpec | null }>({ id: null, spec: null })
  useEffect(() => {
    if (!id) return
    let alive = true
    void loadViewSpec(id).then((spec) => alive && setState({ id, spec }))
    return () => {
      alive = false
    }
  }, [id])
  return { spec: state.id === id ? state.spec : null, loading: !!id && state.id !== id }
}

/** A spec narrowed to one block (what a widget shows). */
export function oneBlock(spec: VizSpec, index: number): VizSpec {
  const block = spec.blocks[index] ?? spec.blocks[0]
  return { ...spec, blocks: [block] }
}

// ── "nothing to say" detection ─────────────────────────────────────────
type Status = { state: 'loading' } | { state: 'ready' } | { state: 'empty'; text: string } | { state: 'error'; text: string }

const EMPTY_TEXT: Partial<Record<string, string>> = {
  timeseries: 'Not enough history yet',
  bars: 'Nothing to show yet',
  allocation: 'Nothing to show yet',
  treemap: 'No holdings yet',
  table: 'Nothing to show yet',
}

/**
 * Resolves the block's data up front (the engine caches it, so the block's own
 * fetch is free) to decide whether the widget has anything to say.
 */
function useBlockStatus(spec: VizSpec, ctx: EngineCtx, nonce: number): Status {
  const [status, setStatus] = useState<Status>({ state: 'loading' })
  const block = spec.blocks[0]
  const key = `${JSON.stringify(spec)}|${ctx.currency}|${nonce}`
  useEffect(() => {
    let alive = true
    const check = async (): Promise<Status> => {
      const empty = block.empty ?? EMPTY_TEXT[block.type] ?? 'Nothing to show yet'
      if (block.type === 'hero' || block.type === 'meter') {
        const v = await resolveMeasure(spec, block.value, block.from, block.transform, ctx)
        return v == null ? { state: 'empty', text: empty } : { state: 'ready' }
      }
      if (block.type === 'note' || block.type === 'kpis' || block.type === 'multiples' || block.type === 'heatgrid') return { state: 'ready' }
      const rows = await resolveRows(spec, block.from, block.transform, ctx)
      if (block.type === 'timeseries') return rows.length < 2 ? { state: 'empty', text: empty } : { state: 'ready' }
      if (block.type === 'bars' && rows.every((r) => !num(r[block.value]))) return { state: 'empty', text: empty }
      return rows.length ? { state: 'ready' } : { state: 'empty', text: empty }
    }
    setStatus({ state: 'loading' })
    check()
      .then((s) => alive && setStatus(s))
      .catch((e: unknown) => alive && setStatus({ state: 'error', text: e instanceof Error ? e.message : 'Couldn’t load this' }))
    return () => {
      alive = false
    }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  return status
}

/** A widget with nothing to say: one quiet line, not an empty box. */
export function SlimLine({ title, text, tone = 'quiet', onRetry, onOpen }: { title: string; text: string; tone?: 'quiet' | 'error'; onRetry?: () => void; onOpen?: () => void | Promise<void> }) {
  return (
    <div data-slim className="flex min-h-11 items-center gap-2.5 rounded-xl border border-dashed border-border/80 px-3.5 py-2 text-sm [[data-editing]_&]:pr-28">
      <CircleSlash className="size-3.5 shrink-0 text-muted-foreground/70" aria-hidden />
      <span className="truncate font-medium text-foreground/80">{title}</span>
      <span className={cn('min-w-0 truncate text-xs', tone === 'error' ? 'text-negative' : 'text-muted-foreground')}>{text}</span>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="ml-auto inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={`Retry ${title}`}>
          <RefreshCw className="size-3.5" />
        </button>
      ) : onOpen ? (
        <button type="button" onClick={() => void onOpen()} className="ml-auto shrink-0 text-xs text-muted-foreground hover:text-foreground">
          Open →
        </button>
      ) : null}
    </div>
  )
}

/**
 * Opening a widget's view: the whole tile is the link outside edit mode (a
 * click inside an interactive mark — a bar, a tile, a button — keeps its own
 * meaning), with a visible ↗ on hover and on touch screens.
 */
function useTileLink(title: string, onOpen?: () => void | Promise<void>) {
  const [busy, setBusy] = useState(false)
  const open = async () => {
    if (!onOpen || busy) return
    setBusy(true)
    try {
      await onOpen()
    } catch (err) {
      toastApiError(err, 'Couldn’t open this view')
    } finally {
      setBusy(false)
    }
  }
  const onClick = (e: React.MouseEvent) => {
    if (!onOpen) return
    if ((e.target as HTMLElement).closest('button, a, input, [role="button"], [data-drill]')) return
    if (window.getSelection()?.toString()) return
    void open()
  }
  const button = onOpen ? (
    <button
      type="button"
      onClick={() => void open()}
      className="absolute right-2.5 top-2.5 z-10 inline-flex size-7 items-center justify-center rounded-md text-muted-foreground/70 opacity-0 transition-opacity hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover/tile:opacity-100 [@media(hover:none)]:opacity-100"
      aria-label={`Open ${title}`}
      title="Open the full view"
    >
      {busy ? <RefreshCw className="size-3.5 animate-spin" /> : <ArrowUpRight className="size-4" />}
    </button>
  ) : null
  const tileClass = onOpen ? 'cursor-pointer transition-[border-color,box-shadow] hover:border-foreground/20 hover:shadow-sm' : ''
  return { onClick, button, tileClass }
}

/**
 * A view as a Home tile: a quiet title row over its block(s) at the widget
 * size. At L a view's lead composition (headline figure + main chart) sits side
 * by side when the tile is wide enough. `onOpen` makes the tile a link to the
 * view. Empty or failing data collapses to a slim line.
 */
export function VizTile({ spec: raw, size, title, onOpen, live = true }: { spec: VizSpec; size: HomeWidgetSize; title: string; onOpen?: () => void | Promise<void>; live?: boolean }) {
  // Validation also fills the schema defaults (formats, limits) the renderer expects.
  const checked = useMemo(() => validateVizSpec(raw), [raw])
  if (!checked.ok) return <SlimLine title={title} text={`Couldn’t draw this: ${checked.error}`} tone="error" />
  return <ValidTile spec={checked.spec} size={size} title={title} onOpen={live ? onOpen : undefined} />
}

function ValidTile({ spec, size, title, onOpen }: { spec: VizSpec; size: HomeWidgetSize; title: string; onOpen?: () => void | Promise<void> }) {
  const { currency } = useDisplayPrefs()
  const ctx = useMemo<EngineCtx>(() => ({ currency, account: 'all' }), [currency])
  const [nonce, setNonce] = useState(0)
  const status = useBlockStatus(spec, ctx, nonce)
  const [box, setBox] = useState<HTMLDivElement | null>(null)
  const wide = useBoxWidth(box) >= 640
  const link = useTileLink(title, onOpen)
  const block = spec.blocks[0]
  const pair = spec.blocks.length > 1 && wide ? spec.blocks[1] : null
  const labelled = !pair && (block.type === 'hero' || block.type === 'meter')

  if (status.state === 'empty') return <SlimLine title={title} text={status.text} onOpen={onOpen} />
  if (status.state === 'error') return <SlimLine title={title} text={status.text} tone="error" onRetry={() => setNonce((n) => n + 1)} />

  return (
    <VizProvider size={size}>
      <div
        ref={setBox}
        data-viz-tile
        onClick={link.onClick}
        className={cn('group/tile relative flex flex-col rounded-2xl border border-border/70 bg-card p-4', VIZ_MIN_H[size], link.tileClass)}
        data-widget-size={size}
      >
        {labelled ? null : (
          <div className="mb-2.5 flex items-center gap-2 pr-6">
            <p className="min-w-0 truncate text-xs font-medium text-muted-foreground">{title}</p>
          </div>
        )}
        {link.button}
        <div className="min-h-0 flex-1 overflow-hidden">
          {status.state === 'loading' ? (
            <div className="h-full min-h-20 animate-pulse rounded-lg bg-muted/50" />
          ) : pair ? (
            <div className="grid h-full grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-8">
              <BlockView spec={spec} block={{ ...block, title: undefined, subtitle: undefined }} ctx={ctx} chrome={false} />
              <BlockView spec={spec} block={{ ...pair, title: undefined, subtitle: undefined }} ctx={ctx} chrome={false} />
            </div>
          ) : (
            <BlockView spec={spec} block={{ ...block, title: undefined, subtitle: undefined }} ctx={ctx} chrome={false} />
          )}
        </div>
      </div>
    </VizProvider>
  )
}

function useBoxWidth(el: HTMLElement | null) {
  const [w, setW] = useState(0)
  useEffect(() => {
    if (!el) return
    setW(el.clientWidth)
    const ro = new ResizeObserver(() => setW(el.clientWidth))
    ro.observe(el)
    return () => ro.disconnect()
  }, [el])
  return w
}

/**
 * A view that's custom code (not a chart spec) as a widget: it can't be drawn
 * small, so the tile says what it is and opens it.
 */
export function ViewCardTile({ meta, size, onOpen, live = true }: { meta: ViewMeta; size: HomeWidgetSize; onOpen?: () => void | Promise<void>; live?: boolean }) {
  const link = useTileLink(meta.title, live ? onOpen : undefined)
  const Icon = viewIcon(meta.icon)
  const big = size === 'L'
  return (
    <div
      onClick={link.onClick}
      className={cn('group/tile relative flex flex-col overflow-hidden rounded-2xl border border-border/70 bg-card p-4', VIZ_MIN_H[size], link.tileClass)}
      data-widget-size={size}
    >
      {/* A large, faint mark of the view's icon gives the card a face without pretending to be its content. */}
      <Icon aria-hidden className={cn('pointer-events-none absolute text-foreground/[0.04]', big ? '-right-8 -bottom-10 size-72' : '-right-5 -bottom-6 size-36')} strokeWidth={1.25} />
      <span className="inline-flex size-9 items-center justify-center rounded-xl bg-muted text-foreground/80">
        <Icon className="size-[18px]" aria-hidden />
      </span>
      <p className={cn('mt-3 pr-6 font-semibold tracking-tight', big ? 'text-xl' : 'line-clamp-2 text-[15px] leading-snug')}>{meta.title}</p>
      {meta.description && size !== 'S' ? (
        <p className={cn('mt-1 text-muted-foreground', big ? 'max-w-[60ch] text-sm leading-relaxed' : 'line-clamp-2 text-xs')}>{meta.description}</p>
      ) : null}
      <p className="mt-auto pt-3 text-xs font-medium text-muted-foreground transition-colors group-hover/tile:text-foreground">
        {size === 'S' ? 'Open →' : 'Open view →'}
      </p>
      {link.button}
    </div>
  )
}
