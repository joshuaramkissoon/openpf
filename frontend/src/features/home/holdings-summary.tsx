import { Money, Pct, PctDelta, Section, SectionLink } from "@/components/kit"
import { Skeleton } from "@/components/ui/skeleton"
import { useBook } from "@/features/plays/book"
import { useAppActions } from "@/lib/app-actions"
import { usePrivacyMode } from "@/lib/privacy"
import { cn } from "@/lib/utils"
import { formatMoney, formatPercent } from "@/utils/format"
import type { AppConfig, PortfolioSnapshot, PositionItem } from "@/types"

const COLORS = [
  "var(--color-chart-1)",
  "var(--color-chart-2)",
  "var(--color-chart-3)",
  "var(--color-chart-4)",
  "var(--color-chart-5)",
  "var(--color-chart-6)",
]
const TOP = 6

/**
 * Allocation as one stacked bar (top names, other, cash) over a short list of
 * the largest holdings. The full table lives at /home/holdings.
 */
export function HoldingsSummary({
  snapshot,
  positions,
}: {
  snapshot: PortfolioSnapshot | null
  positions: PositionItem[]
}) {
  const { openTicker, navigate } = useAppActions()
  if (!snapshot) {
    return (
      <Section title="Holdings">
        <Skeleton className="h-48 w-full rounded-xl" />
      </Section>
    )
  }
  const total = snapshot.account.total || 1
  const top = positions.slice(0, TOP)
  const otherValue = positions.slice(TOP).reduce((s, p) => s + p.value, 0)
  const cash = snapshot.account.free_cash
  const segments = [
    ...top.map((p, i) => ({ key: p.ticker, label: p.display_ticker || p.ticker, w: p.value / total, color: COLORS[i % COLORS.length] })),
    ...(otherValue > 0 ? [{ key: "other", label: "Other", w: otherValue / total, color: "var(--color-muted-foreground)" }] : []),
    ...(cash > 0 ? [{ key: "cash", label: "Cash", w: cash / total, color: "var(--color-border)" }] : []),
  ]

  return (
    <Section
      title="Holdings"
      description={`${positions.length} positions`}
      action={<SectionLink onClick={() => navigate("/home/holdings")}>All holdings →</SectionLink>}
    >
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-muted" role="img" aria-label="Allocation">
        {segments.map((s) => (
          <span
            key={s.key}
            title={`${s.label} ${formatPercent(s.w)}`}
            className="h-full border-r border-background last:border-r-0"
            style={{ width: `${Math.max(s.w * 100, 0.5)}%`, background: s.color }}
          />
        ))}
      </div>

      <div className="mt-4 divide-y divide-border/60">
        {top.map((p, i) => {
          const cost = p.total_cost || p.value - p.ppl
          const ret = cost > 0 ? p.ppl / cost : null
          return (
            <button
              key={`${p.account_kind}:${p.ticker}`}
              type="button"
              onClick={() => openTicker(p.ticker)}
              className="-mx-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded-md px-2 py-2 text-left text-sm transition-colors hover:bg-muted/40"
            >
              <span className="size-2 shrink-0 rounded-[3px]" style={{ background: COLORS[i % COLORS.length] }} />
              <span className="w-16 shrink-0 font-medium">{p.display_ticker || p.ticker}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{p.name}</span>
              <Money value={p.value} currency={snapshot.account.currency} decimals={0} className="w-24 shrink-0 text-right" />
              <Pct value={p.weight} className="hidden w-12 shrink-0 text-right text-xs text-muted-foreground sm:inline" />
              <PctDelta value={ret} className="w-14 shrink-0 text-right text-xs" />
            </button>
          )
        })}
      </div>
    </Section>
  )
}

type Chip = { tone: "ok" | "warn" | "neutral"; text: string; action?: { label: string; prompt: string } }

/** Risk in plain English: a few chips instead of a wall of KPI tiles. */
export function RiskChips({
  snapshot,
  positions,
  config,
}: {
  snapshot: PortfolioSnapshot | null
  positions: PositionItem[]
  config: AppConfig | null
}) {
  const { askArchie } = useAppActions()
  const privacy = usePrivacyMode()
  const { book } = useBook()
  if (!snapshot) return <Skeleton className="h-32 w-full rounded-xl" />
  const { metrics, account } = snapshot
  const chips: Chip[] = []
  // Per-theme concentration first: one bet can be bigger than any single stock.
  const theme = book?.theme_risk[0]
  if (theme && theme.weight >= 0.15) {
    chips.push({
      tone: theme.tone === "warn" ? "warn" : "neutral",
      text: theme.text,
      action:
        theme.tone === "warn"
          ? {
              label: "Stress-test it",
              prompt: `${theme.text}. Pressure-test the concentration in my "${theme.title}" play: what happens to my book if the theme breaks, and should I cap or trim it? Play id: ${theme.play_id}.`,
            }
          : undefined,
    })
  }
  const topPos = positions[0]
  const maxW = config?.risk.max_position_weight ?? 0.25
  if (topPos) {
    const over = topPos.weight > maxW
    chips.push({
      tone: over ? "warn" : "ok",
      text: `Largest holding ${topPos.display_ticker || topPos.ticker} is ${formatPercent(topPos.weight, 0)} of the book${over ? ` — above your ${formatPercent(maxW, 0)} cap` : ""}`,
      action: over
        ? { label: "Plan a trim", prompt: `${topPos.ticker} is ${formatPercent(topPos.weight, 0)} of my portfolio, above my ${formatPercent(maxW, 0)} cap. Propose a trim and where the proceeds should go.` }
        : undefined,
    })
  }
  if (metrics.concentration_hhi > 0) {
    const eff = 1 / metrics.concentration_hhi
    chips.push({
      tone: eff < 5 ? "warn" : "neutral",
      text: `Diversified like ~${eff.toFixed(eff < 10 ? 1 : 0)} equal positions`,
    })
  }
  if (Number.isFinite(metrics.estimated_beta) && metrics.estimated_beta > 0) {
    const b = metrics.estimated_beta
    chips.push({
      tone: b > 1.3 ? "warn" : "neutral",
      text: b >= 0.95 && b <= 1.05 ? "Moves roughly with the market" : `Moves ~${b.toFixed(1)}× the market`,
    })
  }
  if (metrics.estimated_volatility > 0) {
    chips.push({ tone: "neutral", text: `Typical yearly swing ±${formatPercent(metrics.estimated_volatility, 0)}` })
  }
  const idle = metrics.cash_ratio > 0.08
  chips.push({
    tone: idle ? "warn" : "ok",
    text: `${formatPercent(metrics.cash_ratio, 0)} in cash${privacy === "blur" ? "" : ` (${formatMoney(account.free_cash, account.currency, 0)})`}`,
    action: idle
      ? {
          label: "Deploy it",
          prompt: `I have ${formatMoney(account.free_cash, account.currency, 0)} (${formatPercent(metrics.cash_ratio, 0)}) sitting in cash. How should I deploy it given my allocation and risk rails? Propose specific trades.`,
        }
      : undefined,
  })

  return (
    <Section title="Risk at a glance">
      <ul className="space-y-2">
        {chips.map((c) => (
          <li key={c.text} className="flex items-start gap-2.5 text-sm">
            <span
              className={cn(
                "mt-[7px] size-1.5 shrink-0 rounded-full",
                c.tone === "warn" ? "bg-warning" : c.tone === "ok" ? "bg-positive" : "bg-muted-foreground/50",
              )}
            />
            <span className="min-w-0 flex-1 leading-relaxed text-foreground/90">
              {c.text}
              {c.action ? (
                <button
                  type="button"
                  onClick={() => askArchie(c.action!.prompt)}
                  className="ml-2 text-xs text-primary hover:underline"
                >
                  {c.action.label} →
                </button>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </Section>
  )
}
