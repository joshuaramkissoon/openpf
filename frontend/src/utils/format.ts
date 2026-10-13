import { accountInfo, isCryptoAccount } from '@/lib/accounts'


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

// The crypto account is labelled by its exchange ("Kraken"); set once the
// connection status loads (see lib/crypto-status).
let cryptoAccountLabel = 'Crypto'

export function setCryptoAccountLabel(label: string) {
  cryptoAccountLabel = label || 'Crypto'
}

export function accountLabel(kind: string): string {
  if (kind === 'stocks_isa') return 'Stocks ISA'
  if (kind === 'invest') return 'Invest'
  if (kind === 'crypto') return accountInfo(kind)?.short ?? cryptoAccountLabel
  if (kind === 'all') return 'All Accounts'
  return accountInfo(kind)?.short ?? kind.toUpperCase()
}

export function accountTag(kind: string): string {
  if (kind === 'stocks_isa') return 'ISA'
  if (kind === 'invest') return 'INVEST'
  if (kind === 'crypto') return (accountInfo(kind)?.short ?? cryptoAccountLabel).toUpperCase()
  const info = accountInfo(kind)
  if (info) return info.short.toUpperCase()
  return 'BOTH'
}

/** Coins need more decimals than shares (0.0018 BTC, not 0.00). */
export function formatQuantity(quantity: number, accountKind?: string | null): string {
  if (!Number.isFinite(quantity)) return '—'
  if (isCryptoAccount(accountKind)) return (+quantity.toPrecision(4)).toLocaleString(undefined, { maximumFractionDigits: 8 })
  return quantity.toFixed(2)
}
