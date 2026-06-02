import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api',
  timeout: 30000,
})

// ── Client-side response cache ──────────────────────────────────────────────
// Chart specs re-render on every chat open / remount, and StockChart fetches on
// mount — so without a cache, re-opening a chat means a fresh candles + forecast
// round-trip (and loading spinners) every single time. Cache results in-memory
// for the session and coalesce concurrent identical requests, so re-opening a
// chat (or two charts of the same ticker) is instant. TTLs mirror the server:
// candles ~5min, forecast ~30min. Only successful results are cached; a failed
// request clears its in-flight slot so the next mount retries.
const CANDLE_TTL_MS = 5 * 60_000
const FORECAST_TTL_MS = 30 * 60_000

interface CacheEntry<T> {
  ts: number
  value: T
}
const _cache = new Map<string, CacheEntry<unknown>>()
const _inflight = new Map<string, Promise<unknown>>()

async function cached<T>(key: string, ttlMs: number, fetcher: () => Promise<T>): Promise<T> {
  const hit = _cache.get(key)
  if (hit && Date.now() - hit.ts <= ttlMs) {
    return hit.value as T
  }
  const pending = _inflight.get(key)
  if (pending) {
    return pending as Promise<T>
  }
  const promise = fetcher()
    .then((value) => {
      _cache.set(key, { ts: Date.now(), value })
      return value
    })
    .finally(() => {
      _inflight.delete(key)
    })
  _inflight.set(key, promise)
  return promise as Promise<T>
}

/** Drop all cached chart/forecast data (e.g. for a manual "refresh" control). */
export function clearChartCache(): void {
  _cache.clear()
  _inflight.clear()
}

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
  // Normalise indicator order so 'sma20,sma50' and 'sma50,sma20' share an
  // entry — matches the backend's order-independent key.
  const indicatorKey = (params.indicators ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .sort()
    .join(',')
  const key = `candles:${params.ticker}|${params.period ?? ''}|${params.interval ?? ''}|${indicatorKey}`
  return cached(key, CANDLE_TTL_MS, async () => {
    const { data } = await api.get<ChartData>('/charts/candles', { params })
    return data
  })
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
  const key = `forecast:${params.ticker}|${params.horizon ?? ''}|${params.lookback ?? ''}|${params.samples ?? ''}|${params.temperature ?? ''}|${params.top_p ?? ''}`
  return cached(key, FORECAST_TTL_MS, async () => {
    const { data } = await api.get<ForecastData>('/charts/forecast', {
      params,
      // Forecasting can be slow on first call (weight download / CPU paths).
      timeout: 120000,
    })
    return data
  })
}
