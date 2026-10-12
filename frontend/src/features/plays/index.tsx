/**
 * Plays feature — public surface used by the app shell.
 *
 * CONTRACT (owned by the plays workstream; the shell only imports these):
 *   <PlaysPage route={route} />   — the whole `/plays` and `/plays/:id` area
 *   <PlaysHomeCard />             — compact summary card for the Home screen
 *
 * This file starts as a stub so the shell can compile before the feature lands.
 */
import type { Route } from '@/lib/router'

export function PlaysPage(_props: { route: Route }) {
  return <p className="text-sm text-muted-foreground">Plays are coming soon.</p>
}

export function PlaysHomeCard() {
  return null
}
