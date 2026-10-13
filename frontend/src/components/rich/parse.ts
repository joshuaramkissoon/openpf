/**
 * A small, streaming-tolerant markdown parser for Archie's replies and
 * reports. It produces a flat block list that the rich renderer turns into
 * typography, data tables, callouts, viz blocks and proposal cards.
 *
 * Streaming rules (the text may be cut anywhere):
 * - an unterminated ``` fence becomes a code block with `closed: false`
 *   (viz fences render a skeleton until the closing fence arrives);
 * - a table whose divider row hasn't arrived yet is shown as a header-only
 *   table instead of flashing as a paragraph;
 * - a dangling `**` / `` ` `` at the very end is dropped so half-open markers
 *   never flash as literal asterisks.
 */

export type Align = 'left' | 'right' | 'center' | null

export type ListItem = { text: string; children: ListItem[]; ordered: boolean }

export type Block =
  | { kind: 'heading'; level: number; text: string; id: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'list'; ordered: boolean; start: number; items: ListItem[] }
  | { kind: 'table'; header: string[]; align: Align[]; rows: string[][]; pending: boolean }
  | { kind: 'code'; lang: string; body: string; closed: boolean }
  | { kind: 'quote'; text: string; tone: CalloutTone | null }
  | { kind: 'proposal'; id: string; label: string }
  | { kind: 'hr' }

export type CalloutTone = 'insight' | 'risk' | 'note'

const FENCE = /^(\s*)(```+|~~~+)\s*([\w-]*)\s*(.*)$/
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/
const BULLET = /^(\s*)[-*+]\s+(.*)$/
const ORDERED = /^(\s*)(\d+)[.)]\s+(.*)$/
const HR = /^\s*([-*_])(\s*\1){2,}\s*$/
/** A paragraph made only of proposal links: `[Buy NVDA £500](proposal:abc)`. */
const PROPOSAL_ONLY = /^\s*\[([^\]]+)\]\((?:proposal:|#?\/activity\/proposals\?id=)([\w-]+)\)\s*$/

export function slugify(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/[*_`[\]()]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60) || 'section'
  )
}

function isTableDivider(line: string): boolean {
  const t = line.trim()
  return t.includes('-') && t.includes('|') ? /^\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?$/.test(t) : false
}

function looksLikeTableRow(line: string): boolean {
  const t = line.trim()
  return t.startsWith('|') || (t.includes('|') && t.split('|').length >= 3)
}

export function splitRow(line: string): string[] {
  let t = line.trim()
  if (t.startsWith('|')) t = t.slice(1)
  if (t.endsWith('|') && !t.endsWith('\\|')) t = t.slice(0, -1)
  const cells: string[] = []
  let cur = ''
  for (let i = 0; i < t.length; i++) {
    const ch = t[i]
    if (ch === '\\' && t[i + 1] === '|') {
      cur += '|'
      i++
    } else if (ch === '|') {
      cells.push(cur.trim())
      cur = ''
    } else cur += ch
  }
  cells.push(cur.trim())
  return cells
}

function parseAlign(divider: string): Align[] {
  return splitRow(divider).map((c) => {
    const l = c.startsWith(':')
    const r = c.endsWith(':')
    return l && r ? 'center' : r ? 'right' : l ? 'left' : null
  })
}

const QUOTE_TONES: Record<string, CalloutTone> = {
  NOTE: 'note',
  INFO: 'note',
  TIP: 'insight',
  IMPORTANT: 'insight',
  WARNING: 'risk',
  CAUTION: 'risk',
  RISK: 'risk',
}

/** Remove a dangling, unmatched marker at the very end of streamed text. */
export function trimDangling(text: string): string {
  let out = text
  const bold = out.match(/\*\*/g)?.length ?? 0
  if (bold % 2 === 1) {
    const at = out.lastIndexOf('**')
    out = out.slice(0, at) + out.slice(at + 2)
  }
  const ticks = out.match(/`/g)?.length ?? 0
  if (ticks % 2 === 1) {
    const at = out.lastIndexOf('`')
    out = out.slice(0, at) + out.slice(at + 1)
  }
  // An unfinished link: "[label](htt" → "label"
  out = out.replace(/\[([^\]]*)\]\([^)]*$/, '$1').replace(/\[([^\]]*)$/, '$1')
  return out
}

function parseList(rows: string[], start: number): { block: Block; next: number } {
  const first = rows[start]
  const ordered = ORDERED.test(first) && !BULLET.test(first)
  const firstNum = ordered ? Number(first.match(ORDERED)?.[2] ?? 1) : 1
  const baseIndent = (first.match(/^(\s*)/)?.[1] ?? '').length
  const items: ListItem[] = []
  let i = start
  while (i < rows.length) {
    const line = rows[i]
    if (!line.trim()) {
      // A blank line continues the list only if the next line is another item.
      const nxt = rows[i + 1]
      if (nxt !== undefined && (BULLET.test(nxt) || ORDERED.test(nxt))) {
        i++
        continue
      }
      break
    }
    const b = line.match(BULLET)
    const o = line.match(ORDERED)
    const m = b ? { indent: b[1].length, text: b[2], ordered: false } : o ? { indent: o[1].length, text: o[3], ordered: true } : null
    if (!m) {
      // Lazy continuation line of the previous item.
      const last = items[items.length - 1]
      if (last && !HEADING.test(line) && !FENCE.test(line) && !looksLikeTableRow(line) && !line.trim().startsWith('>')) {
        const target = last.children.length ? last.children[last.children.length - 1] : last
        target.text += ' ' + line.trim()
        i++
        continue
      }
      break
    }
    if (m.indent > baseIndent + 1 && items.length) {
      items[items.length - 1].children.push({ text: m.text, children: [], ordered: m.ordered })
    } else {
      items.push({ text: m.text, children: [], ordered: m.ordered })
    }
    i++
  }
  return { block: { kind: 'list', ordered, start: firstNum, items }, next: i }
}

export function parseMarkdown(input: string, opts: { streaming?: boolean } = {}): Block[] {
  const streaming = Boolean(opts.streaming)
  const md = input.replace(/\r\n?/g, '\n').replace(/^---\n[\s\S]*?\n---\n?/, '')
  const rows = md.split('\n')
  const blocks: Block[] = []
  const usedIds = new Map<string, number>()
  let i = 0

  while (i < rows.length) {
    const raw = rows[i]
    const line = raw.trim()
    if (!line) {
      i++
      continue
    }

    const fence = raw.match(FENCE)
    // Streaming: the fence line itself is still arriving ("```vi") — its
    // language isn't known yet, so render nothing rather than flash a code block.
    if (fence && streaming && i === rows.length - 1) break
    if (fence) {
      const marker = fence[2]
      const lang = (fence[3] || '').toLowerCase()
      i++
      const body: string[] = []
      let closed = false
      while (i < rows.length) {
        if (rows[i].trim().startsWith(marker) && rows[i].trim().replace(/[`~]/g, '') === '') {
          closed = true
          i++
          break
        }
        body.push(rows[i])
        i++
      }
      // A finished (non-streaming) message with a missing closing fence is
      // still a complete block — only streaming text is "not yet closed".
      blocks.push({ kind: 'code', lang, body: body.join('\n'), closed: closed || !streaming })
      continue
    }

    if (HR.test(raw)) {
      blocks.push({ kind: 'hr' })
      i++
      continue
    }

    const heading = line.match(HEADING)
    if (heading) {
      const text = heading[2]
      const base = slugify(text)
      const n = usedIds.get(base) ?? 0
      usedIds.set(base, n + 1)
      blocks.push({ kind: 'heading', level: heading[1].length, text, id: n ? `${base}-${n}` : base })
      i++
      continue
    }

    if (looksLikeTableRow(raw) && i + 1 < rows.length && isTableDivider(rows[i + 1])) {
      const header = splitRow(raw)
      const align = parseAlign(rows[i + 1])
      i += 2
      const body: string[][] = []
      while (i < rows.length && rows[i].trim() && looksLikeTableRow(rows[i])) {
        body.push(splitRow(rows[i]))
        i++
      }
      blocks.push({ kind: 'table', header, align, rows: body, pending: false })
      continue
    }

    // Streaming: a table header (and maybe a half-typed divider) at the tail.
    if (streaming && line.startsWith('|')) {
      const rest = rows.slice(i + 1).filter((r) => r.trim())
      if (rest.length === 0 || (rest.length === 1 && /^[\s|:-]*$/.test(rest[0]))) {
        blocks.push({ kind: 'table', header: splitRow(raw), align: [], rows: [], pending: true })
        break
      }
    }

    if (line.startsWith('>')) {
      const quote: string[] = []
      while (i < rows.length && rows[i].trim().startsWith('>')) {
        quote.push(rows[i].trim().replace(/^>\s?/, ''))
        i++
      }
      let tone: CalloutTone | null = null
      const tag = quote[0]?.match(/^\[!(\w+)\]\s*(.*)$/)
      if (tag) {
        tone = QUOTE_TONES[tag[1].toUpperCase()] ?? 'note'
        quote[0] = tag[2]
      }
      blocks.push({ kind: 'quote', text: quote.filter((q, idx) => q || idx > 0).join('\n'), tone })
      continue
    }

    if (BULLET.test(raw) || ORDERED.test(raw)) {
      const { block, next } = parseList(rows, i)
      blocks.push(block)
      i = next
      continue
    }

    const para: string[] = []
    while (i < rows.length) {
      const nxt = rows[i]
      const t = nxt.trim()
      if (
        !t ||
        HEADING.test(t) ||
        FENCE.test(nxt) ||
        t.startsWith('>') ||
        BULLET.test(nxt) ||
        ORDERED.test(nxt) ||
        HR.test(nxt) ||
        (looksLikeTableRow(nxt) && i + 1 < rows.length && isTableDivider(rows[i + 1])) ||
        (streaming && para.length > 0 && t.startsWith('|'))
      ) {
        break
      }
      para.push(t)
      i++
    }
    if (!para.length) {
      // Defensive: never loop forever on an unexpected line.
      para.push(line)
      i++
    }
    const text = para.join('\n')
    const proposalLines = para.map((p) => p.match(PROPOSAL_ONLY))
    if (proposalLines.every(Boolean)) {
      for (const m of proposalLines) blocks.push({ kind: 'proposal', id: m![2], label: m![1] })
    } else {
      blocks.push({ kind: 'paragraph', text })
    }
  }

  if (streaming && blocks.length) {
    const last = blocks[blocks.length - 1]
    if (last.kind === 'paragraph') last.text = trimDangling(last.text)
    else if (last.kind === 'list') {
      const item = last.items[last.items.length - 1]
      const target = item.children.length ? item.children[item.children.length - 1] : item
      target.text = trimDangling(target.text)
    } else if (last.kind === 'heading') last.text = trimDangling(last.text)
  }
  return blocks
}

/* ── Report structure ─────────────────────────────────────────────────────── */

export type Section = { heading: Extract<Block, { kind: 'heading' }>; blocks: Block[] }

export type KeyNumber = { label: string; value: string }

export type ReportDoc = {
  title: string | null
  tldr: Block[]
  keyNumbers: KeyNumber[]
  preamble: Block[]
  sections: Section[]
}

const TLDR_RE = /^(tl;?\s?dr|summary|the short version|bottom line|executive summary|in short|headline|overview)\b/i
const KEYNUM_RE = /^(key numbers|key figures|key metrics|at a glance|by the numbers|numbers)\b/i

export function plainText(md: string): string {
  return md
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[*_`]/g, '')
    .trim()
}

function keyNumbersFrom(blocks: Block[]): KeyNumber[] {
  const out: KeyNumber[] = []
  for (const b of blocks) {
    if (b.kind === 'list') {
      for (const item of b.items) {
        const t = plainText(item.text)
        const m = t.match(/^(.{1,40}?)\s*(?::|—|–|-|=)\s+(.+)$/)
        if (m) out.push({ label: m[1].trim(), value: m[2].trim() })
      }
    } else if (b.kind === 'table' && b.header.length >= 2) {
      for (const r of b.rows) out.push({ label: plainText(r[0] ?? ''), value: plainText(r[1] ?? '') })
    }
  }
  return out.filter((k) => k.label && k.value).slice(0, 6)
}

/**
 * Split a report into title / TL;DR / key numbers / sections. Sections are
 * cut at the shallowest heading level used below the (optional) H1 title.
 */
export function buildReport(blocks: Block[]): ReportDoc {
  let rest = blocks
  let title: string | null = null
  const h1s = rest.filter((b) => b.kind === 'heading' && b.level === 1)
  if (rest[0]?.kind === 'heading' && rest[0].level === 1 && h1s.length === 1) {
    title = plainText(rest[0].text)
    rest = rest.slice(1)
  }
  const levels = rest.filter((b): b is Extract<Block, { kind: 'heading' }> => b.kind === 'heading').map((h) => h.level)
  const top = levels.length ? Math.min(...levels) : 0

  const preamble: Block[] = []
  const sections: Section[] = []
  for (const b of rest) {
    if (b.kind === 'heading' && b.level === top) sections.push({ heading: b, blocks: [] })
    else if (sections.length) sections[sections.length - 1].blocks.push(b)
    else preamble.push(b)
  }

  let tldr: Block[] = []
  const tIdx = sections.findIndex((s) => TLDR_RE.test(plainText(s.heading.text)))
  if (tIdx >= 0) {
    tldr = sections[tIdx].blocks
    sections.splice(tIdx, 1)
  }
  if (!tldr.length && preamble.length && preamble.every((b) => b.kind === 'paragraph' || b.kind === 'list')) {
    tldr = preamble.splice(0, preamble.length)
  }

  let keyNumbers: KeyNumber[] = []
  const kIdx = sections.findIndex((s) => KEYNUM_RE.test(plainText(s.heading.text)))
  if (kIdx >= 0) {
    keyNumbers = keyNumbersFrom(sections[kIdx].blocks)
    if (keyNumbers.length) sections.splice(kIdx, 1)
  }

  return { title, tldr, keyNumbers, preamble, sections }
}

/** Rough "how long is this" used to decide when to collapse a section. */
export function blockWeight(blocks: Block[]): number {
  let n = 0
  for (const b of blocks) {
    if (b.kind === 'paragraph' || b.kind === 'quote') n += b.text.length
    else if (b.kind === 'list') n += b.items.reduce((a, it) => a + it.text.length + 40, 0)
    else if (b.kind === 'table') n += 60 * (b.rows.length + 1)
    else if (b.kind === 'code') n += b.body.length
    else n += 60
  }
  return n
}
