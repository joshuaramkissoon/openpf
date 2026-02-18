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
