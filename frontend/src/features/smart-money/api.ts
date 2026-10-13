import axios from 'axios'

import { withAuth } from '@/api/auth'

const api = withAuth(
  axios.create({
    baseURL: import.meta.env.VITE_API_BASE ?? '/api',
    timeout: 30000,
  }),
)

export type SmartMoneyEvidence = {
  source: 'form4' | '13f' | 'congress'
  actor: string
  role: string
  side: 'buy' | 'sell' | ''
  kind: string
  size: string
  trade_date: string | null
  disclosed_date: string | null
  delay_days: number | null
  url: string | null
}

export type SmartMoneyDetection = {
  kind: string
  title: string
  score: number
  strength: 'weak' | 'moderate' | 'strong'
  caveats: string[]
  touches: string[]
}

export type SmartMoneySummary = {
  ticker: string
  /** e.g. "3 insiders bought in 30 days · held by 2 funds you follow · Congress: 1 buy, disclosed 38 days late" */
  line: string
  insiders: { buyers_30d: number; sellers_30d: number; plan_sales_30d: number }
  funds: { holders: { fund: string; weight: number | null; value: number | null; period: string | null }[]; changes: SmartMoneyEvidence[] }
  congress: { buys_90d: number; sells_90d: number; median_delay_days: number | null }
  recent: SmartMoneyEvidence[]
  detections: SmartMoneyDetection[]
  attribution: { label: string; url: string } | null
  sandbox: boolean
  refreshing: boolean
  instrument_code: string | null
}

export async function getInstrumentSmartMoney(ticker: string) {
  const { data } = await api.get<SmartMoneySummary>(`/instruments/${encodeURIComponent(ticker)}/smart-money`)
  return data
}
