import {
  Activity,
  Bell,
  CalendarClock,
  CreditCard,
  FileText,
  Gauge,
  HelpCircle,
  LayoutDashboard,
  Lightbulb,
  ListChecks,
  MessageSquare,
  Settings,
  Telescope,
  type LucideIcon,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type SectionKey =
  | "overview"
  | "attention"
  | "chat"
  | "execution"
  | "leveraged"
  | "jobs"
  | "artifacts"
  | "analysis"
  | "research"
  | "costs"
  | "diagnostics"
  | "help"

type NavItem = { key: SectionKey; label: string; icon: LucideIcon }
type NavGroup = { label: string | null; items: NavItem[] }

const NAV: NavGroup[] = [
  {
    label: null,
    items: [
      { key: "overview", label: "Portfolio", icon: LayoutDashboard },
      { key: "attention", label: "Attention", icon: Bell },
      { key: "chat", label: "Archie", icon: MessageSquare },
    ],
  },
  {
    label: "Trading",
    items: [
      { key: "execution", label: "Execution", icon: ListChecks },
      { key: "leveraged", label: "Leveraged", icon: Gauge },
    ],
  },
  {
    label: "Automation",
    items: [
      { key: "jobs", label: "Scheduled Jobs", icon: CalendarClock },
      { key: "artifacts", label: "Artifacts", icon: FileText },
    ],
  },
  {
    label: "Analysis",
    items: [
      { key: "analysis", label: "Research Desk", icon: Telescope },
      { key: "research", label: "Insights", icon: Lightbulb },
      { key: "costs", label: "Usage", icon: CreditCard },
      { key: "diagnostics", label: "Diagnostics", icon: Activity },
    ],
  },
]

/**
 * The sidebar body — brand, nav, footer (Help/Settings). Rendered both inside
 * the persistent `md+` aside and inside the mobile `Sheet` drawer. `onNavigate`
 * fires after any nav/footer selection so the drawer can close itself.
 */
export function SidebarBody({
  active,
  onSelect,
  onOpenSettings,
  onNavigate,
  pendingIntents,
  activeTheses,
}: {
  active: SectionKey
  onSelect: (key: SectionKey) => void
  onOpenSettings: () => void
  onNavigate?: () => void
  pendingIntents: number
  activeTheses: number
}) {
  function handleSelect(key: SectionKey) {
    onSelect(key)
    onNavigate?.()
  }

  return (
    <>
      <div className="flex items-center gap-2.5 px-2">
        <div className="flex size-8 items-center justify-center rounded-lg bg-primary/15 text-primary ring-1 ring-primary/25">
          <span className="font-mono text-sm font-bold">M</span>
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold tracking-tight">MyPF</p>
          <p className="text-[11px] text-muted-foreground">Portfolio Operator</p>
        </div>
      </div>

      <nav className="flex flex-1 flex-col gap-5 overflow-y-auto">
        {NAV.map((group, gi) => (
          <div key={group.label ?? `g${gi}`} className="flex flex-col gap-1">
            {group.label ? (
              <p className="px-2 pb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
                {group.label}
              </p>
            ) : null}
            {group.items.map((item) => {
              const Icon = item.icon
              const isActive = active === item.key
              return (
                <button
                  key={item.key}
                  onClick={() => handleSelect(item.key)}
                  className={cn(
                    "group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground",
                  )}
                >
                  <Icon
                    className={cn("size-4 shrink-0", isActive ? "text-primary" : "text-muted-foreground")}
                    strokeWidth={2}
                  />
                  <span className="truncate">{item.label}</span>
                  {item.key === "execution" && pendingIntents > 0 ? (
                    <span className="ml-auto rounded-full bg-primary/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-primary tabular-nums">
                      {pendingIntents}
                    </span>
                  ) : null}
                </button>
              )
            })}
          </div>
        ))}
      </nav>

      <div className="flex flex-col gap-3 border-t border-border/60 pt-4">
        <div className="flex items-center justify-between px-2 text-[11px] text-muted-foreground">
          <span>{pendingIntents} pending</span>
          <span>{activeTheses} active theses</span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className={cn(
            "justify-start gap-2.5 px-2.5",
            active === "help" && "bg-sidebar-accent text-sidebar-accent-foreground",
          )}
          onClick={() => handleSelect("help")}
        >
          <HelpCircle className="size-4 text-muted-foreground" strokeWidth={2} />
          Help &amp; Guide
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="justify-start gap-2.5 px-2.5"
          onClick={() => {
            onOpenSettings()
            onNavigate?.()
          }}
        >
          <Settings className="size-4 text-muted-foreground" strokeWidth={2} />
          Settings
        </Button>
      </div>
    </>
  )
}

export function AppSidebar(props: {
  active: SectionKey
  onSelect: (key: SectionKey) => void
  onOpenSettings: () => void
  pendingIntents: number
  activeTheses: number
}) {
  return (
    <aside className="hidden h-full w-60 shrink-0 flex-col gap-6 border-r border-border bg-sidebar px-3 py-5 md:flex">
      <SidebarBody {...props} />
    </aside>
  )
}
