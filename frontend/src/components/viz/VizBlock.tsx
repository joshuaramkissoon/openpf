import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ArrowUpRight, Check, LayoutGrid, LoaderCircle, Pin } from 'lucide-react'

import { addToHome } from '@/api/home'
import { pinSpecView } from '@/api/views'
import { toastApiError } from '@/lib/api-error'
import { useAppActions } from '@/lib/app-actions'
import { useDisplayPrefs } from '@/lib/display-prefs'
import { cn } from '@/lib/utils'
import { BlockView } from './BlockView'
import type { WidgetSize } from './shared'
import { VizProvider } from './ViewSpecRenderer'
import { validateVizSpec } from './validate'

/** Fixed widget footprints (grid unit ≈ 180px): S 1×1, M 2×1, L 2×2. */
export const WIDGET_CLASS: Record<WidgetSize, string> = {
  S: 'h-[180px] w-[180px]',
  M: 'h-[180px] w-[376px]',
  L: 'h-[376px] w-[376px]',
}

/**
 * Inline viz for chat (```viz {spec}``` fences): validates the spec itself,
 * renders every block at chat width, and offers "Pin as view".
 */
export function VizBlock({ spec, pinnable = true, compact = true, size, fluid = false }: { spec: unknown; pinnable?: boolean; compact?: boolean; size?: WidgetSize; fluid?: boolean }) {
  const actions = useAppActions()
  const { currency } = useDisplayPrefs()
  const result = useMemo(() => validateVizSpec(spec), [spec])
  const [pinning, setPinning] = useState(false)
  const [pinned, setPinned] = useState<string | null>(null)
  const [homing, setHoming] = useState(false)
  const [homed, setHomed] = useState(false)

  if (size && result.ok) {
    // Widget: the spec's first block at a fixed size (S 1×1, M 2×1, L 2×2).
    const s = result.spec
    const block = s.blocks[0]
    return (
      <VizProvider size={size}>
        <div
          className={cn('flex flex-col overflow-hidden rounded-2xl border border-border bg-card p-4', fluid ? 'h-full w-full' : WIDGET_CLASS[size])}
          data-widget-size={size}
        >
          {block.type === 'hero' || block.type === 'meter' ? null : (
            <p className="mb-2 truncate text-xs font-medium text-muted-foreground">{block.title ?? s.title}</p>
          )}
          <div className="min-h-0 flex-1 overflow-hidden">
            <BlockView spec={s} block={{ ...block, title: undefined, subtitle: undefined }} ctx={{ currency, account: 'all' }} chrome={false} />
          </div>
        </div>
      </VizProvider>
    )
  }

  if (!result.ok) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-card/50 px-4 py-3 text-sm text-muted-foreground">
        Couldn’t draw this chart. <span className="text-xs">{result.error}</span>
      </div>
    )
  }
  const s = result.spec

  const pin = async () => {
    setPinning(true)
    try {
      const meta = await pinSpecView(s, s.title)
      setPinned(meta.id)
      toast.success('Pinned as a view', { description: meta.title, action: { label: 'Open', onClick: () => actions.navigate(`/views/${meta.id}`) } })
      window.dispatchEvent(new CustomEvent('openpf:views-changed'))
    } catch (err) {
      toastApiError(err, 'Could not pin this view')
    } finally {
      setPinning(false)
    }
  }

  const addHome = async () => {
    setHoming(true)
    const first = s.blocks[0]
    const res = await addToHome({ kind: 'spec', spec: s, title: s.title, size: first.type === 'hero' || first.type === 'meter' ? 'S' : 'M' }, s.title, actions.navigate)
    setHoming(false)
    if (res) {
      setHomed(true)
      // Home saved the chart as a view (so the widget links to a page): show it in Views.
      if (res.widget.kind === 'view') {
        setPinned(res.widget.ref)
        window.dispatchEvent(new CustomEvent('openpf:views-changed'))
      }
    }
  }

  return (
    <VizProvider compact={compact}>
      <figure className="not-prose my-2 overflow-hidden rounded-xl border border-border bg-card">
        <figcaption className="flex items-start justify-between gap-3 border-b border-border/70 px-4 py-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{s.title}</p>
            {s.description ? <p className="truncate text-xs text-muted-foreground">{s.description}</p> : null}
          </div>
          {pinnable ? (
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                onClick={homed ? () => actions.navigate('/home') : addHome}
                disabled={homing}
                className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-60"
                title={homed ? 'On your Home — open it' : 'Add this chart to Home as a widget'}
              >
                {homing ? <LoaderCircle className="size-3.5 animate-spin" /> : homed ? <Check className="size-3.5" /> : <LayoutGrid className="size-3.5" />}
                {homed ? 'On Home' : 'Add to Home'}
              </button>
              {pinned ? (
                <button
                  type="button"
                  onClick={() => actions.navigate(`/views/${pinned}`)}
                  className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-xs font-medium text-primary hover:bg-primary/10"
                >
                  Open view <ArrowUpRight className="size-3.5" />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={pin}
                  disabled={pinning}
                  className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-border px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-60"
                >
                  {pinning ? <LoaderCircle className="size-3.5 animate-spin" /> : <Pin className="size-3.5" />} Pin as view
                </button>
              )}
            </div>
          ) : null}
        </figcaption>
        <div className="space-y-5 p-4">
          {s.blocks.map((b, i) => (
            <BlockView key={i} spec={s} block={b} ctx={{ currency, account: 'all' }} chrome={false} />
          ))}
        </div>
      </figure>
    </VizProvider>
  )
}
