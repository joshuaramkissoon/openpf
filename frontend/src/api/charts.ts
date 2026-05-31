import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api',
  timeout: 30000,
})

export interface CandleItem {
  time: string | number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface IndicatorPoint {
  time: string | number
  value: number
}

export interface MACDPoint {
  time: string | number
  macd: number
  signal: number
  histogram: number
}

export interface ChartData {
  ok: boolean
  ticker: string
  yfinance_ticker: string
  period: string
  interval: string
  candles: CandleItem[]
  overlays: Record<string, IndicatorPoint[]>
  panels: Record<string, IndicatorPoint[] | MACDPoint[]>
  markers: Array<{
    time: string | number
    position: string
    color: string
    shape: string
    text: string
  }>
}

export async function fetchChartData(params: {
  ticker: string
  period?: string
  interval?: string
  indicators?: string
}): Promise<ChartData> {
  const { data } = await api.get<ChartData>('/charts/candles', { params })
  return data
}

// ── Kronos forecast ────────────────────────────────────────────────────

export interface ForecastBandPoint {
  date: string
  p10: number
  p50: number
  p90: number
}

export interface ForecastHistoryPoint {
  date: string
  close: number
}

export interface ForecastSummary {
  median_terminal_close: number
  expected_return_pct: number
  prob_up: number
  terminal_spread_pct: number
}

export interface ForecastData {
  ok: boolean
  symbol: string
  yfinance_ticker: string
  model: string
  device: string | null
  generated_at: string
  horizon: number
  lookback: number
  samples: number
  last_close: number
  last_date: string
  summary: ForecastSummary
  history: ForecastHistoryPoint[]
  forecast: ForecastBandPoint[]
}

export async function fetchForecast(params: {
  ticker: string
  horizon?: number
  lookback?: number
  samples?: number
  temperature?: number
  top_p?: number
}): Promise<ForecastData> {
  const { data } = await api.get<ForecastData>('/charts/forecast', {
    params,
    // Forecasting can be slow on first call (weight download / CPU paths).
    timeout: 120000,
  })
  return data
}
