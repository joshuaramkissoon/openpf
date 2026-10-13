import axios from 'axios'

import { withAuth } from '@/api/auth'

const api = withAuth(
  axios.create({
    baseURL: import.meta.env.VITE_API_BASE ?? '/api',
    timeout: 60000,
  }),
)

// ── track record ────────────────────────────────────────────────────────────

export type Horizon = '1d' | '1w' | '1m' | '3m' | 'close'

export interface Mark {
  date: string
  price: number
  ret: number
  dir_ret: number
  bench_ret?: number
  excess?: number
}

export interface Stats {
  n: number
  n_vs_benchmark: number
  hits: number
  hit_rate: number | null
  avg_return: number | null
  avg_excess: number | null
  caveat: string
}

export interface Pnl {
  n: number
  notional_gbp: number
  gain_gbp: number
  vs_benchmark_gbp: number
}

export interface OutcomeItem {
  intent_id: string
  proposed_at: string
  symbol: string
  side: 'buy' | 'sell'
  category: string
  category_label: string
  fate: 'taken' | 'rejected' | 'expired' | 'superseded' | 'failed' | 'open'
  approved_by: string | null
  permission_label: string | null
  notional_gbp: number
  ref_price: number | null
  currency: string | null
  ref_price_gbp: number | null
  benchmark: string
  state: 'unpriced' | 'tracking' | 'complete'
  note: string | null
  marks: Partial<Record<Horizon, Mark>>
  latest: (Mark & { horizon: Horizon }) | null
}

export interface CategoryStats extends Stats {
  category: string
  label: string
  proposals: number
  taken: number
}

export interface TrackRecord {
  benchmark: string
  counts: { proposals: number; resolved: number; open: number; taken: number; passed: number; auto: number; unpriced: number }
  headline: Stats & { horizon: Horizon; horizon_label: string }
  by_horizon: Record<string, Stats>
  comparison: { following_archie: Pnl; what_you_did: Pnl; passed_on: Pnl }
  categories: CategoryStats[]
  items: OutcomeItem[]
  method: string
}

export async function getTrackRecord() {
  const { data } = await api.get<TrackRecord>('/track-record')
  return data
}

export async function refreshTrackRecord() {
  const { data } = await api.post<TrackRecord>('/track-record/refresh', null, { params: { backfill: true } })
  return data
}

// ── permissions ─────────────────────────────────────────────────────────────

export type PermissionKind = 'play_tranche' | 'rebalance' | 'deploy_cash'

export interface Permission {
  id: string
  kind: PermissionKind
  kind_label: string
  kind_description: string
  label: string
  enabled: boolean
  per_trade_cap_gbp: number
  daily_cap_gbp: number
  spent_today_gbp: number
  expires_at: string | null
  play_ids: string[]
  created_at: string
  revoked_at: string | null
  confirmed_live_at: string | null
  applies: boolean
  status: string
}

export interface PermissionList {
  broker_mode: 'paper' | 'live'
  paused: boolean
  kinds: { kind: PermissionKind; label: string; description: string }[]
  permissions: Permission[]
}

export interface PermissionEvent {
  id: number
  created_at: string
  permission_id: string | null
  permission_label: string | null
  kind: string
  intent_id: string | null
  amount_gbp: number | null
  detail: Record<string, unknown>
}

export interface Digest {
  date: string
  count: number
  notional_gbp: number
  line: string
  groups: { permission_id: string; label: string; count: number; notional_gbp: number; symbols: string[] }[]
}

export interface PermissionSuggestion {
  id: string
  created_at: string
  kind: PermissionKind
  kind_label: string
  per_trade_cap_gbp: number
  daily_cap_gbp: number
  message: string
  note: string
  evidence: Record<string, unknown>
  status: string
  source: string
}

export interface PermissionDraft {
  kind: PermissionKind
  per_trade_cap_gbp: number
  daily_cap_gbp: number
  expires_at?: string | null
  play_ids?: string[]
  label?: string | null
  confirm_live?: boolean
  from_suggestion_id?: string | null
}

export async function listPermissions() {
  const { data } = await api.get<PermissionList>('/permissions')
  return data
}

export async function createPermission(body: PermissionDraft) {
  const { data } = await api.post<Permission>('/permissions', body)
  return data
}

export async function updatePermission(
  id: string,
  body: Partial<Omit<PermissionDraft, 'kind'>> & { enabled?: boolean; clear_expiry?: boolean },
) {
  const { data } = await api.patch<Permission>(`/permissions/${id}`, body)
  return data
}

export async function revokePermission(id: string) {
  const { data } = await api.post<Permission>(`/permissions/${id}/revoke`)
  return data
}

export async function confirmPermissionLive(id: string) {
  const { data } = await api.post<Permission>(`/permissions/${id}/confirm-live`)
  return data
}

export async function setKillSwitch(paused: boolean) {
  const { data } = await api.put<{ paused: boolean }>('/permissions/kill-switch', { paused })
  return data
}

export async function permissionHistory(id?: string) {
  const { data } = await api.get<PermissionEvent[]>(id ? `/permissions/${id}/history` : '/permissions/history')
  return data
}

export async function getDigest() {
  const { data } = await api.get<Digest>('/permissions/digest')
  return data
}

export async function listPermissionSuggestions() {
  const { data } = await api.get<PermissionSuggestion[]>('/permissions/suggestions')
  return data
}

export async function dismissPermissionSuggestion(id: string) {
  const { data } = await api.post<PermissionSuggestion>(`/permissions/suggestions/${id}/dismiss`)
  return data
}
