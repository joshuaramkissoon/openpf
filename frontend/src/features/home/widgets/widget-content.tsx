import type { HomeWidget, HomeWidgetSize } from '@/api/home'
import type { PortfolioState } from '@/app/hooks/use-portfolio'
import { AutonomyDigest } from '@/features/autonomy/home'
import { BookByPlay } from '@/features/plays'
import type { AppConfig } from '@/types'

import { RiskChips } from '../holdings-summary'
import { TodayCard } from '../today-card'
import { BUILTINS, presetById, presetSpec, type BuiltinId } from './registry'
import { SlimLine, VizTile, oneBlock, useViewSpec } from './viz-widget'

export type HomeCtx = { portfolio: PortfolioState; config: AppConfig | null }

/** Native cards sit in the same quiet tile as viz widgets. */
function BuiltinTile({ children }: { children: React.ReactNode }) {
  return <div className="h-full min-w-0 rounded-2xl border border-border/70 bg-card p-5">{children}</div>
}

export function BuiltinBody({ id, ctx, editing }: { id: BuiltinId; ctx: HomeCtx; editing: boolean }) {
  const { portfolio, config } = ctx
  switch (id) {
    case 'book':
      return (
        <BuiltinTile>
          <BookByPlay bump={portfolio.lastUpdate ? portfolio.lastUpdate.slice(0, 16) : null} />
        </BuiltinTile>
      )
    case 'risk':
      return (
        <BuiltinTile>
          <RiskChips snapshot={portfolio.snapshot} positions={portfolio.positions} config={config} />
        </BuiltinTile>
      )
    case 'today':
      return (
        <BuiltinTile>
          <TodayCard />
        </BuiltinTile>
      )
    case 'permissions':
      // One line on days Archie acted under a permission; otherwise nothing (or a hint while editing).
      return <AutonomyDigest fallback={editing ? <SlimLine title={BUILTINS.permissions.title} text="Nothing placed on its own today — shows up only when it does" /> : null} />
  }
}

/** A widget's display title (for headers, aria labels and toasts). */
export function useWidgetTitle(w: HomeWidget): string {
  const { spec } = useViewSpec(w.kind === 'view' ? w.ref : null)
  if (w.kind === 'builtin') return BUILTINS[w.ref]?.title ?? 'Widget'
  if (w.kind === 'preset') return presetById(w.ref)?.title ?? 'Widget'
  if (w.kind === 'view') return spec?.title ?? 'View'
  return w.title ?? w.spec.title
}

export function WidgetBody({ widget, size, ctx, editing }: { widget: HomeWidget; size: HomeWidgetSize; ctx: HomeCtx; editing: boolean }) {
  const view = useViewSpec(widget.kind === 'view' ? widget.ref : null)
  switch (widget.kind) {
    case 'builtin':
      return <BuiltinBody id={widget.ref} ctx={ctx} editing={editing} />
    case 'preset': {
      const p = presetById(widget.ref)
      if (!p) return <SlimLine title="Widget" text="This widget no longer exists — remove it in Edit Home" />
      return <VizTile spec={presetSpec(p, size)} size={size} title={p.title} live={!editing} />
    }
    case 'view': {
      if (view.loading) return <div className="h-[204px] animate-pulse rounded-2xl bg-muted/40" />
      if (!view.spec) return <SlimLine title="View" text="This view was deleted or isn’t a chart view" />
      return <VizTile spec={oneBlock(view.spec, widget.block)} size={size} title={view.spec.title} href={`/views/${widget.ref}`} live={!editing} />
    }
    case 'spec':
      return <VizTile spec={oneBlock(widget.spec, 0)} size={size} title={widget.title ?? widget.spec.title} live={!editing} />
  }
}
