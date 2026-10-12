import axios from 'axios'

import { withAuth } from '@/api/auth'
import type { DraftJob, DraftRequest, Play, PlayPatch, PlayPerformance, StageResult } from './types'

const api = withAuth(
  axios.create({
    baseURL: import.meta.env.VITE_API_BASE ?? '/api',
    timeout: 30000,
  }),
)

export async function listPlays(status = 'all') {
  const { data } = await api.get<Play[]>('/plays', { params: { status } })
  return data
}

export async function getPlay(id: string) {
  const { data } = await api.get<Play>(`/plays/${id}`)
  return data
}

export async function updatePlay(id: string, patch: PlayPatch) {
  const { data } = await api.patch<Play>(`/plays/${id}`, patch)
  return data
}

export async function deletePlay(id: string) {
  await api.delete(`/plays/${id}`)
}

export async function startDraft(payload: DraftRequest) {
  const { data } = await api.post<DraftJob>('/plays/draft', payload)
  return data
}

export async function getDraftJob(jobId: string) {
  const { data } = await api.get<DraftJob>(`/plays/draft/${jobId}`)
  return data
}

export async function stageTranche(id: string, trancheIndex?: number) {
  const { data } = await api.post<StageResult>(`/plays/${id}/stage`, { tranche_index: trancheIndex ?? null })
  return data
}

export async function getPerformance(id: string) {
  const { data } = await api.get<PlayPerformance>(`/plays/${id}/performance`, { timeout: 60000 })
  return data
}

export async function runReview(id: string) {
  const { data } = await api.post<Play>(`/plays/${id}/review`, null, { timeout: 120000 })
  return data
}
