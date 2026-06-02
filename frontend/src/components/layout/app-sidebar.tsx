import {
  Activity,
  Bell,
  CalendarClock,
  CreditCard,
  Eye,
  EyeOff,
  FileText,
  Gauge,
  HelpCircle,
  LayoutDashboard,
  Lightbulb,
  ListChecks,
  MessageSquare,
  Receipt,
  PanelLeftClose,
  Settings,
  Shield,
  Star,
  Telescope,
  TrendingUp,
  type LucideIcon,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  privacyModeDescription,
  privacyModeLabel,
  type PrivacyMode,
} from "@/lib/privacy"

const PRIVACY_ICON: Record<PrivacyMode, LucideIcon> = {
  off: Eye,
  scramble: EyeOff,
  blur: Shield,
}

export type SectionKey =
  | "overview"
  | "attention"
  | "watchlist"
  | "chat"
  | "execution"
  | "orders"
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
      { key: "watchlist", label: "Watchlist", icon: Star },
      { key: "chat", label: "Archie", icon: MessageSquare },
    ],
  },
  {
    label: "Trading",
    items: [
      { key: "execution", label: "Execution", icon: ListChecks },
      { key: "orders", label: "Orders", icon: Receipt },
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
 *
 * When `collapsed` is true the rail shrinks to an icon-only strip (labels and
 * group headings hide; tooltips carry the names). `onToggleCollapsed` renders
 * the collapse/expand control — omit it (e.g. in the mobile drawer) to hide it.
 */
export function SidebarBody({
  active,
  onSelect,
  onOpenSettings,
  onNavigate,
  pendingIntents,
  privacyMode,
  onCyclePrivacyMode,
  collapsed = false,
  onToggleCollapsed,
}: {
  active: SectionKey
  onSelect: (key: SectionKey) => void
  onOpenSettings: () => void
  onNavigate?: () => void
  pendingIntents: number
  privacyMode: PrivacyMode
  onCyclePrivacyMode: () => void
  collapsed?: boolean
  onToggleCollapsed?: () => void
}) {
  const PrivacyIcon = PRIVACY_ICON[privacyMode]
  function handleSelect(key: SectionKey) {
    onSelect(key)
    onNavigate?.()
  }

  return (
    <>
      {onToggleCollapsed ? (
        <button
          type="button"
          onClick={onToggleCollapsed}
          title={collapsed ? "Expand sidebar (press /)" : "Collapse sidebar (press /)"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={cn(
            "group flex w-full items-center gap-2.5 rounded-lg py-1.5 text-left transition-colors hover:bg-sidebar-accent/50",
            collapsed ? "justify-center px-0" : "px-2",
          )}
        >
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary ring-1 ring-primary/25">
            <TrendingUp className="size-4" strokeWidth={2.5} />
          </span>
          {!collapsed ? (
            <>
              <span className="leading-tight">
                <span className="block text-sm font-semibold tracking-tight text-foreground">OpenPF</span>
                <span className="block text-[11px] text-muted-foreground">Portfolio Operator</span>
              </span>
              <PanelLeftClose className="ml-auto size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground" />
            </>
          ) : null}
        </button>
      ) : (
        <div className="flex items-center gap-2.5 px-2">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary ring-1 ring-primary/25">
            <TrendingUp className="size-4" strokeWidth={2.5} />
          </span>
          <span className="leading-tight">
            <span className="block text-sm font-semibold tracking-tight text-foreground">OpenPF</span>
            <span className="block text-[11px] text-muted-foreground">Portfolio Operator</span>
          </span>
        </div>
      )}

      <nav className="flex flex-1 flex-col gap-5 overflow-y-auto">
        {NAV.map((group, gi) => (
          <div key={group.label ?? `g${gi}`} className="flex flex-col gap-1">
            {group.label && !collapsed ? (
              <p className="px-2 pb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
                {group.label}
              </p>
            ) : null}
            {group.items.map((item) => {
              const Icon = item.icon
              const isActive = active === item.key
              const showBadge = item.key === "execution" && pendingIntents > 0
              return (
                <button
                  key={item.key}
                  onClick={() => handleSelect(item.key)}
                  title={collapsed ? item.label : undefined}
                  aria-label={collapsed ? item.label : undefined}
                  className={cn(
                    "group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
                    collapsed && "justify-center px-0",
                    isActive
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground",
                  )}
                >
                  <span className="relative flex shrink-0">
                    <Icon
                      className={cn("size-4", isActive ? "text-primary" : "text-muted-foreground")}
                      strokeWidth={2}
                    />
                    {showBadge && collapsed ? (
                      <span className="absolute -right-1 -top-1 size-1.5 rounded-full bg-primary" />
                    ) : null}
                  </span>
                  {!collapsed ? <span className="truncate">{item.label}</span> : null}
                  {showBadge && !collapsed ? (
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
        <button
          type="button"
          onClick={onCyclePrivacyMode}
          title={`Privacy: ${privacyModeLabel(privacyMode)} — ${privacyModeDescription(privacyMode)}. Click or press P to cycle.`}
          aria-label={`Privacy mode: ${privacyModeLabel(privacyMode)}. Click to cycle.`}
          className={cn(
            "group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
            collapsed && "justify-center px-0",
            privacyMode === "off"
              ? "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground"
              : "bg-sidebar-accent/60 text-sidebar-accent-foreground hover:bg-sidebar-accent",
          )}
        >
          <PrivacyIcon
            className={cn("size-4 shrink-0", privacyMode === "off" ? "text-muted-foreground" : "text-primary")}
            strokeWidth={2}
          />
          {!collapsed ? <span className="truncate">Privacy</span> : null}
          {!collapsed ? (
            <span
              className={cn(
                "ml-auto rounded-full px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide tabular-nums",
                privacyMode === "off"
                  ? "bg-muted text-muted-foreground"
                  : "bg-primary/15 text-primary",
              )}
            >
              {privacyModeLabel(privacyMode)}
            </span>
          ) : null}
        </button>
        <Button
          variant="ghost"
          size="sm"
          title={collapsed ? "Help & Guide" : undefined}
          className={cn(
            "justify-start gap-2.5 px-2.5",
            collapsed && "justify-center px-0",
            active === "help" && "bg-sidebar-accent text-sidebar-accent-foreground",
          )}
          onClick={() => handleSelect("help")}
        >
          <HelpCircle className="size-4 text-muted-foreground" strokeWidth={2} />
          {!collapsed ? <span>Help &amp; Guide</span> : null}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          title={collapsed ? "Settings" : undefined}
          className={cn("justify-start gap-2.5 px-2.5", collapsed && "justify-center px-0")}
          onClick={() => {
            onOpenSettings()
            onNavigate?.()
          }}
        >
          <Settings className="size-4 text-muted-foreground" strokeWidth={2} />
          {!collapsed ? <span>Settings</span> : null}
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
  privacyMode: PrivacyMode
  onCyclePrivacyMode: () => void
  collapsed?: boolean
  onToggleCollapsed?: () => void
}) {
  const collapsed = props.collapsed ?? false
  return (
    <aside
      className={cn(
        "hidden h-full shrink-0 flex-col gap-6 border-r border-border bg-sidebar py-5 transition-[width] duration-200 md:flex",
        collapsed ? "w-[4.25rem] px-2" : "w-60 px-3",
      )}
    >
      <SidebarBody {...props} />
    </aside>
  )
}
