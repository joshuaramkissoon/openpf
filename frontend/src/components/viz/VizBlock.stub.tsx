/**
 * TEMPORARY STUB — the real `VizBlock` comes from the Views agent's viz
 * library (backend/app/views_sdk/VIZ_SPEC.md). This only exists so chat can
 * render ```viz blocks before that merge. Contract kept identical:
 *   <VizBlock spec={unknown} pinnable? compact? />
 *   validateVizSpec(x) → { ok: true, spec } | { ok: false, error }
 *   Pin: POST /api/views/specs { spec, title } → ViewMeta → /views/{id}
 */
import { useState } from 'react'
import { LayoutGrid, Loader2, Pin } from 'lucide-react'
import { toast } from 'sonner'

import { apiClient } from '@/api/client'
import { navigate } from '@/lib/router'

type VizSpec = { version: number; title: string; description?: string; blocks: { type: string; title?: string }[] }

export function validateVizSpec(x: unknown): { ok: true; spec: VizSpec } | { ok: false; error: string } {
  const s = x as Partial<VizSpec> | null
  if (!s || typeof s !== 'object') return { ok: false, error: 'Spec must be an object' }
  if (typeof s.title !== 'string' || !s.title) return { ok: false, error: 'Spec needs a title' }
  if (!Array.isArray(s.blocks) || !s.blocks.length) return { ok: false, error: 'Spec needs at least one block' }
  return { ok: true, spec: s as VizSpec }
}

export function VizBlock({ spec, pinnable = true }: { spec: unknown; pinnable?: boolean; compact?: boolean }) {
  const [pinning, setPinning] = useState(false)
  const v = validateVizSpec(spec)
  if (!v.ok) return <div className="rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground">Couldn't draw this chart: {v.error}</div>

  async function pin() {
    setPinning(true)
    try {
      const { data } = await apiClient.post<{ id: string }>('/views/specs', { spec, title: v.ok ? v.spec.title : undefined })
      toast.success('Pinned as a view')
      navigate(`/views/${data.id}`)
    } catch {
      toast('Pinning lands with the Views update', { description: 'POST /api/views/specs is not available on this build yet.' })
    } finally {
      setPinning(false)
    }
  }

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{v.spec.title}</p>
          {v.spec.description ? <p className="text-xs text-muted-foreground">{v.spec.description}</p> : null}
        </div>
        {pinnable ? (
          <button type="button" onClick={pin} disabled={pinning} className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:text-foreground">
            {pinning ? <Loader2 className="size-3.5 animate-spin" /> : <Pin className="size-3.5" />}
            Pin as view
          </button>
        ) : null}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
        {v.spec.blocks.map((b, i) => (
          <div key={i} className="flex h-20 flex-col justify-between rounded-lg bg-muted/40 p-2.5 text-xs text-muted-foreground">
            <LayoutGrid className="size-3.5" />
            <span className="truncate">{b.title || b.type}</span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground/70">Preview stub · the viz library renders this for real</p>
    </div>
  )
}
