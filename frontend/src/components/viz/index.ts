/**
 * Viz — declarative, native chart blocks for agent-built views and chat.
 * Spec format: backend/app/views_sdk/VIZ_SPEC.md (validated server-side by
 * app/services/viz_spec.py).
 *
 *   <VizBlock spec={unknown} />          inline (chat ```viz fences), self-validating, "Pin as view"
 *   <ViewSpecRenderer spec={VizSpec} />  a full page (Views)
 *   validateVizSpec(x)                   { ok: true, spec } | { ok: false, error }
 */
import './viz.css'

export { VizBlock, WIDGET_CLASS } from './VizBlock'
export { WIDGET_PRESETS, type WidgetPreset } from './widgets'
export type { WidgetSize } from './shared'
export { ViewSpecRenderer, VizProvider } from './ViewSpecRenderer'
export { validateVizSpec } from './validate'
export { invalidateVizCache } from './engine'
export type { VizSpec, Block as VizBlockSpec } from './types'
