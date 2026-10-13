/**
 * Turns a recipe's skill body (markdown written for Archie) into things a
 * person reads: a timeline of plain-English steps, and the shape of the report
 * it produces. The raw instructions stay one click away ("Show the instructions").
 */

/** Tool or memory file → what Archie is doing, said to the user. */
const VERBS: Record<string, string> = {
  get_account_summary: 'Checks your cash in both accounts',
  get_positions: 'Looks over your holdings',
  get_company_news: 'Reads the news on your holdings',
  get_market_news: 'Scans the wider market news',
  get_macro_snapshot: 'Checks rates, volatility and the pound',
  get_earnings: 'Checks who reports earnings soon',
  get_earnings_calendar: 'Checks the earnings calendar',
  list_plays: 'Reviews your plays',
  get_play: 'Opens each play’s plan',
  stage_play_tranche: 'Lines up the next tranche of a play',
  list_watchlist: 'Goes through your watchlist',
  update_watchlist_item: 'Keeps your watchlist notes current',
  flag_watchlist_item: 'Flags watchlist names worth a look',
  get_price_snapshot: 'Checks the latest prices',
  get_technical_snapshot: 'Looks at trend and momentum',
  get_price_history_rows: 'Pulls recent price history',
  compare_assets: 'Compares against the market',
  get_risk_metrics: 'Measures your risk',
  get_valuation: 'Checks valuations',
  get_fundamentals: 'Checks the fundamentals',
  get_transaction_history: 'Reviews your deposits and withdrawals',
  get_order_history: 'Reviews your recent trades',
  propose_trade: 'Proposes trades for you to approve',
  list_proposals: 'Checks proposals waiting for you',
  get_track_record: 'Checks how past ideas played out',
  'memory/preferences.md': 'Reads your preferences',
  'memory/goals.md': 'Reads your goals',
  'memory/constraints.md': 'Reads your limits',
  'memory/market_views.md': 'Reads your market views',
  'memory/decisions/': 'Looks back at past decisions',
}

/** Short nouns for a tool, for joining two verbs without repeating the subject. */
const NOUNS: Record<string, string> = {
  'memory/preferences.md': 'your preferences',
  'memory/goals.md': 'goals',
  'memory/constraints.md': 'limits',
  'memory/market_views.md': 'market views',
}

const IGNORE = new Set(['summary', 'none', 'legacy_task'])

export type HumanStep = { title: string; detail: string; tools: string[] }

const CODE_RE = /`([^`]+)`/g

function codesIn(text: string): string[] {
  return [...text.matchAll(CODE_RE)].map((m) => m[1].trim()).filter((c) => !IGNORE.has(c))
}

function plain(text: string): string {
  return text
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/(^|\s)_([^_]+)_/g, '$1$2')
    .replace(/\bthe user's\b/gi, 'your')
    .replace(/\bthe user (has|is)\b/gi, (_m, v: string) => (v.toLowerCase() === 'has' ? 'you have' : 'you are'))
    .replace(/\bthe user (\w+?)s\b/gi, 'you $1')
    .replace(/\bthe user\b/gi, 'you')
    .replace(/\btheir\b/g, 'your')
    .replace(/\s+/g, ' ')
    .trim()
}

const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1)

/** "Reads your preferences, goals and limits" / "Reads the news on your holdings and checks who reports earnings soon". */
function titleFor(codes: string[]): string {
  const known = [...new Set(codes)].filter((c) => VERBS[c])
  if (!known.length) return ''
  const memory = known.filter((c) => c.startsWith('memory/') && NOUNS[c])
  if (memory.length > 1) {
    const nouns = memory.map((c) => NOUNS[c])
    const rest = known.filter((c) => !memory.includes(c))
    const head = `Reads ${nouns.slice(0, -1).join(', ')} and ${nouns[nouns.length - 1]}`
    return rest.length ? `${head}, then ${lower(VERBS[rest[0]])}` : head
  }
  const [a, b] = known
  return b ? `${VERBS[a]} and ${lower(VERBS[b])}` : VERBS[a]
}

/** The sentences of a step worth showing under its title (no tool names). */
function detailFor(text: string, hasTitle: boolean): string {
  const sentences = text.split(/(?<=[.!?])\s+(?=[A-Z(])/)
  const out: string[] = []
  for (const s of sentences) {
    if (!s.includes('`')) {
      out.push(s)
      continue
    }
    // "Read `x` — note any cash buffer…": keep what follows the dash.
    const tail = s.split(/\s[—–-]\s|;\s/).slice(1).join(' — ')
    if (tail && !tail.includes('`')) out.push(tail.charAt(0).toUpperCase() + tail.slice(1))
  }
  let detail = plain(out.join(' '))
  if (!hasTitle) return detail
  if (detail.length > 180) detail = detail.slice(0, 177).replace(/\s+\S*$/, '') + '…'
  return detail
}

function section(body: string, name: RegExp): string[] {
  const lines = body.split('\n')
  const start = lines.findIndex((l) => /^#{2,3}\s/.test(l) && name.test(l.replace(/^#+\s*/, '')))
  if (start < 0) return []
  const out: string[] = []
  for (const l of lines.slice(start + 1)) {
    if (/^#{1,3}\s/.test(l)) break
    out.push(l)
  }
  return out
}

/** The "## Steps" list as a human timeline. */
export function humanSteps(body: string): HumanStep[] {
  const lines = section(body, /^(steps|how|method|process)\b/i)
  const items: { text: string; sub: string[] }[] = []
  for (const l of lines) {
    const top = l.match(/^\d+[.)]\s+(.*)$/)
    if (top) items.push({ text: top[1], sub: [] })
    else if (items.length && l.trim()) {
      if (/^\s+([a-z]|\d+|[-*])[.)]?\s/.test(l)) items[items.length - 1].sub.push(l.trim())
      else items[items.length - 1].text += ` ${l.trim()}`
    }
  }
  return items.map(({ text, sub }) => {
    const tools = [...codesIn(text), ...sub.flatMap(codesIn)]
    const title = titleFor(tools)
    // "Candidates, in priority order:" + lettered sub-steps → "…: a, then b, then c".
    const subs = sub.map((l) => l.match(/\*\*([^*]+)\*\*/)?.[1]).filter((x): x is string => !!x)
    if (title && /:\s*$/.test(text) && subs.length) {
      const lead = plain(text.replace(CODE_RE, '').replace(/:\s*$/, ''))
      const list = subs.map((s, i) => (i || lead ? lower(plain(s)) : plain(s)).replace(/[.—\s]+$/, ''))
      return { title, detail: `${lead && !lead.includes('`') ? `${lead}: ` : ''}${list.join(', then ')}.`, tools }
    }
    if (title) return { title, detail: detailFor(text, true), tools }
    const p = plain(text.replace(CODE_RE, '$1'))
    const [first, ...rest] = p.split(/(?<=[.!?])\s+/)
    return { title: first.replace(/[.:]$/, ''), detail: rest.join(' '), tools }
  })
}

export type OutputPart = { title: string; hint: string }

/** The "## Output" bullets: what the report will contain (for the sample). */
export function outputParts(body: string): OutputPart[] {
  return section(body, /^(output|format|what you get)\b/i)
    .map((l) => l.match(/^\s*[-*]\s+(.*)$/)?.[1])
    .filter((x): x is string => !!x)
    .map((t) => {
      const bold = t.match(/^\*\*([^*]+)\*\*\s*(?:\([^)]*\))?\s*[—–:-]?\s*(.*)$/)
      const title = plain(bold ? bold[1] : t.split(/[—:]/)[0])
      const hint = plain((bold ? bold[2] : t.split(/[—:]/).slice(1).join(' ')).replace(CODE_RE, '$1'))
      return { title, hint }
    })
    .filter((p) => !/^summary headline$/i.test(p.title) && p.title.length < 60)
}
