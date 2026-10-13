import { useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, CalendarClock, LayoutGrid, PieChart, Sparkles, Target, Wallet, Scale, TrendingDown, Activity } from 'lucide-react'
import { ArchieIcon } from '@/components/kit'

import { getSnapshot } from '@/api/client'
import { useHeldPositions } from '@/components/instrument/instrument-provider'
import { useDisplayPrefs } from '@/lib/display-prefs'
import { formatCompactMoney } from '@/utils/format'
import type { PositionItem } from '@/types'

type Chip = { icon: typeof Sparkles; label: string; prompt: string }

function symbolOf(p: PositionItem) {
  return p.display_ticker || p.ticker.split('_')[0]
}

/** Suggestions grounded in the actual book: idle cash, concentration, a laggard. */
function smartChips(positions: PositionItem[], cash: number | null, total: number | null, currency: string): Chip[] {
  const chips: Chip[] = [
    { icon: PieChart, label: 'Show my book by theme', prompt: "Show my portfolio split by theme with today's move." },
  ]
  if (cash !== null && total && cash >= Math.max(100, total * 0.02)) {
    const amt = formatCompactMoney(cash, currency, 1)
    chips.push({
      icon: Wallet,
      label: `How should I deploy ${amt} idle cash?`,
      prompt: `I have ${amt} of uninvested cash. Given my allocation and risk rails, how should I deploy it? Propose specific trades with sizes and which account each belongs in.`,
    })
  }
  // Whole-book weight per symbol (a name held in both accounts is one bet).
  const bySym = new Map<string, { w: number; p: PositionItem }>()
  for (const p of positions) {
    const s = symbolOf(p)
    const cur = bySym.get(s)
    bySym.set(s, { w: (cur?.w ?? 0) + (p.weight || 0), p })
  }
  const top = [...bySym.entries()].sort((a, b) => b[1].w - a[1].w)[0]
  if (top && top[1].w >= 0.15) {
    chips.push({
      icon: Scale,
      label: `Is ${top[0]} at ${Math.round(top[1].w * 100)}% too big a bet?`,
      prompt: `${top[0]} is about ${Math.round(top[1].w * 100)}% of my book. Is that too concentrated for my goals and rails? If I should trim, propose how much and from which account.`,
    })
  }
  const laggard = positions
    .filter((p) => p.total_cost > 0)
    .map((p) => ({ p, r: p.ppl / p.total_cost }))
    .sort((a, b) => a.r - b.r)[0]
  if (laggard && laggard.r <= -0.1) {
    const s = symbolOf(laggard.p)
    chips.push({
      icon: TrendingDown,
      label: `Hold or cut ${s}? It's down ${Math.round(-laggard.r * 100)}%`,
      prompt: `${s} is down ${Math.round(-laggard.r * 100)}% from my cost. Has the thesis broken, or is this noise? Give me a clear hold / add / cut call with the reasoning.`,
    })
  }
  if (chips.length < 4) {
    chips.push({
      icon: Activity,
      label: 'What moved my book today?',
      prompt: "What moved my portfolio today and why? Show the biggest contributors and anything I should act on.",
    })
  }
  return chips.slice(0, 4)
}

const BUILDERS: { icon: typeof Sparkles; title: string; body: string; prefill: string; example: string }[] = [
  {
    icon: LayoutGrid,
    title: 'Build me a view…',
    body: 'A page pinned in your sidebar, live on your data.',
    prefill: 'Build me a view of ',
    example: 'dividend income by month',
  },
  {
    icon: Target,
    title: 'Start a play on…',
    body: 'A sized, staged theme bet with kill rules.',
    prefill: 'Start a play on ',
    example: 'AI power demand',
  },
  {
    icon: CalendarClock,
    title: 'Set up a recipe…',
    body: 'A routine Archie runs for you on a schedule.',
    prefill: 'Set up a recipe that ',
    example: 'every Friday flags anything over its cap',
  },
]

let snapshotCache: { key: string; cash: number; total: number } | null = null

/**
 * Archie's empty conversation: what it can do, grounded in the user's book.
 * Top chips ask straight away; the builder cards pre-fill the composer so the
 * user finishes the sentence (views, plays and recipes are all things Archie
 * sets up for you).
 */
export function ArchieEmptyState({
  disabled,
  onSend,
  onPrefill,
}: {
  disabled: boolean
  onSend: (prompt: string) => void
  onPrefill: (text: string) => void
}) {
  const positions = useHeldPositions()
  const { currency } = useDisplayPrefs()
  const key = `all:${currency}`
  const [book, setBook] = useState<{ cash: number; total: number } | null>(snapshotCache?.key === key ? snapshotCache : null)

  useEffect(() => {
    let alive = true
    getSnapshot('all', currency)
      .then((s) => {
        snapshotCache = { key, cash: s.account.free_cash, total: s.account.total }
        if (alive) setBook(snapshotCache)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [currency, key])

  const chips = useMemo(
    () => smartChips(positions, book?.cash ?? null, book?.total ?? null, currency),
    [positions, book, currency],
  )

  return (
    <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col justify-center gap-8 py-4">
      <div className="space-y-2">
        <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <ArchieIcon className="size-5" />
        </div>
        <p className="text-2xl font-semibold tracking-tight text-foreground">What should we work on?</p>
        <p className="max-w-[56ch] text-sm leading-relaxed text-muted-foreground">
          Archie reads your live portfolio and the market, shows you what's going on, and builds things for you.
          Anything it wants to trade comes back as a proposal for you to approve.
        </p>
      </div>

      <div className="space-y-2.5">
        <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">From your book</p>
        <div className="flex flex-wrap gap-2">
          {chips.map(({ icon: Icon, label, prompt }) => (
            <button
              key={label}
              type="button"
              onClick={() => onSend(prompt)}
              disabled={disabled}
              className="group inline-flex items-center gap-2 rounded-full border border-border bg-card/50 px-3.5 py-2 text-left text-sm text-foreground transition-colors hover:border-primary/40 hover:bg-card disabled:opacity-50"
            >
              <Icon className="size-3.5 shrink-0 text-muted-foreground transition-colors group-hover:text-primary" />
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-2.5">
        <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">Archie can build and set up</p>
        <div className="grid gap-2 sm:grid-cols-3">
          {BUILDERS.map(({ icon: Icon, title, body, prefill, example }) => (
            <button
              key={title}
              type="button"
              onClick={() => onPrefill(prefill)}
              disabled={disabled}
              className="group flex flex-col gap-1.5 rounded-xl border border-border bg-card/40 p-3.5 text-left transition-colors hover:border-primary/40 hover:bg-card disabled:opacity-50"
            >
              <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                <Icon className="size-4 text-muted-foreground transition-colors group-hover:text-primary" />
                {title}
                <ArrowUpRight className="ml-auto size-3.5 text-muted-foreground/0 transition-colors group-hover:text-muted-foreground" />
              </span>
              <span className="text-xs leading-relaxed text-muted-foreground">{body}</span>
              <span className="text-xs italic text-muted-foreground/70">e.g. “{example}”</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
