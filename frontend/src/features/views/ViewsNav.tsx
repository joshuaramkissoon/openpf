import { useEffect } from 'react'
import { LoaderCircle, Plus } from 'lucide-react'

import { SidebarNavItem, SidebarSectionLabel } from '@/app/shell/sidebar-nav'
import { navigate, useRoute } from '@/lib/router'
import { viewIcon } from './view-icons'
import { refreshViews, useViews } from './views-store'

/** Sidebar section: pinned views + "New view", built from the shell's nav primitives. */
export function ViewsNav({ collapsed, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const { views } = useViews()
  const route = useRoute()
  const pinned = views.filter((v) => v.pinned)
  const building = views.some((v) => v.current_version === 0)

  // A view mid-build has no title yet — poll until it lands.
  useEffect(() => {
    if (!building) return
    const t = window.setInterval(() => void refreshViews(), 3000)
    return () => window.clearInterval(t)
  }, [building])

  const go = (path: string) => {
    navigate(path)
    onNavigate?.()
  }

  return (
    <div className="flex flex-col gap-0.5">
      <SidebarSectionLabel collapsed={collapsed}>Views</SidebarSectionLabel>
      {pinned.map((v) => {
        const isBuilding = v.current_version === 0
        return (
          <SidebarNavItem
            key={v.id}
            icon={isBuilding ? LoaderCircle : viewIcon(v.icon)}
            label={isBuilding ? 'Building…' : v.title}
            active={route.path === `/views/${v.id}`}
            collapsed={collapsed}
            onClick={() => go(`/views/${v.id}`)}
          />
        )
      })}
      <SidebarNavItem
        icon={Plus}
        label="New view"
        active={route.path === '/views/new' || route.path === '/views'}
        collapsed={collapsed}
        onClick={() => go(pinned.length ? '/views/new' : '/views')}
      />
    </div>
  )
}
