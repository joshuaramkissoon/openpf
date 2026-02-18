import type { PortfolioSnapshot, PositionItem } from '../types'
import { formatMoney } from '../utils/format'

function metric(v: number, d = 2) {
  return Number.isFinite(v) ? v.toFixed(d) : '-'
}

interface Props {
  snapshot: PortfolioSnapshot
  positions: PositionItem[]
  accountView: 'all' | 'invest' | 'stocks_isa'
}

function accountLabel(kind: string) {
  if (kind === 'stocks_isa') return 'Stocks ISA'
  if (kind === 'invest') return 'Invest'
  return kind.toUpperCase()
}

export function MetricGrid({ snapshot, positions, accountView }: Props) {
  const { account, metrics } = snapshot
  const topWeight = positions.length > 0 ? Math.max(...positions.map((p) => p.weight)) : 0
  const concentration = positions.reduce((sum, row) => sum + row.weight * row.weight, 0)
  const accountRows = accountView === 'all' ? snapshot.accounts : snapshot.accounts.filter((row) => row.account_kind === accountView)

  const cards = [
    {
      label: 'Total Equity',
      value: formatMoney(account.total, account.currency),
      detail:
        accountRows.length > 1
          ? accountRows.map((row) => `${accountLabel(row.account_kind)} ${formatMoney(row.total, row.currency)}`)
          : null,
    },
    {
      label: 'Free Cash',
      value: formatMoney(account.free_cash, account.currency),
      detail:
        accountRows.length > 1
          ? accountRows.map((row) => `${accountLabel(row.account_kind)} ${formatMoney(row.free_cash, row.currency)}`)
          : null,
    },
    { label: 'P/L', value: formatMoney(account.ppl, account.currency) },
    { label: 'Cash Ratio', value: `${(metrics.cash_ratio * 100).toFixed(1)}%` },
    { label: 'Top Position Weight', value: `${(topWeight * 100).toFixed(1)}%` },
    { label: 'Concentration (HHI)', value: metric(concentration, 3) },
    { label: 'Estimated Beta', value: metric(metrics.estimated_beta, 2) },
    { label: 'Estimated Volatility', value: `${(metrics.estimated_volatility * 100).toFixed(1)}%` },
  ]

  return (
    <section className="metric-grid">
      {cards.map((card) => (
        <article key={card.label} className="glass-card metric-card">
          <span className="metric-label">{card.label}</span>
          <strong className="metric-value">{card.value}</strong>
          {card.detail && (
            <div className="metric-breakdown">
              {card.detail.map((line) => (
                <span key={`${card.label}-${line}`}>{line}</span>
              ))}
            </div>
          )}
        </article>
      ))}
    </section>
  )
}
