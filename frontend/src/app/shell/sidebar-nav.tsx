import type { ReactNode } from "react"
import type { LucideIcon } from "lucide-react"

import { cn } from "@/lib/utils"

/**
 * Sidebar building blocks, shared with feature-owned nav sections (e.g.
 * `<ViewsNav/>`) so every entry in the rail looks the same.
 */
export function SidebarNavItem({
  icon: Icon,
  label,
  active = false,
  collapsed = false,
  badge,
  trailing,
  onClick,
}: {
  icon: LucideIcon
  label: string
  active?: boolean
  collapsed?: boolean
  /** Small count pill (shown as a dot when collapsed). */
  badge?: number
  /** Extra right-aligned content when expanded (e.g. a kbd hint). */
  trailing?: ReactNode
  onClick: () => void
}) {
  const showBadge = typeof badge === "number" && badge > 0
  return (
    <button
      type="button"
      onClick={onClick}
      title={collapsed ? label : undefined}
      aria-label={collapsed ? label : undefined}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors",
        collapsed && "justify-center px-0 py-2",
        active
          ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
          : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground",
      )}
    >
      <span className="relative flex shrink-0">
        <Icon className={cn("size-4", active ? "text-foreground" : "text-muted-foreground")} strokeWidth={2} />
        {showBadge && collapsed ? <span className="absolute -right-1 -top-1 size-1.5 rounded-full bg-primary" /> : null}
      </span>
      {!collapsed ? <span className="truncate">{label}</span> : null}
      {!collapsed && showBadge ? (
        <span className="ml-auto rounded-full bg-primary/15 px-1.5 py-px font-mono text-[10px] font-semibold tabular-nums text-primary">
          {badge}
        </span>
      ) : null}
      {!collapsed && !showBadge && trailing ? <span className="ml-auto">{trailing}</span> : null}
    </button>
  )
}

export function SidebarSectionLabel({ children, collapsed = false }: { children: ReactNode; collapsed?: boolean }) {
  if (collapsed) return <div className="mx-auto my-1 h-px w-5 bg-border" />
  return (
    <p className="px-2.5 pb-1 pt-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
      {children}
    </p>
  )
}
