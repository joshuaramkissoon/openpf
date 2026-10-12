import { useEffect, useSyncExternalStore } from 'react'

import { listViews } from '@/api/views'
import type { ViewMeta } from '@/types/views'

/**
 * Tiny shared store for the views list so the sidebar (ViewsNav) and the pages
 * stay in sync without prop drilling: any mutation calls `refreshViews()`.
 */
type Snapshot = { views: ViewMeta[]; loaded: boolean; error: boolean }

let snapshot: Snapshot = { views: [], loaded: false, error: false }
const listeners = new Set<() => void>()
let inflight: Promise<void> | null = null

function emit(next: Snapshot) {
  snapshot = next
  listeners.forEach((l) => l())
}

export function refreshViews(): Promise<void> {
  if (inflight) return inflight
  inflight = listViews()
    .then((views) => emit({ views, loaded: true, error: false }))
    .catch(() => emit({ ...snapshot, loaded: true, error: true }))
    .finally(() => {
      inflight = null
    })
  return inflight
}

/** Optimistically patch one view in the cached list (e.g. pin toggle). */
export function patchCachedView(id: string, patch: Partial<ViewMeta>) {
  emit({ ...snapshot, views: snapshot.views.map((v) => (v.id === id ? { ...v, ...patch } : v)) })
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useViews(): Snapshot {
  const snap = useSyncExternalStore(subscribe, () => snapshot)
  useEffect(() => {
    if (!snapshot.loaded) void refreshViews()
  }, [])
  return snap
}
