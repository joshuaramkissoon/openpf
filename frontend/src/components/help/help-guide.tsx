import {
  Activity,
  CalendarClock,
  CreditCard,
  FileText,
  Gauge,
  LayoutDashboard,
  Lightbulb,
  ListChecks,
  MessageSquare,
  Settings,
  ShieldCheck,
  Sparkles,
  Telescope,
  type LucideIcon,
} from "lucide-react"

import { SectionCard } from "@/components/kit"
import { Badge } from "@/components/ui/badge"

type Feature = { icon: LucideIcon; title: string; what: string; how: string }

const FEATURES: Feature[] = [
  {
    icon: LayoutDashboard,
    title: "Portfolio",
    what: "Holdings, allocation, and risk across Invest + ISA at a glance.",
    how: "Switch account/currency in the top bar. Click any position row for a detail sheet with financials, signals (momentum, RSI, trend), risk, a price chart, and a Kronos forecast toggle.",
  },
  {
    icon: MessageSquare,
    title: "Archie (chat)",
    what: "Your conversational copilot — live prices, technicals, risk, forecasts, and portfolio reasoning.",
    how: "Ask anything (\"how's my portfolio?\", \"forecast PLTR\", \"what's overexposed?\"). Archie pulls live data via tools and can delegate to its quant + research specialists. Press ⌘/Ctrl + / to jump in and out.",
  },
  {
    icon: Telescope,
    title: "Research Desk",
    what: "Structured, agent-driven analysis of a holding or a brand-new idea — ends in a verdict.",
    how: "File an Analysis Request (subject, objective, optional hypothesis, horizon). Archie gathers live evidence (prices, technicals, risk, fundamentals/valuation), a Kronos forecast, and news, then returns verdict · confidence · suggested action · invalidation plus a saved report. Toggle \"save as thesis\" to track it over time.",
  },
  {
    icon: Gauge,
    title: "Leveraged Desk",
    what: "Leveraged positions, the risk rails that bound them, and the live signal queue.",
    how: "Set per-position size, max exposure, max open, take-profit/stop-loss, and the new daily target / loss-limit / max-trades rails. Execution stays gated until you turn broker mode to live.",
  },
  {
    icon: CalendarClock,
    title: "Scheduled Jobs",
    what: "Automated Archie routines on a cron schedule — the heartbeat of the daily-alpha loop.",
    how: "Enable daily_alpha_goal and set your £/day target to have Archie scan + propose entries each morning. Run any job on demand with \"Run now\"; outputs land in Artifacts.",
  },
  {
    icon: ListChecks,
    title: "Execution",
    what: "Review and act on proposed trade intents.",
    how: "Approve, reject, or execute intents Archie or a job proposed. Nothing trades automatically unless you've enabled auto-execute and it's within your rails.",
  },
  {
    icon: FileText,
    title: "Artifacts",
    what: "Every report Archie writes — research verdicts and scheduled-job briefings.",
    how: "Open any artifact to read the full markdown. Research Desk runs and scheduled jobs both save here.",
  },
  {
    icon: Lightbulb,
    title: "Insights",
    what: "Your theses, backtests, and Archie's reasoning history.",
    how: "Track active theses (incl. ones saved from the Research Desk), run a quick MA-crossover backtest, and browse past agent runs.",
  },
  {
    icon: CreditCard,
    title: "Usage",
    what: "Token usage powering Archie.",
    how: "You're on your Claude subscription (OAuth), so this is an estimate of model usage, not a bill.",
  },
  {
    icon: Activity,
    title: "Diagnostics",
    what: "Runtime health — MCP servers, models, and capabilities.",
    how: "Check that market-data, T212, scheduler, and forecast tools are live, and which model each runtime uses.",
  },
]

const RHYTHM: { time: string; title: string; body: string }[] = [
  {
    time: "07:45",
    title: "Morning scan",
    body: "If enabled, the daily-alpha job wakes Archie with your £/day goal, scans the leveraged universe, forecasts the top names, and proposes 1–2 entries inside your rails.",
  },
  {
    time: "Any time",
    title: "Validate an idea",
    body: "Open the Research Desk and file an Analysis Request on a holding or a new ticker — get an evidence-backed verdict before you act.",
  },
  {
    time: "12:00",
    title: "Midday check",
    body: "A lightweight monitor enforces stop-loss / take-profit / time-stops on any open leveraged trades.",
  },
  {
    time: "15:30",
    title: "End of day",
    body: "Non-overnight leveraged positions are flagged to close before the UK market shuts; the daily goal resets.",
  },
  {
    time: "Sun 10:00",
    title: "Weekly review",
    body: "Archie reviews the week's trades + artifacts and proposes tweaks to your rails — surfaced for your approval, never applied silently.",
  },
]

const TIPS: { label: string; body: string }[] = [
  { label: "Account & currency", body: "Top-bar selectors switch between All / Invest / ISA and GBP / USD everywhere." },
  { label: "Presentation mode", body: "Settings → mask sensitive values to obfuscate real figures when screen-sharing." },
  { label: "Quick chat", body: "⌘/Ctrl + / toggles Archie from any screen." },
  { label: "Refresh vs Run Agent", body: "Refresh re-pulls live data; Run Agent triggers a full analyst reasoning cycle." },
]

function FeatureCard({ icon: Icon, title, what, how }: Feature) {
  return (
    <div className="flex gap-3 rounded-lg border border-border/60 bg-muted/15 p-3.5">
      <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary ring-1 ring-primary/20">
        <Icon className="size-4" strokeWidth={2} />
      </div>
      <div className="min-w-0">
        <p className="text-sm font-semibold">{title}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{what}</p>
        <p className="mt-1.5 text-xs leading-relaxed">
          <span className="font-medium text-foreground/80">How: </span>
          <span className="text-muted-foreground">{how}</span>
        </p>
      </div>
    </div>
  )
}

export function HelpGuide() {
  return (
    <div className="flex flex-col gap-6">
      <SectionCard title="Meet Archie" description="Your personal finance agent and control pane.">
        <div className="flex items-start gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary ring-1 ring-primary/20">
            <Sparkles className="size-4" strokeWidth={2} />
          </div>
          <p className="text-sm leading-relaxed text-muted-foreground">
            MyPF connects your Trading 212 accounts to <span className="font-medium text-foreground">Archie</span>, an
            agent that can read live market data, run quantitative analysis, forecast prices, and act on a schedule —
            all within risk rails you control. Use it to understand your portfolio, validate new ideas, and capture
            small, consistent alpha day to day. Analysis is always separated from execution: nothing trades unless you
            explicitly enable it.
          </p>
        </div>
      </SectionCard>

      <SectionCard title="Your daily rhythm" description="A suggested way to use scheduled jobs + the Research Desk together.">
        <div className="flex flex-col">
          {RHYTHM.map((step, i) => (
            <div key={step.title} className="flex gap-4">
              <div className="flex flex-col items-center">
                <Badge variant="outline" className="w-[88px] justify-center font-mono text-[10px] tabular-nums text-muted-foreground">
                  {step.time}
                </Badge>
                {i < RHYTHM.length - 1 ? <span className="my-1 w-px flex-1 bg-border/60" /> : null}
              </div>
              <div className={i < RHYTHM.length - 1 ? "pb-5" : ""}>
                <p className="text-sm font-semibold">{step.title}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{step.body}</p>
              </div>
            </div>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="What each tab does" description="Every surface in the app and how to use it.">
        <div className="grid gap-2.5 md:grid-cols-2">
          {FEATURES.map((f) => (
            <FeatureCard key={f.title} {...f} />
          ))}
        </div>
      </SectionCard>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <SectionCard title="Tips & controls">
          <div className="flex flex-col gap-3">
            {TIPS.map((t) => (
              <div key={t.label} className="text-sm">
                <span className="font-medium">{t.label}</span>
                <span className="text-muted-foreground"> — {t.body}</span>
              </div>
            ))}
          </div>
        </SectionCard>

        <SectionCard title="Safety">
          <div className="flex items-start gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-positive/10 text-positive ring-1 ring-positive/20">
              <ShieldCheck className="size-4" strokeWidth={2} />
            </div>
            <ul className="flex flex-col gap-1.5 text-xs leading-relaxed text-muted-foreground">
              <li>Analysis is never an executed trade — Archie proposes, you decide.</li>
              <li>Per-position size, total exposure, and max-open rails are always enforced.</li>
              <li>Daily target / loss-limit / max-trades rails stop an over-eager session.</li>
              <li>Live order placement requires switching broker mode to live in Settings.</li>
              <li>Fundamentals — valuation ratios, financial statements, FCF, and earnings dates — are live (via yfinance), so Archie can run valuation analysis.</li>
            </ul>
          </div>
        </SectionCard>
      </div>
    </div>
  )
}
