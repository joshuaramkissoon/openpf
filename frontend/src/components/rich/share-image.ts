/**
 * The result card as a 1200×630 PNG (the OG-image size), drawn on a canvas in
 * the browser: no server renderer, no new dependencies. It uses the app's own
 * fonts (already loaded in the document). Money is blurred unless privacy is off.
 */
import type { ReportStat, ReportSummary } from '@/types'
import { statText, statTone } from './card-model'

const W = 1200
const H = 630

type Palette = { bg: string; card: string; fg: string; muted: string; faint: string; rule: string; primary: string; pos: string; neg: string; warn: string }

const DARK: Palette = {
  bg: '#16191e', card: '#1d2127', fg: '#eef0f3', muted: '#a3a9b3', faint: '#6c737e', rule: 'rgba(255,255,255,0.08)',
  primary: '#7fb59b', pos: '#6fcf8e', neg: '#e2786a', warn: '#e3c27a',
}
const LIGHT: Palette = {
  bg: '#f6f7f8', card: '#ffffff', fg: '#1d2128', muted: '#5d6470', faint: '#8d939c', rule: 'rgba(15,20,30,0.09)',
  primary: '#3f8a6a', pos: '#23894d', neg: '#c4483b', warn: '#a87a14',
}

const SANS = "'Geist Variable', ui-sans-serif, system-ui, sans-serif"
const MONO = "'Geist Mono Variable', ui-monospace, 'SF Mono', Menlo, monospace"

function plain(md: string): string {
  return md.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/[*_`]/g, '').trim()
}

function wrap(ctx: CanvasRenderingContext2D, text: string, maxWidth: number, maxLines: number): string[] {
  const words = text.split(/\s+/)
  const lines: string[] = []
  let line = ''
  for (const w of words) {
    const next = line ? `${line} ${w}` : w
    if (ctx.measureText(next).width <= maxWidth) line = next
    else {
      if (line) lines.push(line)
      line = w
      if (lines.length === maxLines) break
    }
  }
  if (line && lines.length < maxLines) lines.push(line)
  if (lines.length === maxLines && words.join(' ') !== lines.join(' ')) {
    let last = lines[maxLines - 1]
    while (ctx.measureText(`${last}…`).width > maxWidth && last.length) last = last.slice(0, -1)
    lines[maxLines - 1] = `${last.trimEnd()}…`
  }
  return lines
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath()
  ctx.roundRect(x, y, w, h, r)
}

function drawStat(ctx: CanvasRenderingContext2D, s: ReportStat, x: number, y: number, w: number, h: number, p: Palette, ccy: string, blurMoney: boolean) {
  const pad = 26
  ctx.fillStyle = p.muted
  ctx.font = `600 15px ${SANS}`
  ctx.letterSpacing = '1.5px'
  ctx.fillText(s.label.toUpperCase(), x + pad, y + pad + 12)
  ctx.letterSpacing = '0px'

  const tone = statTone(s)
  ctx.fillStyle = tone === 'pos' ? p.pos : tone === 'neg' ? p.neg : p.fg
  let size = 46
  ctx.font = `500 ${size}px ${MONO}`
  const text = statText(s, ccy)
  while (ctx.measureText(text).width > w - pad * 2 && size > 24) {
    size -= 2
    ctx.font = `500 ${size}px ${MONO}`
  }
  const isMoney = s.kind === 'money' || (s.sub ?? '').match(/[£$€]\d/)
  ctx.save()
  if (blurMoney && s.kind === 'money') ctx.filter = 'blur(14px)'
  ctx.fillText(text, x + pad, y + pad + 26 + size)
  ctx.restore()

  let cy = y + pad + 26 + size + 14
  if (s.sub) {
    ctx.save()
    if (blurMoney && isMoney && /[£$€]\d/.test(s.sub)) ctx.filter = 'blur(8px)'
    ctx.fillStyle = p.muted
    ctx.font = `400 17px ${SANS}`
    ctx.fillText(wrap(ctx, plain(s.sub), w - pad * 2, 1)[0] ?? '', x + pad, cy + 14)
    ctx.restore()
    cy += 26
  }

  // The tile's picture sits on its floor.
  const vx = x + pad
  const vw = w - pad * 2
  const vy = y + h - pad - 22
  if (s.spark && s.spark.length >= 2) {
    const min = Math.min(...s.spark)
    const max = Math.max(...s.spark)
    const span = max - min || 1
    ctx.strokeStyle = tone === 'neg' ? p.neg : tone === 'pos' ? p.pos : p.primary
    ctx.lineWidth = 2.5
    ctx.lineJoin = 'round'
    ctx.beginPath()
    s.spark.forEach((v, i) => {
      const px = vx + (i / (s.spark!.length - 1)) * vw
      const py = vy + 22 - ((v - min) / span) * 26
      if (i) ctx.lineTo(px, py)
      else ctx.moveTo(px, py)
    })
    ctx.stroke()
  } else if (s.bar && s.bar.max > 0) {
    const f = Math.max(0, Math.min(1, s.bar.value / s.bar.max))
    ctx.fillStyle = p.rule
    roundRect(ctx, vx, vy + 10, vw, 8, 4)
    ctx.fill()
    ctx.fillStyle = p.primary
    roundRect(ctx, vx, vy + 10, Math.max(8, vw * f), 8, 4)
    ctx.fill()
  } else if (s.kind === 'date' && s.timeline?.length) {
    const dates = s.timeline.map((m) => new Date(m.date).getTime())
    const start = Math.min(Date.now(), ...dates)
    const end = Math.max(...dates) + 86400000
    ctx.strokeStyle = p.rule
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(vx, vy + 6)
    ctx.lineTo(vx + vw, vy + 6)
    ctx.stroke()
    s.timeline.forEach((m, i) => {
      const px = vx + ((dates[i] - start) / (end - start)) * vw
      ctx.fillStyle = p.primary
      ctx.beginPath()
      ctx.arc(px, vy + 6, 5, 0, Math.PI * 2)
      ctx.fill()
      ctx.fillStyle = p.muted
      ctx.font = `500 13px ${MONO}`
      ctx.fillText(m.label, Math.min(px - 6, vx + vw - ctx.measureText(m.label).width), vy + 28)
    })
  } else if (s.tickers?.length) {
    let tx = vx
    ctx.font = `600 15px ${MONO}`
    for (const t of s.tickers.slice(0, 4)) {
      const tw = ctx.measureText(t).width + 18
      if (tx + tw > vx + vw) break
      ctx.strokeStyle = p.rule
      ctx.lineWidth = 1.5
      roundRect(ctx, tx, vy - 2, tw, 28, 7)
      ctx.stroke()
      ctx.fillStyle = p.fg
      ctx.fillText(t, tx + 9, vy + 17)
      tx += tw + 8
    }
  }
}

export async function renderCardPng(opts: {
  summary: ReportSummary
  stats: ReportStat[]
  title: string
  when?: string | null
  theme: 'dark' | 'light'
  blurMoney: boolean
  currency?: string
}): Promise<Blob> {
  const p = opts.theme === 'light' ? LIGHT : DARK
  const ccy = opts.currency ?? 'GBP'
  try {
    await document.fonts.ready
  } catch {
    /* fonts are a nicety */
  }
  const canvas = document.createElement('canvas')
  canvas.width = W * 2
  canvas.height = H * 2
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('canvas unavailable')
  ctx.scale(2, 2)

  // Ground + a quiet glow in the brand colour.
  ctx.fillStyle = p.bg
  ctx.fillRect(0, 0, W, H)
  const glow = ctx.createRadialGradient(80, 40, 0, 80, 40, 700)
  glow.addColorStop(0, opts.theme === 'light' ? 'rgba(63,138,106,0.12)' : 'rgba(127,181,155,0.13)')
  glow.addColorStop(1, 'rgba(0,0,0,0)')
  ctx.fillStyle = glow
  ctx.fillRect(0, 0, W, H)

  const M = 64
  // Eyebrow: brand · recipe · date.
  ctx.fillStyle = p.primary
  roundRect(ctx, M, M - 2, 10, 10, 3)
  ctx.fill()
  ctx.font = `600 17px ${SANS}`
  ctx.letterSpacing = '2px'
  ctx.fillStyle = p.fg
  const eyebrow = opts.title.toUpperCase()
  ctx.fillText(eyebrow, M + 22, M + 8)
  const ew = ctx.measureText(eyebrow).width
  ctx.fillStyle = p.faint
  ctx.fillText(opts.when ? `  ·  ${opts.when.toUpperCase()}` : '', M + 22 + ew, M + 8)
  ctx.letterSpacing = '0px'

  // Headline.
  ctx.fillStyle = p.fg
  ctx.font = `600 50px ${SANS}`
  ctx.letterSpacing = '-1px'
  const lines = wrap(ctx, plain(opts.summary.headline), W - M * 2, 3)
  lines.forEach((l, i) => ctx.fillText(l, M, M + 84 + i * 60))
  ctx.letterSpacing = '0px'

  // Stat tiles.
  const stats = opts.stats.slice(0, 4)
  if (stats.length) {
    const top = Math.max(M + 84 + lines.length * 60 + 10, 300)
    const tileH = H - top - 84
    const gap = 14
    const tw = (W - M * 2 - gap * (stats.length - 1)) / stats.length
    stats.forEach((s, i) => {
      const x = M + i * (tw + gap)
      ctx.fillStyle = p.card
      roundRect(ctx, x, top, tw, tileH, 18)
      ctx.fill()
      ctx.strokeStyle = p.rule
      ctx.lineWidth = 1.5
      ctx.stroke()
      drawStat(ctx, s, x, top, tw, tileH, p, ccy, opts.blurMoney)
    })
  }

  // Footer.
  ctx.fillStyle = p.faint
  ctx.font = `500 16px ${SANS}`
  ctx.fillText('OpenPF · Archie', M, H - 40)
  if (opts.summary.action) {
    const action = `Next: ${plain(opts.summary.action)}`
    ctx.font = `500 16px ${SANS}`
    ctx.fillStyle = p.muted
    const aw = Math.min(ctx.measureText(action).width, W - M * 2 - 220)
    ctx.fillText(wrap(ctx, action, aw + 1, 1)[0] ?? '', W - M - aw, H - 40)
  }

  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('could not encode'))), 'image/png'))
}

export function cardFileName(title: string, when?: string | null): string {
  const slug = `${title} ${when ?? ''}`.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  return `${slug || 'result'}.png`
}
