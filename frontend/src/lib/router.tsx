import { useEffect, useState } from 'react'

/**
 * Tiny hash router. Routes are plain paths like `/home`, `/plays/abc123`,
 * `/views/dividends`, `/settings/broker`. Hash-based so the app works from any
 * static host and deep links survive a reload.
 *
 * Top-level route keys (first path segment):
 *   home · archie · plays · activity · views · settings · labs
 */
export type Route = {
  /** Full path, always starting with `/` (e.g. `/plays/abc123`). */
  path: string
  /** Path segments without the leading slash (e.g. `['plays', 'abc123']`). */
  segments: string[]
  /** Query params parsed from `#/path?x=1`. */
  query: URLSearchParams
}

const DEFAULT_PATH = '/home'

function parse(hash: string): Route {
  const raw = hash.replace(/^#/, '') || DEFAULT_PATH
  const [pathPart, queryPart = ''] = raw.split('?')
  const path = pathPart.startsWith('/') ? pathPart : `/${pathPart}`
  const segments = path.split('/').filter(Boolean)
  return { path: segments.length ? path : DEFAULT_PATH, segments: segments.length ? segments : ['home'], query: new URLSearchParams(queryPart) }
}

export function currentRoute(): Route {
  return parse(window.location.hash)
}

export function navigate(path: string, opts: { replace?: boolean } = {}): void {
  const target = `#${path.startsWith('/') ? path : `/${path}`}`
  if (window.location.hash === target) return
  if (opts.replace) {
    window.history.replaceState(null, '', target)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  } else {
    window.location.hash = target
  }
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => currentRoute())
  useEffect(() => {
    const onChange = () => setRoute(currentRoute())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}
