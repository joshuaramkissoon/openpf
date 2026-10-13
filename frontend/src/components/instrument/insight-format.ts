/** Formatting for the instrument sheet. Proportional text everywhere; numbers
 *  get tabular figures via the `num` class, never a monospace face. */

export const num = "tabular-nums"

const SYMBOL: Record<string, string> = { GBP: "£", USD: "$", EUR: "€", CHF: "CHF ", CAD: "C$", AUD: "A$", JPY: "¥" }

export function sym(ccy: string | null | undefined): string {
  const c = (ccy || "").toUpperCase()
  return SYMBOL[c] ?? (c ? `${c} ` : "")
}

/** A price: 2dp normally, more for sub-unit prices. */
export function fmtPrice(v: number | null | undefined, ccy: string | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—"
  const dp = Math.abs(v) < 1 ? 4 : 2
  const s = Math.abs(v).toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp })
  return `${v < 0 ? "−" : ""}${sym(ccy)}${s}`
}

/** Money, compact above 10k (£10.6k), whole pounds above 1k. */
export function fmtMoney(v: number | null | undefined, ccy = "GBP", { signed = false } = {}): string {
  if (v == null || !Number.isFinite(v)) return "—"
  const a = Math.abs(v)
  let body: string
  if (a >= 1_000_000) body = `${(a / 1_000_000).toFixed(a >= 10_000_000 ? 0 : 1)}m`
  else if (a >= 10_000) body = `${(a / 1000).toFixed(1)}k`
  else if (a >= 1000) body = a.toLocaleString("en-GB", { maximumFractionDigits: 0 })
  else body = a.toLocaleString("en-GB", { minimumFractionDigits: a < 100 ? 2 : 0, maximumFractionDigits: a < 100 ? 2 : 0 })
  const sign = v < 0 ? "−" : signed ? "+" : ""
  return `${sign}${sym(ccy)}${body}`
}

export function fmtPct(v: number | null | undefined, dp = 1, { signed = false } = {}): string {
  if (v == null || !Number.isFinite(v)) return "—"
  const s = Math.abs(v * 100).toFixed(dp)
  const sign = v < 0 ? "−" : signed ? "+" : ""
  return `${sign}${s}%`
}

export function fmtBig(v: number | null | undefined, ccy: string): string {
  if (v == null || !Number.isFinite(v)) return "—"
  const a = Math.abs(v)
  if (a >= 1e12) return `${sym(ccy)}${(a / 1e12).toFixed(2)}tn`
  if (a >= 1e9) return `${sym(ccy)}${(a / 1e9).toFixed(1)}bn`
  if (a >= 1e6) return `${sym(ccy)}${(a / 1e6).toFixed(0)}m`
  return fmtMoney(v, ccy)
}

export function fmtDate(iso: string | null | undefined, withYear = false): string {
  if (!iso) return ""
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", ...(withYear ? { year: "numeric" } : {}) })
}

export function relDays(days: number): string {
  if (days <= 0) return "today"
  if (days === 1) return "tomorrow"
  if (days < 14) return `in ${days} days`
  if (days < 60) return `in ${Math.round(days / 7)} weeks`
  return `in ${Math.round(days / 30)} months`
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return ""
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return ""
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000))
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 48) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}
