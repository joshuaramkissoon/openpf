import axios from 'axios'

import { withAuth } from './auth'

const api = withAuth(
  axios.create({
    baseURL: import.meta.env.VITE_API_BASE ?? '/api',
    timeout: 30000,
  }),
)

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

// ── Insight: the stock sheet's aggregated payload ───────────────────────────
// Every section is optional; `pending` names sections still loading server-side
// (re-request to pick them up from the cache).

export interface InsightQuote {
  price: number
  prev_close: number | null
  change: number | null
  change_pct: number | null
  as_of: string
  /** Major currency of `price` (GBP for pence-quoted LSE lines). */
  currency: string
  price_gbp: number | null
  fx_to_gbp: number | null
}

export interface InsightPosition {
  quantity: number
  value_gbp: number
  cost_gbp: number
  ppl_gbp: number
  ppl_pct: number | null
  weight: number
  /** Average cost in the quote's major currency (same axis as the chart). */
  avg_cost: number | null
  avg_cost_gbp: number | null
  accounts: string[]
  value_by_account: Record<string, number>
  last_price?: number
}

export interface InsightBook {
  total_gbp: number
  free_cash_gbp: number
  free_cash_by_account: Record<string, number>
  position_cap: number | null
  max_order_gbp: number | null
}

export interface InsightRule {
  text: string
  state: string | null
  /** fine | watch | triggered | judgement | unknown (null until evaluated). */
  status: string | null
  detail: string | null
}

export interface InsightPlay {
  id: string
  title: string
  status: string
  /** owner | bench | related */
  relation: string
  role: string | null
  rationale: string | null
  leg_target_weight: number | null
  target_weight_of_book: number | null
  thesis: string
  what_would_change_my_mind: string
  verdict: string | null
  reviewed_at: string | null
  review_notes: string | null
  rules: InsightRule[]
}

export interface InsightThesis {
  id: string
  title: string
  thesis: string
  invalidation: string
  catalysts: string[]
  confidence: number
  created_at: string | null
}

export interface InsightTrade {
  date: string
  side: 'buy' | 'sell'
  quantity: number
  price: number
  account_kind: string
}

export interface InsightChart {
  /** [date, open, high, low, close], daily, oldest first, in `currency`. */
  points: [string, number, number, number, number][]
  currency: string
  /** Factor from Yahoo's quoted unit to `currency` (0.01 for pence lines). */
  unit_multiplier?: number
  avg_cost: number | null
  trades: InsightTrade[]
}

export interface InsightRisk {
  vol_annual: number
  typical_daily_move: number
  market_vol_annual: number | null
  vol_vs_market: number | null
  beta: number | null
  max_drawdown_1y: number | null
  risk_share: number | null
  value_share: number | null
  portfolio_vol_annual: number | null
  moves_with: { ticker: string; label: string; corr: number }[]
  holdings_considered: number
}

export interface InsightEvents {
  next_earnings?: { date: string; days_away: number }
  ex_dividend?: { date: string; days_away: number; amount: number | null }
}

export interface InsightNews {
  headline: string
  source: string
  url: string
  published_at: string | null
  summary: string
}

export interface InsightFundamentals {
  sector: string | null
  industry: string | null
  market_cap: number | null
  pe_trailing: number | null
  pe_forward: number | null
  price_to_sales: number | null
  peg: number | null
  revenue_growth: number | null
  earnings_growth: number | null
  gross_margin: number | null
  operating_margin: number | null
  profit_margin: number | null
  dividend_yield: number | null
  summary: string | null
  pe_history: { now: number; min: number; median: number; max: number; since: string; observations: number; basis: string } | null
}

export interface InstrumentInsight {
  ticker: string
  display_ticker: string
  name: string | null
  instrument_code: string | null
  yfinance_ticker: string
  quote_currency: string
  quote: InsightQuote | null
  why_moved: { text: string; sources?: InsightNews[] } | null
  held: boolean
  position: InsightPosition | null
  book: InsightBook | null
  plays: InsightPlay[]
  theses: InsightThesis[]
  alerts: { id: string; severity: string; title: string; detail: string; consider: string | null }[]
  watchlist: { id: string; note: string; target_price: number | null } | null
  chart: InsightChart | null
  risk: InsightRisk | null
  events: InsightEvents | null
  news: InsightNews[]
  fundamentals: InsightFundamentals | null
  pending: string[]
}

export async function getInstrumentInsight(ticker: string, wait?: number): Promise<InstrumentInsight> {
  const { data } = await api.get<InstrumentInsight>(`/instruments/${encodeURIComponent(ticker)}/insight`, {
    params: wait != null ? { wait } : undefined,
  })
  return data
}

export interface SheetProposalResult {
  ok: boolean
  intent?: { id: string; symbol: string; side: string; quantity: number; estimated_notional: number }
  warnings?: string[]
  error?: string
  violations?: string[]
  note?: string
}

export async function proposeFromSheet(
  ticker: string,
  body: { side: 'buy' | 'sell'; account_kind: string; notional_gbp: number; rationale?: string },
): Promise<SheetProposalResult> {
  const { data } = await api.post<SheetProposalResult>(`/instruments/${encodeURIComponent(ticker)}/propose`, body)
  return data
}

// ── Manual trades: the user trades from the sheet (no proposal) ──────────────

export type TradeAmount = { side: 'buy' | 'sell'; account_kind: string; notional_gbp: number }

export interface TradeFees {
  commission_gbp: number
  fx_fee_rate: number
  fx_fee_gbp: number
}

export interface TradePreview {
  ok: boolean
  broker_mode: 'live' | 'paper' | 'demo' | 'sandbox'
  sizing?: { symbol: string; quantity: number; reference_price: number; currency: string; estimated_notional_gbp: number; price_gbp: number }
  fees?: TradeFees
  violations: string[]
  warnings: string[]
  error?: string
  caps?: { max_single_order_notional_gbp: number; max_daily_notional_gbp: number; executed_today_gbp: number }
}

export interface TradeResult {
  ok: boolean
  intent: { id: string; status: string; symbol: string; side: string; quantity: number; estimated_notional_gbp: number }
  fill_price: number | null
  broker_mode: string
  fees: TradeFees
}

/** Size, fees and every rail for the review step. Records nothing. */
export async function previewTrade(ticker: string, body: TradeAmount): Promise<TradePreview> {
  const { data } = await api.post<TradePreview>(`/instruments/${encodeURIComponent(ticker)}/trade/preview`, body)
  return data
}

/** Place the user's own order: approved as the user, executed through the single gate. */
export async function placeTrade(ticker: string, body: TradeAmount & { live_confirmed?: boolean }): Promise<TradeResult> {
  const { data } = await api.post<TradeResult>(
    `/instruments/${encodeURIComponent(ticker)}/trade`,
    { ...body, user_action: { kind: 'manual_trade', reviewed: true } },
    { timeout: 60000 },
  )
  return data
}
