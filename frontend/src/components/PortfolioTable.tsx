import type { PositionItem } from '../types'
import { formatMoney } from '../utils/format'

function pct(v?: number | null, decimals = 1) {
  if (v === undefined || v === null || Number.isNaN(v)) return '-'
  return `${(v * 100).toFixed(decimals)}%`
}

interface Props {
  positions: PositionItem[]
  accountView: 'all' | 'invest' | 'stocks_isa'
  displayCurrency: 'GBP' | 'USD'
}

const MAX_VISIBLE_ROWS = 24

function accountTag(accountKind: string) {
  if (accountKind === 'stocks_isa') return 'ISA'
  if (accountKind === 'invest') return 'INVEST'
  return 'BOTH'
}

function formatPctChange(p: PositionItem) {
  const costBasis = Number.isFinite(p.total_cost) && p.total_cost > 0 ? p.total_cost : Math.max(p.value - p.ppl, 0)
  if (!Number.isFinite(costBasis) || Math.abs(costBasis) < 1e-9) {
    return '-'
  }
  return `${((p.ppl / costBasis) * 100).toFixed(1)}%`
}

function signalSummary(p: PositionItem) {
  const parts: string[] = []
  if (p.momentum_63d !== null && p.momentum_63d !== undefined) {
    parts.push(`3M ${(p.momentum_63d * 100).toFixed(1)}%`)
  }
  if (p.rsi_14 !== null && p.rsi_14 !== undefined) {
    parts.push(`RSI ${p.rsi_14.toFixed(1)}`)
  }
  if (p.trend_score !== null && p.trend_score !== undefined) {
    parts.push(`Trend ${p.trend_score.toFixed(2)}`)
  }
  return parts.join(' | ') || '-'
}

export function PortfolioTable({ positions, accountView, displayCurrency }: Props) {
  const currency = displayCurrency
  const visibleRows = positions.slice(0, MAX_VISIBLE_ROWS)
  const hiddenCount = Math.max(0, positions.length - visibleRows.length)
  return (
    <section className="glass-card table-card">
      <div className="section-heading-row">
        <h2>Position Intelligence</h2>
        <span className="hint">
          {accountView === 'all'
            ? `Aggregated by ticker across Invest + ISA${hiddenCount ? ` • showing top ${visibleRows.length}` : ''}`
            : `Focused view for selected account${hiddenCount ? ` • showing top ${visibleRows.length}` : ''}`}
        </span>
      </div>
      <div className="table-scroll position-table-scroll">
        <table>
          <thead>
            <tr>
              <th>Accounts</th>
              <th>Symbol</th>
              <th>Qty</th>
              <th>Invested</th>
              <th>Current Value</th>
              <th>Weight</th>
              <th>Profit</th>
              <th>Profit %</th>
              <th>Signal</th>
              <th>Risk</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((p, index) => (
              <tr key={`${p.account_kind}-${p.instrument_code}-${p.quantity}-${index}`}>
                <td>
                  <span className="pill ok">{accountTag(p.account_kind)}</span>
                </td>
                <td>
                  <span className="chip">{p.ticker}</span>
                </td>
                <td>{p.quantity.toFixed(3)}</td>
                <td>{formatMoney(Number.isFinite(p.total_cost) && p.total_cost > 0 ? p.total_cost : Math.max(p.value - p.ppl, 0), currency)}</td>
                <td>{formatMoney(p.value, currency)}</td>
                <td>{pct(p.weight)}</td>
                <td className={p.ppl >= 0 ? 'up' : 'down'}>{formatMoney(p.ppl, currency)}</td>
                <td className={p.ppl >= 0 ? 'up' : 'down'}>{formatPctChange(p)}</td>
                <td className="signal-cell">{signalSummary(p)}</td>
                <td>{p.risk_flag ? <span className={`pill ${p.risk_flag}`}>{p.risk_flag}</span> : <span className="pill ok">ok</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
