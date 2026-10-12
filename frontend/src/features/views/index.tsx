/**
 * Views feature (agent-built pages) — public surface used by the app shell.
 *
 * CONTRACT (owned by the views workstream; the shell only imports these):
 *   <ViewsNav collapsed onNavigate />  — sidebar section: pinned views + "New view"
 *   <ViewsPage route={route} />        — the whole `/views` and `/views/:id` area
 *
 * This file starts as a stub so the shell can compile before the feature lands.
 */
import type { Route } from '@/lib/router'

export function ViewsNav(_props: { collapsed?: boolean; onNavigate?: () => void }) {
  return null
}

export function ViewsPage(_props: { route: Route }) {
  return <p className="text-sm text-muted-foreground">Views are coming soon.</p>
}
