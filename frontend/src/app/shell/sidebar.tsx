import {
  Activity,
  Eye,
  EyeOff,
  FlaskConical,
  Home,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  Shield,
  Target,
  Workflow,
  type LucideIcon,
} from "lucide-react"
import { ArchieIcon } from "@/components/kit"

import { LogoCredit } from "@/features/settings/logos-setting"
import { ViewsNav } from "@/features/views"
import { useLogosEnabled } from "@/lib/logos"
import { privacyModeDescription, privacyModeLabel, type PrivacyMode } from "@/lib/privacy"
import { cn } from "@/lib/utils"

import type { BrokerMode } from "../hooks/use-app-config"
import { BrokerBadge } from "./broker-badge"
import { SidebarNavItem, SidebarSectionLabel } from "./sidebar-nav"

const PRIVACY_ICON: Record<PrivacyMode, LucideIcon> = { off: Eye, scramble: EyeOff, blur: Shield }

const PRIMARY: { key: string; label: string; icon: LucideIcon; path: string }[] = [
  { key: "home", label: "Home", icon: Home, path: "/home" },
  { key: "archie", label: "Archie", icon: ArchieIcon, path: "/archie" },
  { key: "plays", label: "Plays", icon: Target, path: "/plays" },
  { key: "recipes", label: "Recipes", icon: Workflow, path: "/recipes" },
  { key: "activity", label: "Activity", icon: Activity, path: "/activity" },
]

export type SidebarProps = {
  /** First route segment, e.g. `home`. */
  section: string
  path: string
  onNavigate: (path: string) => void
  onOpenPalette: () => void
  pendingProposals: number
  leveragedEnabled: boolean
  brokerMode: BrokerMode | null
  privacyMode: PrivacyMode
  onCyclePrivacyMode: () => void
  collapsed?: boolean
  onToggleCollapsed?: () => void
  /** Fired after any selection (the mobile sheet closes itself). */
  onAfterNavigate?: () => void
}

export function SidebarBody({
  section,
  path,
  onNavigate,
  onOpenPalette,
  pendingProposals,
  leveragedEnabled,
  brokerMode,
  privacyMode,
  onCyclePrivacyMode,
  collapsed = false,
  onToggleCollapsed,
  onAfterNavigate,
}: SidebarProps) {
  const PrivacyIcon = PRIVACY_ICON[privacyMode]
  const go = (p: string) => {
    onNavigate(p)
    onAfterNavigate?.()
  }

  return (
    <>
      <div className={cn("flex items-center gap-2.5", collapsed ? "justify-center" : "px-2")}>
        <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <span className="font-mono text-[11px] font-bold tracking-tight">pf</span>
        </span>
        {!collapsed ? <span className="text-sm font-semibold tracking-tight">OpenPF</span> : null}
        {!collapsed && onToggleCollapsed ? (
          <button
            type="button"
            onClick={onToggleCollapsed}
            title="Collapse sidebar (press /)"
            aria-label="Collapse sidebar"
            className="ml-auto rounded-md p-1 text-muted-foreground transition-colors hover:bg-sidebar-accent/50 hover:text-foreground"
          >
            <PanelLeftClose className="size-4" />
          </button>
        ) : null}
      </div>

      <SidebarNavItem
        icon={Search}
        label="Search"
        collapsed={collapsed}
        onClick={() => {
          onOpenPalette()
          onAfterNavigate?.()
        }}
        trailing={
          <kbd className="rounded border border-border/70 px-1 font-mono text-[10px] text-muted-foreground">⌘K</kbd>
        }
      />

      <nav className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto [scrollbar-width:none]">
        <div className="flex flex-col gap-0.5">
          {PRIMARY.map((item) => (
            <SidebarNavItem
              key={item.key}
              icon={item.icon}
              label={item.label}
              active={section === item.key}
              collapsed={collapsed}
              badge={item.key === "home" ? pendingProposals : undefined}
              onClick={() => go(item.path)}
            />
          ))}
        </div>

        <ViewsNav collapsed={collapsed} onNavigate={onAfterNavigate} />

        {leveragedEnabled ? (
          <div className="flex flex-col gap-0.5">
            <SidebarSectionLabel collapsed={collapsed}>Labs</SidebarSectionLabel>
            <SidebarNavItem
              icon={FlaskConical}
              label="Leveraged"
              active={path.startsWith("/labs/leveraged")}
              collapsed={collapsed}
              onClick={() => go("/labs/leveraged")}
            />
          </div>
        ) : null}
      </nav>

      <div className="flex flex-col gap-0.5">
        <SidebarLogoCredit collapsed={collapsed} />
        <BrokerBadge mode={brokerMode} collapsed={collapsed} className="mb-2" />
        <SidebarNavItem
          icon={PrivacyIcon}
          label={privacyMode === "off" ? "Privacy" : `Privacy · ${privacyModeLabel(privacyMode)}`}
          active={privacyMode !== "off"}
          collapsed={collapsed}
          onClick={onCyclePrivacyMode}
          trailing={<kbd className="rounded border border-border/70 px-1 font-mono text-[10px] text-muted-foreground">P</kbd>}
        />
        <span className="sr-only">{privacyModeDescription(privacyMode)}</span>
        <SidebarNavItem
          icon={Settings}
          label="Settings"
          active={section === "settings"}
          collapsed={collapsed}
          onClick={() => go("/settings/general")}
        />
        {collapsed && onToggleCollapsed ? (
          <SidebarNavItem icon={PanelLeftOpen} label="Expand sidebar" collapsed onClick={onToggleCollapsed} />
        ) : null}
      </div>
    </>
  )
}

/** The logo service's free tier asks for a visible link wherever logos show. */
function SidebarLogoCredit({ collapsed }: { collapsed?: boolean }) {
  const on = useLogosEnabled()
  if (!on || collapsed) return null
  return <LogoCredit className="mb-1.5 px-2.5 text-[12px]" />
}

export function AppSidebar(props: SidebarProps) {
  const collapsed = props.collapsed ?? false
  return (
    <aside
      className={cn(
        "hidden h-full shrink-0 flex-col gap-4 border-r border-sidebar-border bg-sidebar py-4 transition-[width] duration-200 md:flex",
        collapsed ? "w-[3.75rem] px-2" : "w-56 px-2.5",
      )}
    >
      <SidebarBody {...props} />
    </aside>
  )
}
