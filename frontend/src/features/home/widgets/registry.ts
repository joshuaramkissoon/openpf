import { WIDGET_PRESETS, type VizSpec, type WidgetPreset } from '@/components/viz'
import type { HomeWidget, HomeWidgetSize, NewHomeWidget } from '@/api/home'

export type BuiltinId = 'book' | 'risk' | 'today' | 'permissions'

export type BuiltinMeta = {
  id: BuiltinId
  title: string
  description: string
  sizes: HomeWidgetSize[]
  defaultSize: HomeWidgetSize
  /** Renders its own frame (the permissions strip already is one line). */
  frameless?: boolean
}

export const BUILTINS: Record<BuiltinId, BuiltinMeta> = {
  book: { id: 'book', title: 'Your book', description: 'Every holding by play, and what isn’t sorted yet', sizes: ['M', 'L'], defaultSize: 'M' },
  risk: { id: 'risk', title: 'Risk at a glance', description: 'Concentration, market sensitivity and idle cash in plain English', sizes: ['M', 'L'], defaultSize: 'M' },
  today: { id: 'today', title: 'Today’s briefing', description: 'The latest report’s headline, and when the next one lands', sizes: ['M', 'L'], defaultSize: 'M' },
  permissions: {
    id: 'permissions',
    title: 'Standing permissions',
    description: 'What Archie placed on its own today, with one-tap revoke. Hidden on quiet days',
    sizes: ['M', 'L'],
    defaultSize: 'M',
    frameless: true,
  },
}

export const SIZE_LABEL: Record<HomeWidgetSize, string> = { S: 'Small', M: 'Medium', L: 'Large' }

/** Grid footprint: 1 column on phones, 2 on tablets, 4 on wide screens. */
export const SPAN: Record<HomeWidgetSize, string> = {
  S: 'col-span-1',
  M: 'col-span-1 md:col-span-2',
  L: 'col-span-1 md:col-span-2 xl:col-span-4',
}

/** Viz tiles have fixed heights, like Apple widgets (native cards size to their content). */
export const VIZ_MIN_H: Record<HomeWidgetSize, string> = {
  S: 'h-[164px] md:h-[204px]',
  M: 'h-[204px]',
  L: 'h-[400px]',
}

export function presetById(id: string): WidgetPreset | undefined {
  return WIDGET_PRESETS.find((p) => p.id === id)
}

/** The spec a preset renders at a size (some have a glanceable small variant). */
export function presetSpec(p: WidgetPreset, size: HomeWidgetSize): VizSpec {
  return size === 'S' && p.small ? p.small : p.spec
}

/** Sizes a widget can take. Views and chat specs can be any size. */
export function sizesFor(w: Pick<HomeWidget, 'kind'> & { ref?: string }): HomeWidgetSize[] {
  if (w.kind === 'builtin') return BUILTINS[w.ref as BuiltinId]?.sizes ?? ['M', 'L']
  if (w.kind === 'preset') return presetById(w.ref ?? '')?.sizes ?? ['S', 'M', 'L']
  return ['S', 'M', 'L']
}

/** A stable identity so the gallery can tell what's already on Home. */
export function identity(w: NewHomeWidget | HomeWidget): string | null {
  if (w.kind === 'spec') return null
  if (w.kind === 'view') return `view:${w.ref}:${w.block ?? 0}`
  return `${w.kind}:${w.ref}`
}

/** View-preset slug (ranked server-side for this portfolio) → the widget presets it speaks to. */
export const SUGGESTION_MAP: Record<string, string[]> = {
  today: ['today', 'movers', 'movers-map'],
  'winners-losers': ['winners'],
  income: ['income'],
  'cash-drag': ['cash'],
  'isa-allowance': ['isa'],
  risk: ['sectors'],
}

/** The block a view contributes to Home: its first one that isn't prose. */
export function headlineBlock(spec: VizSpec): number {
  const i = spec.blocks.findIndex((b) => b.type !== 'note')
  return i < 0 ? 0 : i
}
