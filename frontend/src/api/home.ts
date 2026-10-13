import { toast } from 'sonner'

import { toastApiError } from '@/lib/api-error'
import { apiClient as api } from './client'

export type HomeWidgetSize = 'S' | 'M' | 'L'

/** One Home widget. Builtins are native cards; preset/view/spec render a viz block. */
export type HomeWidget =
  | { id: string; kind: 'builtin'; ref: 'book' | 'risk' | 'today' | 'permissions'; size: HomeWidgetSize }
  | { id: string; kind: 'preset'; ref: string; size: HomeWidgetSize }
  | { id: string; kind: 'view'; ref: string; block: number; size: HomeWidgetSize }
  | { id: string; kind: 'spec'; spec: import('@/components/viz').VizSpec; title?: string; size: HomeWidgetSize }

export type HomeLayout = { version: 1; widgets: HomeWidget[] }

/** A widget before it has an id (the server assigns one). */
export type NewHomeWidget = HomeWidget extends infer W ? (W extends HomeWidget ? Omit<W, 'id'> & { id?: string } : never) : never

export const HOME_CHANGED = 'openpf:home-changed'

export async function getHomeLayout() {
  const { data } = await api.get<HomeLayout>('/home/layout')
  return data
}

export async function putHomeLayout(layout: Pick<HomeLayout, 'widgets'>) {
  const { data } = await api.put<HomeLayout>('/home/layout', layout)
  return data
}

export async function resetHomeLayout() {
  const { data } = await api.post<HomeLayout>('/home/layout/reset')
  return data
}

export async function addHomeWidget(widget: NewHomeWidget) {
  const { data } = await api.post<{ layout: HomeLayout; widget: HomeWidget }>('/home/widgets', widget)
  return data
}

/** "Add to Home" from chat or a view: append, confirm with a toast that links Home. */
export async function addToHome(widget: NewHomeWidget, label: string, navigate: (path: string) => void) {
  try {
    const res = await addHomeWidget(widget)
    window.dispatchEvent(new CustomEvent(HOME_CHANGED))
    toast.success('Added to Home', { description: label, action: { label: 'Open Home', onClick: () => navigate('/home') } })
    return res
  } catch (err) {
    toastApiError(err, 'Could not add this to Home')
    return null
  }
}
