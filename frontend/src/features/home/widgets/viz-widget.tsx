import { useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, CircleSlash, RefreshCw } from 'lucide-react'

import { getViewHtml } from '@/api/views'
import type { HomeWidgetSize } from '@/api/home'
import { VizProvider, validateVizSpec, type VizSpec } from '@/components/viz'
import { BlockView } from '@/components/viz/BlockView'
import { num, resolveMeasure, resolveRows, type EngineCtx } from '@/components/viz/engine'
import { useAppActions } from '@/lib/app-actions'
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
export function SlimLine({ title, text, tone = 'quiet', onRetry, href }: { title: string; text: string; tone?: 'quiet' | 'error'; onRetry?: () => void; href?: string }) {
  const { navigate } = useAppActions()
  return (
    <div data-slim className="flex min-h-11 items-center gap-2.5 rounded-xl border border-dashed border-border/80 px-3.5 py-2 text-sm [[data-editing]_&]:pr-28">
      <CircleSlash className="size-3.5 shrink-0 text-muted-foreground/70" aria-hidden />
      <span className="truncate font-medium text-foreground/80">{title}</span>
      <span className={cn('min-w-0 truncate text-xs', tone === 'error' ? 'text-negative' : 'text-muted-foreground')}>{text}</span>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="ml-auto inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={`Retry ${title}`}>
          <RefreshCw className="size-3.5" />
        </button>
      ) : href ? (
        <button type="button" onClick={() => navigate(href)} className="ml-auto shrink-0 text-xs text-muted-foreground hover:text-foreground">
          Open →
        </button>
      ) : null}
    </div>
  )
}

/**
 * One viz block as a Home tile: a quiet title row (linking back to its view
 * when there is one) over the block at its widget size. Empty or failing data
 * collapses to a slim line.
 */
export function VizTile({ spec: raw, size, title, href, live = true }: { spec: VizSpec; size: HomeWidgetSize; title: string; href?: string; live?: boolean }) {
  // Validation also fills the schema defaults (formats, limits) the renderer expects.
  const checked = useMemo(() => validateVizSpec(raw), [raw])
  if (!checked.ok) return <SlimLine title={title} text={`Couldn’t draw this: ${checked.error}`} tone="error" />
  return <ValidTile spec={checked.spec} size={size} title={title} href={href} live={live} />
}

function ValidTile({ spec, size, title, href, live }: { spec: VizSpec; size: HomeWidgetSize; title: string; href?: string; live: boolean }) {
  const { navigate } = useAppActions()
  const { currency } = useDisplayPrefs()
  const ctx = useMemo<EngineCtx>(() => ({ currency, account: 'all' }), [currency])
  const [nonce, setNonce] = useState(0)
  const status = useBlockStatus(spec, ctx, nonce)
  const block = spec.blocks[0]
  const labelled = block.type === 'hero' || block.type === 'meter'

  if (status.state === 'empty') return <SlimLine title={title} text={status.text} href={href} />
  if (status.state === 'error') return <SlimLine title={title} text={status.text} tone="error" onRetry={() => setNonce((n) => n + 1)} />

  return (
    <VizProvider size={size}>
      <div data-viz-tile className={cn('group/tile relative flex flex-col rounded-2xl border border-border/70 bg-card p-4', VIZ_MIN_H[size])} data-widget-size={size}>
        {labelled ? null : (
          <div className="mb-2.5 flex items-center gap-2 pr-6">
            <p className="min-w-0 truncate text-xs font-medium text-muted-foreground">{title}</p>
          </div>
        )}
        {href && live ? (
          <button
            type="button"
            onClick={() => navigate(href)}
            className="absolute right-2.5 top-2.5 inline-flex size-7 items-center justify-center rounded-md text-muted-foreground/70 opacity-0 transition-opacity hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover/tile:opacity-100 [@media(hover:none)]:opacity-100"
            aria-label={`Open ${title}`}
            title="Open the full view"
          >
            <ArrowUpRight className="size-4" />
          </button>
        ) : null}
        <div className="min-h-0 flex-1 overflow-hidden">
          {status.state === 'loading' ? (
            <div className="h-full min-h-20 animate-pulse rounded-lg bg-muted/50" />
          ) : (
            <BlockView spec={spec} block={{ ...block, title: undefined, subtitle: undefined }} ctx={ctx} chrome={false} />
          )}
        </div>
      </div>
    </VizProvider>
  )
}
