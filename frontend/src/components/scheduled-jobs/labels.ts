/** Expansions for known abbreviations used in scheduled-task names. */
const ACRONYMS: Record<string, string> = {
  lev: 'Leveraged',
  eod: 'EOD',
  pnl: 'P&L',
  isa: 'ISA',
  etp: 'ETP',
  pf: 'Portfolio',
}

/**
 * Turn a snake_case task identifier into a human-friendly label.
 * e.g. "lev_midday_check" → "Leveraged Midday Check", "weekly_pnl_snapshot" → "Weekly P&L Snapshot".
 */
export function humanizeTaskName(name: string): string {
  const words = name.split(/[_\s]+/).filter(Boolean)
  if (words.length === 0) return name
  return words
    .map((w) => {
      const lower = w.toLowerCase()
      if (ACRONYMS[lower]) return ACRONYMS[lower]
      return lower.charAt(0).toUpperCase() + lower.slice(1)
    })
    .join(' ')
}

/** Friendly sentence-case label for a report type: "weekly_review" → "Weekly review". */
export function reportTypeLabel(taskName?: string | null, fallback = 'Report'): string {
  if (!taskName) return fallback
  const words = humanizeTaskName(taskName).split(' ')
  return words.map((w, i) => (i === 0 || /^[A-Z&]{2,}$/.test(w) ? w : w.toLowerCase())).join(' ')
}
