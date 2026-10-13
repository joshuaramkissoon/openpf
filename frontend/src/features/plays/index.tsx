/**
 * Plays feature — public surface used by the app shell.
 *
 * CONTRACT (owned by the plays workstream; the shell only imports these):
 *   <PlaysPage route={route} />   — the whole `/plays` and `/plays/:id` area
 *   <PlaysHomeCard />             — compact summary card for the Home screen
 *
 * Routes handled here:
 *   /plays                 list
 *   /plays?tab=watchlist   watchlist tab
 *   /plays?new=1[&idea=…]  new-play flow (`&job=…` while drafting)
 *   /plays/:id             draft review (status=draft) or detail
 */
import type { Route } from '@/lib/router'
import { PlaysHomeSummary, PlaysList } from './list'
import { NewPlay } from './new-play'
import { PlayPage } from './play-page'

export function PlaysPage({ route }: { route: Route }) {
  const id = route.segments[1]
  if (id) return <PlayPage key={id} id={id} />
  if (route.query.get('new')) {
    const job = route.query.get('job')
    return <NewPlay key={job ?? 'form'} initialIdea={route.query.get('idea') ?? undefined} jobId={job} />
  }
  return <PlaysList tab={route.query.get('tab') === 'watchlist' ? 'watchlist' : 'plays'} />
}

export function PlaysHomeCard() {
  return <PlaysHomeSummary />
}

export { BookByPlay, AllocationBar } from './book-card'
export { OwnerMenu, useBook, reloadBook, holdingKey, Swatch } from './book'
export { SortHoldingsSheet } from './suggestions'
export type { Book, BookGroup, BookHolding, ThemeRisk } from './types'
