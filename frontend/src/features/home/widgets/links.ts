import { useEffect, useState } from 'react'

import { installPreset, listViews, pinSpecView } from '@/api/views'
import type { VizSpec, WidgetPreset } from '@/components/viz'
import type { ViewMeta } from '@/types/views'

// ── every view's meta (title, icon, kind), cached; refreshed when views change ──
let metas: Promise<Map<string, ViewMeta>> | null = null
if (typeof window !== 'undefined') window.addEventListener('openpf:views-changed', () => (metas = null))

export function loadViewMetas(): Promise<Map<string, ViewMeta>> {
  if (!metas) {
    metas = listViews()
      .then((vs) => new Map(vs.map((v) => [v.id, v])))
      .catch(() => {
        metas = null
        return new Map<string, ViewMeta>()
      })
  }
  return metas
}

export function useViewMeta(id: string | null): { meta: ViewMeta | null; loading: boolean } {
  const [state, setState] = useState<{ id: string | null; meta: ViewMeta | null }>({ id: null, meta: null })
  useEffect(() => {
    if (!id) return
    let alive = true
    void loadViewMetas().then((m) => alive && setState({ id, meta: m.get(id) ?? null }))
    return () => {
      alive = false
    }
  }, [id])
  return { meta: state.id === id ? state.meta : null, loading: !!id && state.id !== id }
}

/**
 * Widget preset → the shipped view preset that tells its full story. A preset
 * with no matching view (Portfolio value) opens a view made from its own spec.
 */
export const PRESET_VIEW: Record<string, string> = {
  today: 'today',
  movers: 'today',
  'movers-map': 'today',
  winners: 'winners-losers',
  income: 'income',
  cash: 'cash-drag',
  isa: 'isa-allowance',
  sectors: 'risk',
}

/** The view page behind a preset widget, installed on first open (never duplicated). */
export async function presetViewId(p: WidgetPreset): Promise<string> {
  const views = [...(await loadViewMetas()).values()]
  const slug = PRESET_VIEW[p.id]
  const starter = slug ?? `widget:${p.id}`
  const hit = views.find((v) => v.starter === starter)
  if (hit) return hit.id
  const made = slug ? await installPreset(slug) : await pinSpecView(p.spec, p.title, starter)
  window.dispatchEvent(new CustomEvent('openpf:views-changed'))
  return made.id
}

const FIGURES = new Set(['hero', 'kpis', 'meter'])

/**
 * What a view widget shows. S and M: the headline block. L: the view's lead
 * composition — the headline figure plus its main chart — when it has both.
 */
export function leadSpec(spec: VizSpec, headline: number, size: 'S' | 'M' | 'L'): VizSpec {
  const first = spec.blocks[headline] ?? spec.blocks[0]
  if (size === 'L' && FIGURES.has(first.type)) {
    const chart = spec.blocks.slice(headline + 1).find((b) => b.type !== 'note' && !FIGURES.has(b.type))
    if (chart) return { ...spec, blocks: [first, chart] }
  }
  return { ...spec, blocks: [first] }
}
