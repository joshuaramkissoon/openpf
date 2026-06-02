import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api',
  timeout: 30000,
})

export interface InstrumentSearchRow {
  instrument_code: string | null
  ticker: string
  display_ticker?: string | null
  name: string
  currency: string | null
}

export interface InstrumentSignals {
  momentum_63d: number | null
  rsi_14: number | null
  trend_score: number | null
  volatility_30d: number | null
  risk_flag: string | null
  trend_direction: string | null
}

export interface InstrumentPosition {
  account_kind: string
  accounts: string[]
  quantity: number
  average_price: number
  current_price: number
  total_cost: number
  value: number
  ppl: number
  ppl_pct: number | null
  weight: number
}

export interface InstrumentWatchlistContext {
  id: string
  conviction: 'low' | 'medium' | 'high' | null
  status: string
  note: string
  target_price: number | null
  target_direction: 'above' | 'below' | null
  monitor: boolean
}

export interface InstrumentAlert {
  id: string
  created_at: string | null
  category: string
  severity: 'critical' | 'warning' | 'info' | string
  title: string
  detail: string
  consider: string | null
  ticker: string | null
  status: string
  source: string
}

export interface InstrumentThesisSummary {
  id: string
  title: string
  status: string
  confidence: number
  invalidation: string
}

export interface InstrumentDetail {
  ticker: string
  display_ticker?: string | null
  instrument_code: string | null
  name: string | null
  yfinance_ticker: string | null
  currency: string | null
  is_minor_unit: boolean
  price: number | null
  change_pct: number | null
  held: boolean
  position: InstrumentPosition | null
  signals: InstrumentSignals
  watchlist: InstrumentWatchlistContext | null
  alerts: InstrumentAlert[]
  theses: InstrumentThesisSummary[]
  target_price: number | null
  target_direction: 'above' | 'below' | null
  target_distance_pct: number | null
  display_currency: string
}

export async function searchInstruments(q: string, limit = 8): Promise<InstrumentSearchRow[]> {
  const { data } = await api.get<{ results: InstrumentSearchRow[] }>('/instruments/search', {
    params: { q, limit },
  })
  return data.results
}

export async function getInstrumentDetail(
  ticker: string,
  displayCurrency: 'GBP' | 'USD' = 'GBP'
): Promise<InstrumentDetail> {
  const { data } = await api.get<InstrumentDetail>(`/instruments/${encodeURIComponent(ticker)}/detail`, {
    params: { display_currency: displayCurrency },
  })
  return data
}
