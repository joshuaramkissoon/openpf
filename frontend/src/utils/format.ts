export function currencySymbol(code: string): string {
  const normalized = (code || '').toUpperCase()
  if (normalized === 'GBP') return '£'
  if (normalized === 'USD') return '$'
  if (normalized === 'EUR') return '€'
  return `${normalized} `
}

export function formatMoney(value: number, currency: string, decimals = 2): string {
  const num = Number.isFinite(value) ? value : 0
  const sign = num < 0 ? '-' : ''
  const abs = Math.abs(num)
  const symbol = currencySymbol(currency)
  return `${sign}${symbol}${abs.toLocaleString(undefined, {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  })}`
}

/** Compact money, e.g. £223.1k / $1.2M — for headline tiles. */
export function formatCompactMoney(value: number, currency: string, decimals = 1): string {
  const num = Number.isFinite(value) ? value : 0
  const sign = num < 0 ? '-' : ''
  const abs = Math.abs(num)
  const symbol = currencySymbol(currency)
  if (abs < 1000) {
    return `${sign}${symbol}${abs.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
  }
  return `${sign}${symbol}${abs.toLocaleString(undefined, { notation: 'compact', maximumFractionDigits: decimals })}`
}

/** Money with an explicit +/- sign (gains/losses). */
export function formatSignedMoney(value: number, currency: string, decimals = 2): string {
  const num = Number.isFinite(value) ? value : 0
  return `${num >= 0 ? '+' : ''}${formatMoney(num, currency, decimals)}`
}

/** A fraction (0.173) -> "17.3%". */
export function formatPercent(fraction?: number | null, decimals = 1): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return '—'
  return `${(fraction * 100).toFixed(decimals)}%`
}

export function formatSignedPercent(fraction?: number | null, decimals = 1): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return '—'
  return `${fraction >= 0 ? '+' : ''}${(fraction * 100).toFixed(decimals)}%`
}

export function formatNumber(value?: number | null, decimals = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toLocaleString(undefined, { maximumFractionDigits: decimals, minimumFractionDigits: 0 })
}

export function accountLabel(kind: string): string {
  if (kind === 'stocks_isa') return 'Stocks ISA'
  if (kind === 'invest') return 'Invest'
  if (kind === 'all') return 'All Accounts'
  return kind.toUpperCase()
}

export function accountTag(kind: string): string {
  if (kind === 'stocks_isa') return 'ISA'
  if (kind === 'invest') return 'INVEST'
  return 'BOTH'
}
