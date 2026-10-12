import type { ApiError } from '@/api/client'
import { SCRAMBLE_MONEY_FACTOR } from '@/lib/privacy'
import type { PortfolioSnapshot, PositionItem } from '@/types'

export type AccountView = 'all' | 'invest' | 'stocks_isa'
export type DisplayCurrency = 'GBP' | 'USD'

export function parseApiError(error: unknown): string {
  const detail = (error as ApiError)?.response?.data?.detail as unknown
  // New endpoints return a typed envelope ({code, message, meta}); older ones a string.
  if (detail && typeof detail === 'object') {
    return (detail as { message?: string }).message || 'Request failed'
  }
  if (typeof detail === 'string' && detail) return detail
  return error instanceof Error ? error.message : 'Unexpected error'
}

export function aggregatePositionsByTicker(positions: PositionItem[], portfolioTotal: number): PositionItem[] {
  const riskRank: Record<string, number> = { ok: 0, oversold: 1, overbought: 1, warning: 2, critical: 3 }
  const grouped = new Map<
    string,
    PositionItem & {
      _qty_total: number
      _total_cost: number
      _mom_weighted: number
      _mom_base: number
      _rsi_weighted: number
      _rsi_base: number
      _trend_weighted: number
      _trend_base: number
      _accounts: Set<string>
    }
  >()

  for (const row of positions) {
    const rowTotalCost = Number.isFinite(row.total_cost) ? row.total_cost : Math.max(row.value - row.ppl, 0)
    const key = row.ticker
    const existing = grouped.get(key)
    if (!existing) {
      const valueBase = Math.max(row.value, 0)
      const next = {
        ...row,
        total_cost: rowTotalCost,
        account_kind: row.account_kind,
        weight: 0,
        _qty_total: row.quantity,
        _total_cost: rowTotalCost,
        _mom_weighted: (row.momentum_63d ?? 0) * valueBase,
        _mom_base: row.momentum_63d === null || row.momentum_63d === undefined ? 0 : valueBase,
        _rsi_weighted: (row.rsi_14 ?? 0) * valueBase,
        _rsi_base: row.rsi_14 === null || row.rsi_14 === undefined ? 0 : valueBase,
        _trend_weighted: (row.trend_score ?? 0) * valueBase,
        _trend_base: row.trend_score === null || row.trend_score === undefined ? 0 : valueBase,
        _accounts: new Set([row.account_kind]),
      }
      grouped.set(key, next)
      continue
    }

    const valueBase = Math.max(row.value, 0)
    existing.quantity += row.quantity
    existing.value += row.value
    existing.ppl += row.ppl
    existing.total_cost += rowTotalCost
    existing._qty_total += row.quantity
    existing._total_cost += rowTotalCost
    existing._accounts.add(row.account_kind)

    if (row.momentum_63d !== null && row.momentum_63d !== undefined) {
      existing._mom_weighted += row.momentum_63d * valueBase
      existing._mom_base += valueBase
    }
    if (row.rsi_14 !== null && row.rsi_14 !== undefined) {
      existing._rsi_weighted += row.rsi_14 * valueBase
      existing._rsi_base += valueBase
    }
    if (row.trend_score !== null && row.trend_score !== undefined) {
      existing._trend_weighted += row.trend_score * valueBase
      existing._trend_base += valueBase
    }

    const currentRank = riskRank[(existing.risk_flag || 'ok').toLowerCase()] ?? 0
    const incomingRank = riskRank[(row.risk_flag || 'ok').toLowerCase()] ?? 0
    if (incomingRank > currentRank) {
      existing.risk_flag = row.risk_flag
    }
  }

  const rows = Array.from(grouped.values()).map((row) => {
    const qty = row._qty_total
    const avgPrice = qty > 0 && row._total_cost > 0 ? row._total_cost / qty : row.average_price
    const currentPrice = qty > 0 ? row.value / qty : row.current_price
    const accountKind = row._accounts.size > 1 ? 'all' : Array.from(row._accounts)[0] || row.account_kind
    return {
      account_kind: accountKind,
      ticker: row.ticker,
      display_ticker: row.display_ticker,
      instrument_code: row.instrument_code,
      name: row.name,
      yfinance_ticker: row.yfinance_ticker,
      instrument_currency: row.instrument_currency,
      quantity: row.quantity,
      average_price: avgPrice,
      current_price: currentPrice,
      total_cost: row.total_cost,
      value: row.value,
      ppl: row.ppl,
      weight: portfolioTotal > 0 ? row.value / portfolioTotal : 0,
      momentum_63d: row._mom_base > 0 ? row._mom_weighted / row._mom_base : null,
      rsi_14: row._rsi_base > 0 ? row._rsi_weighted / row._rsi_base : null,
      trend_score: row._trend_base > 0 ? row._trend_weighted / row._trend_base : null,
      risk_flag: row.risk_flag,
    } satisfies PositionItem
  })

  return rows.sort((a, b) => b.value - a.value)
}

export function obfuscateSnapshot(snapshot: PortfolioSnapshot): PortfolioSnapshot {
  const quantityFactor = 1.11
  const priceFactor = 1.23
  const moneyFactor = SCRAMBLE_MONEY_FACTOR // == quantityFactor * priceFactor; shared so the chart matches

  const obfuscateAmount = (value: number) => (Number.isFinite(value) ? value * moneyFactor : value)
  const obfuscatePrice = (value: number) => (Number.isFinite(value) ? value * priceFactor : value)
  const obfuscateQty = (value: number) => (Number.isFinite(value) ? value * quantityFactor : value)

  return {
    ...snapshot,
    account: {
      ...snapshot.account,
      free_cash: obfuscateAmount(snapshot.account.free_cash),
      invested: obfuscateAmount(snapshot.account.invested),
      pie_cash: obfuscateAmount(snapshot.account.pie_cash),
      total: obfuscateAmount(snapshot.account.total),
      ppl: obfuscateAmount(snapshot.account.ppl),
    },
    accounts: snapshot.accounts.map((row) => ({
      ...row,
      free_cash: obfuscateAmount(row.free_cash),
      invested: obfuscateAmount(row.invested),
      pie_cash: obfuscateAmount(row.pie_cash),
      total: obfuscateAmount(row.total),
      ppl: obfuscateAmount(row.ppl),
    })),
    positions: snapshot.positions.map((row) => ({
      ...row,
      quantity: obfuscateQty(row.quantity),
      average_price: obfuscatePrice(row.average_price),
      current_price: obfuscatePrice(row.current_price),
      total_cost: obfuscateAmount(row.total_cost),
      value: obfuscateAmount(row.value),
      ppl: obfuscateAmount(row.ppl),
    })),
    metrics: {
      ...snapshot.metrics,
      total_value: obfuscateAmount(snapshot.metrics.total_value),
      free_cash: obfuscateAmount(snapshot.metrics.free_cash),
    },
  }
}
