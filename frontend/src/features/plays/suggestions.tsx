import { useEffect, useState } from 'react'
import { Check, Loader2, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'

import { Money } from '@/components/kit'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { toastApiError } from '@/lib/api-error'
import { useAppActions } from '@/lib/app-actions'
import { cn } from '@/lib/utils'
import { formatPercent } from '@/utils/format'
import { acceptSuggestion, dismissSuggestion, generateSuggestions, getSuggestions } from './api'
import { ArchieMark, Swatch, reloadBook } from './book'
import type { Suggestion, Suggestions } from './types'

/**
 * Archie groups the holdings that aren't in any play into suggested plays or
 * Core ("NVDA, AVGO, TSM already form an AI-infra bet at 18%"). The user
 * accepts, edits or dismisses each one — nothing applies on its own.
 *
 * The fast model answers in a few seconds, so the sheet waits for it (with a
 * calm loading state) and shows its groups directly. Theme rules only stand in
 * when the model fails or times out, with a quiet note saying so.
 */
export function SortHoldingsSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [data, setData] = useState<Suggestions | null>(null)
  const [thinking, setThinking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function regroup() {
    setThinking(true)
    setError(null)
    try {
      setData(await generateSuggestions(true))
    } catch {
      setError('Archie couldn’t group your holdings just now. Try again in a moment.')
    } finally {
      setThinking(false)
    }
  }

  useEffect(() => {
    if (!open) return
    let active = true
    setError(null)
    getSuggestions()
      .then((d) => {
        if (!active) return
        setData(d)
        if (!d.items.length && d.unsorted.length) void regroup()
      })
      .catch(() => active && setError('Your holdings are unavailable right now.'))
    return () => {
      active = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  function settled(id: string) {
    setData((d) => (d ? { ...d, items: d.items.filter((i) => i.id !== id) } : d))
  }

  const covered = new Set(data?.items.flatMap((i) => i.tickers) ?? [])
  const leftover = (data?.unsorted ?? []).filter((h) => !covered.has(h.ticker))

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 sm:max-w-lg">
        <SheetHeader className="border-b border-border px-5 pt-5 pb-4">
          <SheetTitle className="flex items-center gap-2 text-base">
            <ArchieMark className="text-muted-foreground" />
            Sort your holdings
          </SheetTitle>
          <SheetDescription>
            Archie groups what you already own into plays or Core. Nothing changes until you accept — and nothing is ever traded.
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {error ? <p className="text-sm text-muted-foreground">{error}</p> : null}
          {thinking && data?.unsorted.length ? (
            <GroupingHoldings tickers={data.unsorted.map((h) => h.ticker)} />
          ) : !data || thinking ? (
            !error ? <Skeleton className="h-40 w-full rounded-xl" /> : null
          ) : data.unsorted.length === 0 ? (
            <div className="flex flex-col items-start gap-2 py-8">
              <span className="flex size-8 items-center justify-center rounded-full bg-positive/10 text-positive">
                <Check className="size-4" />
              </span>
              <p className="text-sm font-medium">Everything has a home</p>
              <p className="text-xs text-muted-foreground">Every holding belongs to a play or Core.</p>
            </div>
          ) : (
            <>
              <div className="space-y-4 animate-in fade-in duration-500">
                {data.items.map((s) => (
                  <SuggestionCard key={s.id} s={s} onDone={() => settled(s.id)} />
                ))}
              </div>
              {data.items.length === 0 ? (
                <p className="text-sm text-muted-foreground">No groupings stood out. Move holdings one at a time from Home, or regroup.</p>
              ) : null}
              {leftover.length ? (
                <p className="text-xs text-muted-foreground">
                  Left unsorted: {leftover.map((h) => h.ticker).join(', ')} — they don’t fit a clear theme. You can move them yourself from Home.
                </p>
              ) : null}
            </>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-border px-5 py-3">
          <p className="text-[11px] text-muted-foreground" aria-live="polite">
            {thinking ? '' : data?.source === 'archie' ? 'Grouped by Archie' : data?.source === 'rules' ? data.refine_error || 'Grouped by theme rules' : ''}
          </p>
          <Button variant="ghost" size="sm" onClick={() => void regroup()} disabled={thinking || !data?.unsorted.length}>
            <RefreshCw className={cn('size-3.5', thinking && 'animate-spin')} />
            Regroup
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  )
}

// ── the wait: holdings drift into groups while the model thinks ─────────────

const STEPS = ['Reading your holdings', 'Looking for what they have in common', 'Drafting groups']
const CLUSTERS = [
  { x: 24, y: 32 },
  { x: 76, y: 32 },
  { x: 50, y: 70 },
]

/** Deterministic 0..1 noise so a chip's scatter position is stable across renders. */
function noise(i: number, salt: number) {
  const v = Math.sin(i * 12.9898 + salt * 78.233) * 43758.5453
  return v - Math.floor(v)
}

function chipPos(i: number, n: number, phase: number) {
  if (phase === 0) return { x: 12 + noise(i, 1) * 76, y: 12 + noise(i, 2) * 76, dx: 0, dy: 0 }
  // Each pass shuffles the chips and deals them evenly into three loose clusters, two to a row.
  const order = Array.from({ length: n }, (_, j) => j).sort((a, b) => noise(a, phase + 3) - noise(b, phase + 3))
  const c = order.indexOf(i) % CLUSTERS.length
  const members = order.filter((_, k) => k % CLUSTERS.length === c)
  const slot = members.indexOf(i)
  const rows = Math.ceil(members.length / 2)
  const row = Math.floor(slot / 2)
  const inRow = Math.min(2, members.length - row * 2)
  // Offsets in px (chips are a fixed size); cluster centres in % of the stage.
  return { x: CLUSTERS[c].x, y: CLUSTERS[c].y, dx: ((slot % 2) - (inRow - 1) / 2) * 56, dy: (row - (rows - 1) / 2) * 28 }
}

export function GroupingHoldings({ tickers }: { tickers: string[] }) {
  const [phase, setPhase] = useState(0)
  const [step, setStep] = useState(0)
  const shown = tickers.slice(0, 18)
  useEffect(() => {
    const t = window.setInterval(() => setPhase((p) => p + 1), 2600)
    const s1 = window.setTimeout(() => setStep(1), 1500)
    const s2 = window.setTimeout(() => setStep(2), 4200)
    return () => {
      window.clearInterval(t)
      window.clearTimeout(s1)
      window.clearTimeout(s2)
    }
  }, [])
  return (
    <div className="flex flex-col gap-4" role="status" aria-live="polite">
      <div className="relative h-64 overflow-hidden rounded-2xl border border-border/70 bg-[radial-gradient(ellipse_at_center,color-mix(in_oklab,var(--muted)_70%,transparent),transparent_70%)]">
        {CLUSTERS.map((c, i) => (
          <span
            key={i}
            aria-hidden
            className="absolute size-36 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/[0.06] transition-opacity duration-1000 motion-reduce:hidden"
            style={{ left: `${c.x}%`, top: `${c.y}%`, opacity: phase === 0 ? 0 : 1 }}
          />
        ))}
        {shown.map((t, i) => {
          const p = chipPos(i, shown.length, phase)
          return (
            <span
              key={t}
              aria-hidden
              className="absolute -translate-x-1/2 -translate-y-1/2 rounded-md border border-border bg-card px-2 py-0.5 font-mono text-[11px] text-foreground/80 shadow-sm transition-[left,top] duration-[1200ms] ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none"
              style={{ left: `calc(${p.x}% + ${p.dx}px)`, top: `calc(${p.y}% + ${p.dy}px)`, transitionDelay: `${(i % 6) * 40}ms` }}
            >
              {t}
            </span>
          )
        })}
        {tickers.length > shown.length ? (
          <span className="absolute right-3 bottom-2.5 font-mono text-[11px] text-muted-foreground">+{tickers.length - shown.length} more</span>
        ) : null}
      </div>
      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium">
          {STEPS[step]}
          <span className="inline-block w-5 animate-pulse text-muted-foreground">…</span>
        </p>
        <ol className="flex gap-1.5" aria-hidden>
          {STEPS.map((_, i) => (
            <li key={i} className={cn('h-1 flex-1 rounded-full transition-colors duration-700', i <= step ? 'bg-primary/70' : 'bg-muted')} />
          ))}
        </ol>
        <p className="text-xs text-muted-foreground">
          Archie is grouping {tickers.length} holding{tickers.length === 1 ? '' : 's'} into plays and Core. Usually a few seconds.
        </p>
      </div>
    </div>
  )
}

function SuggestionCard({ s, onDone }: { s: Suggestion; onDone: () => void }) {
  const { navigate } = useAppActions()
  const [title, setTitle] = useState(s.title)
  const [picked, setPicked] = useState<Set<string>>(new Set(s.tickers))
  const [busy, setBusy] = useState<null | 'accept' | 'dismiss'>(null)
  const chosen = s.holdings.filter((h) => picked.has(h.ticker))
  const weight = chosen.reduce((a, h) => a + h.weight, 0)
  const value = chosen.reduce((a, h) => a + h.value_gbp, 0)
  const isCore = s.kind === 'core'
  const verb = isCore ? 'Move to Core' : s.kind === 'existing' ? `Add to ${s.title}` : 'Make it a play'

  function toggle(t: string) {
    setPicked((p) => {
      const n = new Set(p)
      if (n.has(t)) n.delete(t)
      else n.add(t)
      return n
    })
  }

  async function accept() {
    setBusy('accept')
    try {
      const res = await acceptSuggestion(s.id, { title: title.trim() || s.title, tickers: [...picked] })
      await reloadBook()
      onDone()
      toast.success(isCore ? `${res.tickers.length} holding${res.tickers.length === 1 ? '' : 's'} moved to Core` : `“${title.trim() || s.title}” is now a play`, {
        description: isCore ? undefined : 'Built from holdings you already own — nothing was bought.',
        action: res.play_id ? { label: 'Open', onClick: () => navigate(`/plays/${res.play_id}`) } : undefined,
      })
    } catch (e) {
      toastApiError(e, 'Could not apply that')
      setBusy(null)
    }
  }

  async function dismiss() {
    setBusy('dismiss')
    try {
      await dismissSuggestion(s.id)
      onDone()
    } catch (e) {
      toastApiError(e, 'Could not dismiss')
      setBusy(null)
    }
  }

  return (
    <article className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          <Swatch kind={isCore ? 'core' : 'play'} colorIndex={isCore ? 0 : 1} />
          {isCore ? 'Core' : s.kind === 'existing' ? 'Join a play' : 'New play'}
        </span>
        <span className="font-mono text-xs tabular-nums text-muted-foreground">
          {formatPercent(weight, 0)} · <Money value={value} decimals={0} />
        </span>
      </div>
      {s.kind === 'play' ? (
        <Input
          aria-label="Play name"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="mt-2 -ml-1.5 h-8 w-[calc(100%+0.375rem)] border-transparent bg-transparent px-1.5 text-base font-semibold shadow-none hover:border-border focus-visible:border-ring dark:bg-transparent"
        />
      ) : (
        <p className="mt-2 px-0.5 text-base font-semibold">{isCore ? 'Core' : s.title}</p>
      )}
      {s.why ? <p className="mt-1 px-0.5 text-sm text-muted-foreground">{s.why}</p> : null}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {s.holdings.map((h) => {
          const on = picked.has(h.ticker)
          return (
            <button
              key={h.ticker}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(h.ticker)}
              title={`${h.name} · ${formatPercent(h.weight, 1)}`}
              className={cn(
                'rounded-md border px-2 py-0.5 font-mono text-xs transition-colors',
                on ? 'border-foreground/20 bg-muted text-foreground' : 'border-dashed border-border text-muted-foreground line-through',
              )}
            >
              {h.ticker}
            </button>
          )
        })}
      </div>
      <div className="mt-4 flex items-center justify-end gap-1.5">
        <Button variant="ghost" size="sm" onClick={() => void dismiss()} disabled={busy !== null}>
          Dismiss
        </Button>
        <Button size="sm" onClick={() => void accept()} disabled={busy !== null || picked.size === 0}>
          {busy === 'accept' ? <Loader2 className="size-3.5 animate-spin" /> : null}
          {verb}
        </Button>
      </div>
    </article>
  )
}
