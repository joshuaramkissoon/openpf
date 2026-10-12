import { apiClient as api } from './client'
import type { ViewDocument, ViewJob, ViewMeta, ViewStarter } from '../types/views'

export async function listViews() {
  const { data } = await api.get<ViewMeta[]>('/views')
  return data
}

export async function getView(id: string) {
  const { data } = await api.get<ViewMeta>(`/views/${encodeURIComponent(id)}`)
  return data
}

export async function getViewHtml(id: string, version?: number) {
  const { data } = await api.get<ViewDocument>(`/views/${encodeURIComponent(id)}/html`, { params: version ? { version } : {} })
  return data
}

export async function generateView(payload: { prompt: string; view_id?: string; kind?: 'edit' | 'fix' }) {
  const { data } = await api.post<ViewJob>('/views/generate', payload)
  return data
}

export async function getViewJob(jobId: string) {
  const { data } = await api.get<ViewJob>(`/views/jobs/${encodeURIComponent(jobId)}`)
  return data
}

export async function patchView(id: string, patch: Partial<Pick<ViewMeta, 'title' | 'description' | 'icon' | 'pinned'>>) {
  const { data } = await api.patch<ViewMeta>(`/views/${encodeURIComponent(id)}`, patch)
  return data
}

export async function revertView(id: string, version: number) {
  const { data } = await api.post<ViewMeta>(`/views/${encodeURIComponent(id)}/revert`, { version })
  return data
}

export async function deleteView(id: string) {
  await api.delete(`/views/${encodeURIComponent(id)}`)
}

export async function reorderViews(ids: string[]) {
  const { data } = await api.post<ViewMeta[]>('/views/reorder', { ids })
  return data
}

export async function getViewState(id: string) {
  const { data } = await api.get<Record<string, unknown>>(`/views/${encodeURIComponent(id)}/state`)
  return data
}

export async function putViewState(id: string, state: Record<string, unknown>) {
  const { data } = await api.put<Record<string, unknown>>(`/views/${encodeURIComponent(id)}/state`, state)
  return data
}

/** Whitelisted read-only data for sandboxed views (the bridge's only data path). */
export async function getViewData(source: string, params: Record<string, unknown>) {
  const { data } = await api.post<unknown>('/views/data', { source, params }, { timeout: 60000 })
  return data
}

export async function listStarters() {
  const { data } = await api.get<ViewStarter[]>('/views/starters')
  return data
}

export async function installStarter(slug: string) {
  const { data } = await api.post<ViewMeta>(`/views/starters/${encodeURIComponent(slug)}/install`)
  return data
}
