import axios from 'axios'

import { withAuth } from './auth'

const api = withAuth(
  axios.create({
    baseURL: import.meta.env.VITE_API_BASE ?? '/api',
    timeout: 90000, // verifying a key can wait on the provider (IBKR Flex reports take a few seconds)
  }),
)

export type ProviderType = 'broker' | 'crypto' | 'aggregator'
export type ConnectionMode = 'read' | 'trade'

export interface ProviderField {
  key: string
  label: string
  secret: boolean
  placeholder?: string
  multiline?: boolean
  options?: [string, string][]
}

export interface Provider {
  id: string
  type: ProviderType
  label: string
  product: string
  modes: ConnectionMode[]
  availability: string
  summary: string
  trade_note?: string
  notes?: string
  key_url?: string
  managed_in?: string
  read_steps: string[]
  trade_steps?: string[]
  fields: ProviderField[]
  fees?: { maker: number; taker: number }
  quote?: string
  sandbox_values?: Record<string, string>
  /** Sandbox: the mock exchange's trade key, prefilled when "Read + trade" is picked. */
  sandbox_trade_values?: Record<string, string>
  /** The exact key boxes at the exchange, per mode: tick ("on"), leave unticked ("off") or optional. */
  permissions?: Partial<Record<ConnectionMode, KeyPermission[]>>
  /** How to turn an existing read-only key into a trade key at this exchange. */
  upgrade?: string | null
}

export interface KeyPermission {
  label: string
  state: "on" | "off" | "optional"
}

export interface ProviderCatalog {
  groups: { id: ProviderType; label: string }[]
  providers: Provider[]
  unavailable: { id: string; type: ProviderType; label: string; why: string }[]
  sandbox: boolean
}

export interface ConnectionAccount {
  account_kind: string
  name: string
  value_gbp: number | null
  currency: string | null
  last_snapshot: string | null
  institution?: string | null
  configured?: boolean
  enabled?: boolean
  trade_key?: boolean
}

export interface Connection {
  id: string
  provider: string
  type: ProviderType
  label: string
  detail: string
  mode: ConnectionMode
  capabilities: ConnectionMode[]
  can_trade_provider: boolean
  trade_note?: string | null
  managed_in?: string
  enabled: boolean
  status: { state: 'ok' | 'error' | 'pending'; message: string; last_sync: string | null; last_attempt?: string | null }
  accounts: ConnectionAccount[]
  sandbox: boolean
  removable: boolean
  link_url?: string | null
}

export interface ConnectionsResponse {
  connections: Connection[]
  sandbox: boolean
}

export interface AccountInfo {
  account_kind: string
  label: string
  short: string
  provider: string
  broker: string
  tradable: boolean
  read_only: boolean
  connection_id: string
}

export async function getProviders() {
  const { data } = await api.get<ProviderCatalog>('/connections/providers')
  return data
}

export async function getConnections() {
  const { data } = await api.get<ConnectionsResponse>('/connections')
  return data
}

export async function getAccountDirectory() {
  const { data } = await api.get<{ accounts: Record<string, AccountInfo> }>('/connections/accounts')
  return data.accounts
}

export async function addConnection(provider: string, mode: ConnectionMode, fields: Record<string, string>) {
  const { data } = await api.post<ConnectionsResponse & { id: string; link_url?: string | null }>('/connections', {
    provider,
    mode,
    fields,
  })
  return data
}

export async function reconnectConnection(id: string, mode: ConnectionMode, fields: Record<string, string>) {
  const { data } = await api.put<ConnectionsResponse & { id: string; link_url?: string | null }>(
    `/connections/${encodeURIComponent(id)}`,
    { mode, fields },
  )
  return data
}

export async function syncConnection(id: string) {
  const { data } = await api.post<ConnectionsResponse>(`/connections/${encodeURIComponent(id)}/sync`)
  return data
}

export async function snaptradeLink(id: string) {
  const { data } = await api.post<{ link_url: string }>(`/connections/${encodeURIComponent(id)}/link`)
  return data.link_url
}

export async function removeConnection(id: string) {
  const { data } = await api.delete<ConnectionsResponse>(`/connections/${encodeURIComponent(id)}`)
  return data
}
