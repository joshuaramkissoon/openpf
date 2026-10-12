import { Info, Minus, ShieldCheck, TriangleAlert, X } from 'lucide-react'

import { Money, MoneyDelta, Pct } from '@/components/kit'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAppActions } from '@/lib/app-actions'
import { cn } from '@/lib/utils'
import { formatPercent } from '@/utils/format'
import type { KillCriterion, KillState, LegPerformance, Play, PlayLeg, PlaySizing, StagedTranche } from './types'
import { Block, Eyebrow, KillIcon, ROLE_LABEL, ruleLabel } from './ui'

/** Editable basket for a draft: weights as %, remove a leg. */
export function BasketEditor({
  legs,
  allocations,
  onChange,
}: {
  legs: PlayLeg[]
  allocations?: PlaySizing['allocations']
  onChange: (legs: PlayLeg[]) => void
}) {
  const { openTicker } = useAppActions()
  const total = legs.reduce((s, l) => s + l.target_weight, 0)
  const alloc = new Map((allocations ?? []).map((a) => [a.ticker, a]))

  function setWeight(i: number, pct: string) {
    const v = Math.max(0, Math.min(100, Number(pct.replace(/[^0-9.]/g, '')) || 0)) / 100
    onChange(legs.map((l, j) => (j === i ? { ...l, target_weight: v } : l)))
  }

  return (
    <div className="-mx-1 overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="text-xs text-muted-foreground">Instrument</TableHead>
            <TableHead className="hidden text-xs text-muted-foreground sm:table-cell">Why</TableHead>
            <TableHead className="w-24 text-right text-xs text-muted-foreground">Weight</TableHead>
            <TableHead className="w-8" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {legs.map((l, i) => {
            const a = alloc.get(l.ticker)
            return (
              <TableRow key={l.ticker} className="align-top hover:bg-muted/40">
                <TableCell className="py-3">
                  <button type="button" onClick={() => openTicker(l.ticker)} className="text-left">
                    <span className="flex items-center gap-2">
                      <span className="font-mono text-sm font-medium">{l.ticker}</span>
                      <span className="text-[11px] text-muted-foreground">{ROLE_LABEL[l.role] ?? l.role}</span>
                    </span>
                    <span className="block max-w-56 truncate text-xs text-muted-foreground">{l.name}</span>
                  </button>
                  {a && a.held_gbp > 0 ? (
                    <span className="mt-1 block text-[11px] text-muted-foreground">
                      Held <Money value={a.held_gbp} decimals={0} /> · {a.new_gbp > 0 ? 'topping up' : 'covered — no new money'}
                    </span>
                  ) : null}
                  {l.instrument_verified === false ? (
                    <span className="mt-1 flex items-center gap-1 text-[11px] text-warning">
                      <TriangleAlert className="size-3" /> Not found in your broker's instrument list
                    </span>
                  ) : null}
                </TableCell>
                <TableCell className="hidden max-w-md py-3 text-xs leading-relaxed whitespace-normal text-muted-foreground sm:table-cell">
                  {l.rationale}
                </TableCell>
                <TableCell className="py-2.5 text-right">
                  <div className="relative ml-auto w-20">
                    <Input
                      aria-label={`${l.ticker} weight`}
                      inputMode="decimal"
                      value={Math.round(l.target_weight * 1000) / 10}
                      onChange={(e) => setWeight(i, e.target.value)}
                      className="h-8 pr-6 text-right font-mono text-sm tabular-nums"
                    />
                    <span className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 text-xs text-muted-foreground">%</span>
                  </div>
                </TableCell>
                <TableCell className="py-2.5">
                  <button
                    type="button"
                    aria-label={`Remove ${l.ticker}`}
                    disabled={legs.length <= 2}
                    onClick={() => onChange(legs.filter((_, j) => j !== i))}
                    className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-30"
                  >
                    <X className="size-4" />
                  </button>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
      <p className={cn('mt-2 px-1 text-right text-xs', Math.abs(total - 1) > 0.005 ? 'text-warning' : 'text-muted-foreground')}>
        Total {formatPercent(total, 0)}
        {Math.abs(total - 1) > 0.005 ? ' — weights are normalised to 100% when saved' : ''}
      </p>
    </div>
  )
}

/** Live legs table for an active play: target vs actual, value, P/L. */
export function LegsTable({ legs, built }: { legs: LegPerformance[]; built: boolean }) {
  const { openTicker } = useAppActions()
  return (
    <div className="-mx-1 overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="text-xs text-muted-foreground">Instrument</TableHead>
            <TableHead className="text-right text-xs text-muted-foreground">Target</TableHead>
            <TableHead className="text-right text-xs text-muted-foreground">Actual</TableHead>
            <TableHead className="hidden text-right text-xs text-muted-foreground sm:table-cell">Value</TableHead>
            <TableHead className="text-right text-xs text-muted-foreground">P/L</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {legs.map((l) => (
            <TableRow key={l.ticker} className="cursor-pointer hover:bg-muted/40" onClick={() => openTicker(l.ticker)}>
              <TableCell className="py-2.5">
                <span className="flex items-center gap-2">
                  <span className="font-mono text-sm font-medium">{l.ticker}</span>
                  <span className="text-[11px] text-muted-foreground">{ROLE_LABEL[l.role] ?? l.role}</span>
                </span>
                <span className="block max-w-48 truncate text-xs text-muted-foreground">{l.name}</span>
              </TableCell>
              <TableCell className="text-right">
                <Pct value={l.target_weight} decimals={0} className="text-sm text-muted-foreground" />
              </TableCell>
              <TableCell className="text-right">
                {l.value_gbp > 0 ? (
                  <span className="inline-flex items-center gap-1.5">
                    <Pct value={l.actual_weight} decimals={0} className="text-sm" />
                    {built && Math.abs(l.drift) > 0.15 ? <TriangleAlert className="size-3.5 text-warning" aria-label="Drifted" /> : null}
                  </span>
                ) : (
                  <Minus className="ml-auto size-3.5 text-muted-foreground" />
                )}
              </TableCell>
              <TableCell className="hidden text-right sm:table-cell">
                {l.value_gbp > 0 ? <Money value={l.value_gbp} decimals={0} className="text-sm" /> : <span className="text-sm text-muted-foreground">—</span>}
              </TableCell>
              <TableCell className="text-right">
                {l.value_gbp > 0 ? <MoneyDelta value={l.ppl_gbp} decimals={0} className="text-sm" /> : <span className="text-sm text-muted-foreground">—</span>}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/** Why this size: big number, the reasoning steps, warnings. */
export function SizingPanel({ play }: { play: Play }) {
  const s = play.sizing
  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <Eyebrow>Budget</Eyebrow>
          <Money value={play.budget_gbp} decimals={0} className="text-3xl font-semibold tracking-tight" />
        </div>
        <div className="text-right">
          <Eyebrow>Of your book</Eyebrow>
          <Pct value={play.budget_pct_of_book} decimals={1} className="text-lg" />
        </div>
      </div>
      {s.steps?.length ? (
        <ol className="space-y-2 border-l border-border pl-4">
          {s.steps.map((step, i) => (
            <li key={i} className="text-xs leading-relaxed text-muted-foreground">
              {step}
            </li>
          ))}
        </ol>
      ) : null}
      {s.warnings?.length ? (
        <div className="space-y-1.5">
          {s.warnings.map((w, i) => (
            <p key={i} className="flex items-start gap-2 rounded-lg bg-warning/10 px-3 py-2 text-xs text-warning">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
              {w}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  )
}

/** Staged entry timeline. */
export function TranchePlan({
  play,
  staged,
  onStage,
  staging,
}: {
  play: Play
  staged: StagedTranche[]
  onStage?: (index: number) => void
  staging?: boolean
}) {
  const tranches = play.sizing.tranches ?? []
  const done = new Map(staged.map((s) => [s.index, s]))
  const nextIdx = tranches.findIndex((_, i) => !done.has(i))
  return (
    <ol className="relative space-y-0">
      {tranches.map((t, i) => {
        const s = done.get(i)
        const isNext = i === nextIdx
        return (
          <li key={i} className="relative flex gap-3 pb-5 last:pb-0">
            {i < tranches.length - 1 ? <span className="absolute top-5 bottom-0 left-[7px] w-px bg-border" /> : null}
            <span
              className={cn(
                'relative mt-1 size-[15px] shrink-0 rounded-full border-2',
                s ? 'border-foreground bg-foreground' : isNext ? 'border-foreground bg-background' : 'border-border bg-background',
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-sm font-medium">
                  {t.label} <span className="font-normal text-muted-foreground">· {formatPercent(t.pct_of_budget, 0)}</span>
                </p>
                <Money value={play.budget_gbp * t.pct_of_budget} decimals={0} className="text-sm" />
              </div>
              <p className="text-xs text-muted-foreground">
                {s ? `Staged ${new Date(s.staged_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })} · ${s.intent_ids.length} proposals` : t.trigger}
              </p>
              {isNext && onStage && play.status === 'active' ? (
                <button
                  type="button"
                  disabled={staging}
                  onClick={() => onStage(i)}
                  className="mt-1.5 text-xs font-medium text-primary hover:underline disabled:opacity-50"
                >
                  {staging ? 'Staging…' : 'Stage this tranche →'}
                </button>
              ) : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** Kill criteria with optional live status. */
export function KillList({
  items,
  wwcmm,
}: {
  items: (KillCriterion & { state?: KillState; detail?: string })[]
  wwcmm?: string
}) {
  return (
    <div className="space-y-4">
      <ul className="space-y-3">
        {items.map((k, i) => (
          <li key={i} className="flex gap-3">
            <span className="mt-0.5">{k.state ? <KillIcon state={k.state} /> : k.rule ? <ShieldCheck className="size-4 text-muted-foreground" aria-label="Monitored rule" /> : <KillIcon state="manual" />}</span>
            <div className="min-w-0">
              <p className="text-sm leading-snug">{k.text}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {k.detail ?? (k.rule ? <span className="font-mono">{ruleLabel(k.rule)}</span> : 'Judgement call — checked in reviews')}
                {k.state && k.rule ? <span className="font-mono"> · {ruleLabel(k.rule)}</span> : null}
              </p>
            </div>
          </li>
        ))}
      </ul>
      {wwcmm ? (
        <div className="rounded-lg border border-dashed border-border px-4 py-3">
          <Eyebrow className="mb-1">What would change my mind</Eyebrow>
          <p className="text-sm leading-relaxed">{wwcmm}</p>
        </div>
      ) : null}
    </div>
  )
}

export function OverlapCallout({ play }: { play: Play }) {
  const rows = play.meta.overlap ?? []
  if (!rows.length) return null
  return (
    <div className="flex gap-3 rounded-lg bg-muted/50 px-4 py-3">
      <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="space-y-1 text-xs leading-relaxed">
        <p className="font-medium text-foreground">You already own part of this theme</p>
        <p className="text-muted-foreground">
          {rows.map((r, i) => (
            <span key={r.ticker}>
              {i ? ', ' : ''}
              <span className="font-mono text-foreground">{r.ticker}</span> <Money value={r.value_gbp} decimals={0} /> (
              {formatPercent(r.weight_of_book, 1)} of book)
            </span>
          ))}
          . Sizing counts this towards the play, so new money goes to the legs you don't hold.
        </p>
      </div>
    </div>
  )
}

export { Block }
