import { useEffect, useState, useSyncExternalStore } from 'react'

import { apiClient } from '@/api/client'

/**
 * Company logos, served by our own backend (`/api/logos/:key`), which fetches
 * each one once from a public logo service and caches it. The browser never
 * contacts a third party. Images are loaded through the API client (so the
 * optional API token applies) and kept as object URLs for the session.
 *
 * Misses are remembered in localStorage for 7 days so a ticker with no logo
 * isn't asked for again on every page.
 */

const MISS_KEY = 'openpf:logo-miss'
const MISS_TTL_MS = 7 * 24 * 3600 * 1000
const MAX_PARALLEL = 6

// ── Setting ──────────────────────────────────────────────────────────────

let enabled: boolean | null = null // null = not loaded yet
let settingsLoad: Promise<void> | null = null
const listeners = new Set<() => void>()

function emit() {
  for (const l of listeners) l()
}

function loadSettings() {
  settingsLoad ??= apiClient
    .get<{ enabled: boolean }>('/logos/settings')
    .then(({ data }) => {
      enabled = Boolean(data.enabled)
    })
    .catch(() => {
      enabled = false // backend down or old: monograms, no requests
      settingsLoad = null
    })
    .finally(emit)
  return settingsLoad
}

function subscribe(l: () => void) {
  listeners.add(l)
  if (enabled === null) void loadSettings()
  return () => listeners.delete(l)
}

/** Whether company logos are on: `null` while loading. */
export function useLogosEnabled(): boolean | null {
  return useSyncExternalStore(subscribe, () => enabled, () => enabled)
}

export async function setLogosEnabled(next: boolean): Promise<boolean> {
  const { data } = await apiClient.put<{ enabled: boolean }>('/logos/settings', { enabled: next })
  enabled = Boolean(data.enabled)
  if (enabled) {
    // Anything that missed while off (or before) gets one fresh try.
    writeMisses({})
    for (const [k, v] of urls) if (v === null) urls.delete(k)
  }
  emit()
  return enabled
}

// ── Negative cache ───────────────────────────────────────────────────────

function readMisses(): Record<string, number> {
  try {
    return JSON.parse(localStorage.getItem(MISS_KEY) || '{}') as Record<string, number>
  } catch {
    return {}
  }
}

function writeMisses(m: Record<string, number>) {
  try {
    localStorage.setItem(MISS_KEY, JSON.stringify(m))
  } catch {
    /* storage unavailable: misses last for this page only (in `urls`) */
  }
}

function missedRecently(key: string) {
  const at = readMisses()[key]
  return typeof at === 'number' && Date.now() - at < MISS_TTL_MS
}

function recordMiss(key: string) {
  const now = Date.now()
  const m = Object.fromEntries(Object.entries(readMisses()).filter(([, t]) => now - t < MISS_TTL_MS))
  m[key] = now
  writeMisses(m)
}

// ── Images ───────────────────────────────────────────────────────────────

export type Logo = { url: string; padded: boolean }

/** key → logo, or null for "no logo" (this session). */
const urls = new Map<string, Logo | null>()
const inflight = new Map<string, Promise<Logo | null>>()
const queue: (() => void)[] = []
let running = 0

function schedule<T>(job: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const run = () => {
      running++
      job()
        .then(resolve, reject)
        .finally(() => {
          running--
          queue.shift()?.()
        })
    }
    if (running < MAX_PARALLEL) run()
    else queue.push(run)
  })
}

/** True when the logo has transparent corners (a bare mark, not a filled
 *  tile): those get padding inside the tile so they don't touch its edge. */
function hasTransparentCorners(url: string): Promise<boolean> {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => {
      try {
        const c = document.createElement('canvas')
        c.width = c.height = 16
        const ctx = c.getContext('2d')
        if (!ctx) return resolve(false)
        ctx.drawImage(img, 0, 0, 16, 16)
        const px = [ctx.getImageData(0, 0, 1, 1), ctx.getImageData(15, 0, 1, 1), ctx.getImageData(0, 15, 1, 1), ctx.getImageData(15, 15, 1, 1)]
        resolve(px.some((p) => p.data[3] < 200))
      } catch {
        resolve(false)
      }
    }
    img.onerror = () => resolve(false)
    img.src = url
  })
}

function fetchLogo(key: string): Promise<Logo | null> {
  const existing = inflight.get(key)
  if (existing) return existing
  const p = schedule(async () => {
    try {
      const { data } = await apiClient.get<Blob>(`/logos/${encodeURIComponent(key)}`, { responseType: 'blob' })
      const url = URL.createObjectURL(data)
      const logo = { url, padded: await hasTransparentCorners(url) }
      urls.set(key, logo)
      return logo
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status === 404 && enabled) recordMiss(key)
      urls.set(key, null)
      return null
    } finally {
      inflight.delete(key)
    }
  })
  inflight.set(key, p)
  return p
}

/**
 * The logo for an equity ticker: `undefined` while loading (show a skeleton),
 * `null` for none (show the monogram), or the image.
 */
export function useLogo(key: string | null | undefined): Logo | null | undefined {
  const on = useLogosEnabled()
  const k = (key || '').trim()
  const initial = () => {
    if (!k || on === false) return null
    if (urls.has(k)) return urls.get(k) ?? null
    if (missedRecently(k)) return null
    return undefined
  }
  const [logo, setLogo] = useState<Logo | null | undefined>(initial)

  useEffect(() => {
    const now = initial()
    setLogo(now)
    if (now !== undefined || on === null) return
    let alive = true
    void fetchLogo(k).then((l) => {
      if (alive) setLogo(l)
    })
    return () => {
      alive = false
    }
  }, [k, on])

  return on === null && logo === undefined ? undefined : logo
}
