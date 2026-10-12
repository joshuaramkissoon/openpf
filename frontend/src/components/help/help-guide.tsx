import { Activity, Home, LayoutGrid, MessageSquare, Settings, Target, type LucideIcon } from "lucide-react"

import { Section } from "@/components/kit"

type Feature = { icon: LucideIcon; title: string; what: string }

const FEATURES: Feature[] = [
  {
    icon: Home,
    title: "Home",
    what: "Your total value, today's move, and the few decisions waiting for you — proposals to approve and signals worth a look. Click any holding for its chart, signals and a deep analysis.",
  },
  {
    icon: MessageSquare,
    title: "Archie",
    what: "Chat with the agent. It reads live prices, fundamentals, forecasts and your book. Anything it wants to trade comes back as a proposal — it can't place orders on its own.",
  },
  {
    icon: Target,
    title: "Plays",
    what: "Theme bets: a thesis turned into a basket, sizing, a staged entry and kill criteria, monitored on a schedule. Your watchlist lives here too.",
  },
  {
    icon: LayoutGrid,
    title: "Views",
    what: "Pages Archie builds on request (\"show me dividend income by month\"). Pin the ones you use to the sidebar.",
  },
  {
    icon: Activity,
    title: "Activity",
    what: "Every proposal, broker order, automation run and audit event. Automations (morning brief, weekly review) are set up here.",
  },
  {
    icon: Settings,
    title: "Settings",
    what: "Broker keys, risk rails, notifications, usage, diagnostics and Labs features.",
  },
]

const TIPS: { keys: string; body: string }[] = [
  { keys: "⌘K", body: "Search instruments, jump to a page, or ask Archie anything." },
  { keys: "P", body: "Cycle privacy: Off → Blur → Scramble. Blur hides real figures; Scramble shows plausible fake ones." },
  { keys: "/", body: "Collapse or expand the sidebar." },
  { keys: "⌘/", body: "Jump to Archie and back." },
]

export function HelpGuide() {
  return (
    <div className="max-w-3xl space-y-10">
      <Section title="How OpenPF works">
        <p className="text-sm leading-relaxed text-muted-foreground">
          OpenPF connects your Trading 212 Invest and Stocks ISA accounts to{" "}
          <span className="font-medium text-foreground">Archie</span>, an agent that reads live market data, analyses
          your portfolio and acts on a schedule. Archie can only propose trades. Every order needs your approval and
          has to pass the risk rails you set in Settings.
        </p>
      </Section>

      <Section title="Where things live">
        <div className="divide-y divide-border/60">
          {FEATURES.map(({ icon: Icon, title, what }) => (
            <div key={title} className="flex gap-3 py-3 first:pt-0">
              <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="text-sm font-medium">{title}</p>
                <p className="mt-0.5 text-sm leading-relaxed text-muted-foreground">{what}</p>
              </div>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Keyboard">
        <ul className="space-y-2">
          {TIPS.map((t) => (
            <li key={t.keys} className="flex items-baseline gap-3 text-sm">
              <kbd className="w-10 shrink-0 rounded border border-border px-1.5 py-0.5 text-center font-mono text-[11px] text-muted-foreground">
                {t.keys}
              </kbd>
              <span className="text-muted-foreground">{t.body}</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  )
}
