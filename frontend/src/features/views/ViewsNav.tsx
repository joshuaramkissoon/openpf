import { type ReactNode, useEffect } from 'react'
import { LoaderCircle, Plus } from 'lucide-react'

import { navigate, useRoute } from '@/lib/router'
import { cn } from '@/lib/utils'
import { viewIcon } from './view-icons'
import { refreshViews, useViews } from './views-store'

/** Sidebar section: pinned views + "New view". Styled to match the shell's nav items. */
export function ViewsNav({ collapsed, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const { views } = useViews()
  const route = useRoute()
  const pinned = views.filter((v) => v.pinned)
  const building = views.some((v) => v.current_version === 0)

  // A view mid-build has no title yet — poll quickly until it lands. Archie can
  // also queue views from chat at any time, so check back periodically too.
  useEffect(() => {
    const t = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refreshViews()
    }, building ? 3000 : 15000)
    return () => window.clearInterval(t)
  }, [building])

  const go = (path: string) => {
    navigate(path)
    onNavigate?.()
  }

  const item = (key: string, path: string, label: string, icon: ReactNode, active: boolean, quiet = false) => (
    <button
      key={key}
      onClick={() => go(path)}
      title={collapsed ? label : undefined}
      aria-label={collapsed ? label : undefined}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors',
        collapsed && 'justify-center px-0',
        active
          ? 'bg-sidebar-accent text-sidebar-accent-foreground'
          : cn('hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground', quiet ? 'text-muted-foreground/80' : 'text-muted-foreground'),
      )}
    >
      <span className={cn('flex shrink-0', active ? 'text-primary' : 'text-muted-foreground')}>{icon}</span>
      {!collapsed ? <span className="truncate">{label}</span> : null}
    </button>
  )

  return (
    <div className="flex flex-col gap-1">
      {!collapsed ? (
        <button
          onClick={() => go('/views')}
          className="px-2 pb-1 text-left text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70 transition-colors hover:text-muted-foreground"
        >
          Views
        </button>
      ) : null}
      {pinned.map((v) => {
        const Icon = viewIcon(v.icon)
        const isBuilding = v.current_version === 0
        return item(
          v.id,
          `/views/${v.id}`,
          isBuilding ? 'Building…' : v.title,
          isBuilding ? <LoaderCircle className="size-4 animate-spin" strokeWidth={2} /> : <Icon className="size-4" strokeWidth={2} />,
          route.path === `/views/${v.id}`,
        )
      })}
      {item('__new', '/views/new', 'New view', <Plus className="size-4" strokeWidth={2} />, route.path === '/views/new', true)}
    </div>
  )
}
