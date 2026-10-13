import axios from 'axios'

import { withAuth } from '@/api/auth'

import type {
  Book,
  BookGroup,
  DraftJob,
  DraftRequest,
  Play,
  PlayAccount,
  PlayPatch,
  PlayPerformance,
  StageResult,
  Suggestions,
} from './types'

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

export async function retryDraft(jobId: string) {
  const { data } = await api.post<DraftJob>(`/plays/draft/${jobId}/retry`)
  return data
}

// ── The book by play ─────────────────────────────────────────────────────────

export async function getBook() {
  const { data } = await api.get<Book>('/plays/book', { timeout: 60000 })
  return data
}

export async function getRings(id: string) {
  const { data } = await api.get<BookGroup & { total_gbp: number }>(`/plays/${id}/rings`, { timeout: 60000 })
  return data
}

/** owner: a play id, 'core' or 'unsorted'. Omit account to move it in every account. */
export async function assignOwner(ticker: string, owner: string, accountKind?: PlayAccount) {
  const { data } = await api.post('/plays/owners', { ticker, owner, account_kind: accountKind ?? null }, { timeout: 60000 })
  return data
}

export async function addRelated(id: string, ticker: string, why: string) {
  const { data } = await api.post<Play>(`/plays/${id}/related`, { ticker, why }, { timeout: 60000 })
  return data
}

export async function removeRelated(id: string, ticker: string) {
  const { data } = await api.delete<Play>(`/plays/${id}/related/${encodeURIComponent(ticker)}`)
  return data
}

export async function addBench(id: string, ticker: string, why: string) {
  const { data } = await api.post<Play>(`/plays/${id}/bench`, { ticker, why }, { timeout: 60000 })
  return data
}

export async function removeBench(id: string, ticker: string) {
  const { data } = await api.delete<Play>(`/plays/${id}/bench/${encodeURIComponent(ticker)}`)
  return data
}

export async function promoteBench(id: string, ticker: string, weight = 0.1) {
  const { data } = await api.post<Play>(`/plays/${id}/bench/${encodeURIComponent(ticker)}/promote`, { weight }, { timeout: 60000 })
  return data
}

export async function getSuggestions() {
  const { data } = await api.get<Suggestions>('/plays/suggestions', { timeout: 60000 })
  return data
}

export async function generateSuggestions(useModel = true) {
  const { data } = await api.post<Suggestions>('/plays/suggestions', { use_model: useModel }, { timeout: 180000 })
  return data
}

export async function acceptSuggestion(id: string, edits: { title?: string; tickers?: string[] } = {}) {
  const { data } = await api.post<{ ok: boolean; kind: string; play_id: string | null; tickers: string[] }>(
    `/plays/suggestions/${id}/accept`,
    edits,
    { timeout: 60000 },
  )
  return data
}

export async function dismissSuggestion(id: string) {
  await api.post(`/plays/suggestions/${id}/dismiss`)
}
