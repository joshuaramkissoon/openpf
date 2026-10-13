import axios from 'axios'

import { withAuth } from './auth'

const api = withAuth(
  axios.create({
    baseURL: import.meta.env.VITE_API_BASE ?? '/api',
    timeout: 30000,
  }),
)

export type CryptoExchangeId = 'kraken' | 'coinbase' | 'binance'

export interface CryptoExchange {
  id: CryptoExchangeId
  label: string
  product: string
  quote: string
  fees: { maker: number; taker: number }
  fee_note: string
  permission_check: 'probe' | 'api'
  uk: string
  key_url: string
  key_steps: string[]
  notes: string
}

export interface CryptoStatus {
  exchange: CryptoExchangeId | ''
  label: string
  product: string
  quote: string
  enabled: boolean
  configured: boolean
  connected: boolean
  key_hint: string
  verified: { can_view?: boolean | null; can_trade?: boolean | null; can_withdraw?: boolean | null; method?: string; at?: string } | null
  needs_verification: boolean
  fees: { maker: number; taker: number; source: string } | null
  sandbox: boolean
  max_crypto_pct_of_book: number
}

export interface CryptoRiskAsset {
  asset: string
  name: string
  volatility?: number | null
  max_drawdown_1y?: number | null
  correlation_to_book?: number | null
  value_gbp: number
}

export interface CryptoRisk {
  crypto_value_gbp: number
  book_gbp: number
  crypto_weight: number
  cap_pct: number
  assets: CryptoRiskAsset[]
  sleeve_volatility: number | null
  book_volatility_ex_crypto: number | null
  stress_50pct_gbp: number
  stress_50pct_of_book: number
}

export async function getCryptoExchanges() {
  const { data } = await api.get<CryptoExchange[]>('/crypto/exchanges')
  return data
}

export async function getCryptoStatus() {
  const { data } = await api.get<CryptoStatus>('/crypto/status')
  return data
}

export async function connectCrypto(exchange: CryptoExchangeId, apiKey: string, apiSecret: string) {
  const { data } = await api.put<CryptoStatus>('/crypto/connection', { exchange, api_key: apiKey, api_secret: apiSecret })
  return data
}

export async function disconnectCrypto() {
  const { data } = await api.delete<CryptoStatus>('/crypto/connection')
  return data
}

export async function getCryptoRisk() {
  const { data } = await api.get<CryptoRisk>('/crypto/risk')
  return data
}

export async function createStarterCryptoPlay(payload: {
  conviction: 'low' | 'medium' | 'high'
  include_eth: boolean
  weeks: number
  budget_gbp?: number | null
}) {
  const { data } = await api.post<{ id: string; title: string }>('/crypto/starter-play', payload, { timeout: 60000 })
  return data
}
