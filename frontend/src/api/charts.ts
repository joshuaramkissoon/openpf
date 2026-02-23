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
