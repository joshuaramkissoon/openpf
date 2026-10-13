import { useEffect, useMemo, useRef, useState } from 'react'
import geistMonoUrl from '@fontsource-variable/geist-mono/files/geist-mono-latin-wght-normal.woff2?url'
import geistUrl from '@fontsource-variable/geist/files/geist-latin-wght-normal.woff2?url'

import { getViewData, putViewState } from '@/api/views'
import { useAppActions } from '@/lib/app-actions'
import type { PrivacyMode } from '@/lib/privacy'
import { cn } from '@/lib/utils'
import runtimeCss from './runtime/runtime.css?raw'
import runtimeJs from './runtime/runtime.js?raw'

/**
 * Sandboxed renderer for an agent-built view.
 *
 * Guardrails (all enforced by the browser, not by trusting the view):
 * - `sandbox="allow-scripts"` only → opaque origin: no access to the app's DOM,
 *   cookies, storage or auth token; no forms, popups or top navigation.
 * - CSP `default-src 'none'` → no fetch/XHR/WebSocket/images/fonts from anywhere.
 * - The only way out is postMessage to this component, which accepts messages
 *   solely from this iframe's window, whitelists methods, validates arguments,
 *   rate-limits data calls and fetches through the normal (authenticated) API.
 */

const CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  'font-src data:',
  "form-action 'none'",
  "base-uri 'none'",
].join('; ')

const THEME_VARS = [
  'background', 'foreground', 'card', 'card-foreground', 'muted', 'muted-foreground', 'accent', 'border', 'input',
  'primary', 'primary-foreground', 'positive', 'negative', 'warning', 'destructive', 'radius',
  'chart-1', 'chart-2', 'chart-3', 'chart-4', 'chart-5', 'chart-6', 'chart-7', 'chart-8',
]

const APP_PATH = /^\/(home|archie|plays|activity|views|settings|labs)(\/[A-Za-z0-9_-]+){0,2}$/
const TICKER = /^[A-Za-z0-9.\-^=_]{1,24}$/
const DATA_WINDOW_MS = 10_000
const DATA_MAX_PER_WINDOW = 60
const MAX_PARAMS_BYTES = 4096

export type ViewRuntimeError = { message: string; stack?: string }

function readThemeMode(): 'light' | 'dark' {
  return document.documentElement.classList.contains('light') ? 'light' : 'dark'
}

function readThemeVars(): Record<string, string> {
  const cs = getComputedStyle(document.documentElement)
  const out: Record<string, string> = {}
  for (const name of THEME_VARS) {
    const v = cs.getPropertyValue(`--${name}`).trim()
    if (v) out[`--${name}`] = v
  }
  return out
}

// Fonts are inlined as data: URIs (the CSP allows nothing else). Fetched once per session.
let fontCssPromise: Promise<string> | null = null
function loadFontCss(): Promise<string> {
  if (!fontCssPromise) {
    const toDataUrl = async (url: string) => {
      const blob = await (await fetch(url)).blob()
      return await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result).replace(/^data:[^;]*;/, 'data:font/woff2;'))
        reader.onerror = () => reject(reader.error)
        reader.readAsDataURL(blob)
      })
    }
    fontCssPromise = Promise.all([toDataUrl(geistUrl), toDataUrl(geistMonoUrl)])
      .then(
        ([sans, mono]) =>
          `@font-face{font-family:'Geist Variable';font-style:normal;font-display:swap;font-weight:100 900;src:url(${sans}) format('woff2-variations')}` +
          `@font-face{font-family:'Geist Mono Variable';font-style:normal;font-display:swap;font-weight:100 900;src:url(${mono}) format('woff2-variations')}`,
      )
      .catch(() => '')
  }
  return fontCssPromise
}

/** JSON safe to embed inside an inline <script>. */
function scriptJson(value: unknown): string {
  return JSON.stringify(value).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029')
}

export function buildSrcDoc(opts: { html: string; fontCss: string; init: Record<string, unknown> }): string {
  return (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
    `<meta http-equiv="Content-Security-Policy" content="${CSP}">` +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    `<style>${opts.fontCss}</style><style>${runtimeCss}</style>` +
    `<script>window.__OPENPF_INIT__=${scriptJson(opts.init)}</script>` +
    `<script>${runtimeJs}</script>` +
    `</head><body>${opts.html}</body></html>`
  )
}

export function ViewFrame({
  viewId,
  html,
  version,
  state,
  currency,
  privacy,
  onFix,
  onHealthy,
  onBroken,
  className,
}: {
  viewId: string
  html: string
  version: number
  state: Record<string, unknown>
  currency: 'GBP' | 'USD'
  privacy: PrivacyMode
  onFix?: (error: ViewRuntimeError) => void
  /** Called once per document when it rendered and stayed error-free for a moment. */
  onHealthy?: (version: number) => void
  /** Called when the document errors or tries to leave its frame. */
  onBroken?: (kind: 'error' | 'escaped') => void
  className?: string
}) {
  const actions = useAppActions()
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [fontCss, setFontCss] = useState<string | null>(null)
  const [height, setHeight] = useState(480)
  const [sized, setSized] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [escaped, setEscaped] = useState(false)
  // Latest values for the message handler without re-binding it.
  const live = useRef({ actions, onFix, viewId, onBroken })
  live.current = { actions, onFix, viewId, onBroken }
  const [errored, setErrored] = useState(false)
  // State is captured once per render of the document; later writes go to the server.
  const stateRef = useRef(state)
  const currencyRef = useRef(currency)
  currencyRef.current = currency

  useEffect(() => {
    let alive = true
    void loadFontCss().then((css) => alive && setFontCss(css))
    return () => {
      alive = false
    }
  }, [])

  const srcDoc = useMemo(() => {
    if (fontCss == null) return null
    return buildSrcDoc({ html, fontCss, init: { viewId, version, currency, privacy, state: stateRef.current, theme: readThemeVars(), mode: readThemeMode() } })
    // reloadKey forces a fresh document on "Reload".
  }, [html, fontCss, viewId, version, currency, privacy, reloadKey])

  useEffect(() => {
    setSized(false)
    setEscaped(false)
    setErrored(false)
  }, [srcDoc])

  // Healthy = sized, no error, still in its document after a short settle.
  const healthyRef = useRef(onHealthy)
  healthyRef.current = onHealthy
  useEffect(() => {
    if (!sized || errored || escaped) return
    const t = window.setTimeout(() => healthyRef.current?.(version), 2500)
    return () => window.clearTimeout(t)
  }, [sized, errored, escaped, version])

  // Bridge.
  useEffect(() => {
    const frame = iframeRef.current
    if (!frame) return
    let windowStart = Date.now()
    let windowCount = 0

    const reply = (id: unknown, ok: boolean, payload: unknown) => {
      frame.contentWindow?.postMessage({ __openpf: 1, id, ok, [ok ? 'result' : 'error']: payload }, '*')
    }

    const onMessage = async (event: MessageEvent) => {
      if (!frame.contentWindow || event.source !== frame.contentWindow) return
      const msg = event.data as { __openpf?: number; id?: number; method?: string; args?: unknown[] }
      if (!msg || msg.__openpf !== 1 || typeof msg.method !== 'string') return
      const args = Array.isArray(msg.args) ? msg.args : []
      const { actions: act, onFix: fix, viewId: vid } = live.current

      switch (msg.method) {
        case 'data': {
          const [source, params] = args as [unknown, unknown]
          const now = Date.now()
          if (now - windowStart > DATA_WINDOW_MS) {
            windowStart = now
            windowCount = 0
          }
          if (++windowCount > DATA_MAX_PER_WINDOW) return reply(msg.id, false, 'Too many data requests — slow down.')
          if (typeof source !== 'string' || !/^[a-z_]{1,32}$/.test(source)) return reply(msg.id, false, 'Invalid source')
          const p = params && typeof params === 'object' && !Array.isArray(params) ? (params as Record<string, unknown>) : {}
          if (JSON.stringify(p).length > MAX_PARAMS_BYTES) return reply(msg.id, false, 'Params too large')
          try {
            const merged = 'currency' in p ? p : { ...p, currency: currencyRef.current }
            reply(msg.id, true, await getViewData(source, merged))
          } catch (err: unknown) {
            const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
            reply(msg.id, false, typeof detail === 'string' ? detail : `Could not load ${source}`)
          }
          return
        }
        case 'state.set': {
          const [next] = args
          if (!next || typeof next !== 'object' || Array.isArray(next)) return reply(msg.id, false, 'State must be an object')
          try {
            stateRef.current = next as Record<string, unknown>
            await putViewState(vid, stateRef.current)
            reply(msg.id, true, true)
          } catch {
            reply(msg.id, false, 'Could not save')
          }
          return
        }
        case 'ask':
          if (typeof args[0] === 'string' && args[0].trim()) act.askArchie(args[0].slice(0, 4000), { send: false })
          return
        case 'openTicker':
          if (typeof args[0] === 'string' && TICKER.test(args[0])) act.openTicker(args[0])
          return
        case 'navigate':
          if (typeof args[0] === 'string' && APP_PATH.test(args[0])) act.navigate(args[0])
          return
        case 'resize': {
          const h = Number(args[0])
          if (Number.isFinite(h)) {
            setHeight(Math.max(120, Math.min(20000, Math.ceil(h))))
            setSized(true)
          }
          return
        }
        case 'fix': {
          const e = args[0] as ViewRuntimeError | undefined
          if (e && typeof e.message === 'string') fix?.({ message: e.message.slice(0, 600), stack: typeof e.stack === 'string' ? e.stack.slice(0, 1500) : undefined })
          return
        }
        case 'reload':
          setReloadKey((k) => k + 1)
          return
        case 'error':
          setSized(true)
          setErrored(true)
          live.current.onBroken?.('error')
          return
        default:
          return
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [srcDoc])

  // Live theme sync (the app may toggle classes on <html>).
  useEffect(() => {
    const obs = new MutationObserver(() => {
      iframeRef.current?.contentWindow?.postMessage({ __openpf: 1, event: 'theme', vars: readThemeVars(), mode: readThemeMode() }, '*')
    })
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] })
    return () => obs.disconnect()
  }, [])

  // A sandboxed frame can still navigate itself; a second load means the view
  // left its document. Tear it down rather than show foreign content.
  const loads = useRef(0)
  useEffect(() => {
    loads.current = 0
  }, [srcDoc])
  const onLoad = () => {
    loads.current += 1
    if (loads.current > 1 && !escaped) {
      setEscaped(true)
      live.current.onBroken?.('escaped')
    }
  }

  if (escaped) {
    // The parent page renders the recovery actions (reload / restore last good version).
    return null
  }

  return (
    <div className={cn('relative', className)}>
      {!sized ? (
        <div className="pointer-events-none absolute inset-x-0 top-0 grid gap-4 sm:grid-cols-3" aria-hidden>
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-[104px] animate-pulse rounded-xl bg-muted/60" />
          ))}
          <div className="h-[260px] animate-pulse rounded-xl bg-muted/60 sm:col-span-3" />
        </div>
      ) : null}
      {srcDoc != null ? (
        <iframe
          key={reloadKey}
          ref={iframeRef}
          title={`View ${viewId}`}
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
          srcDoc={srcDoc}
          onLoad={onLoad}
          className={cn('block w-full border-0 bg-transparent transition-opacity duration-200', sized ? 'opacity-100' : 'opacity-0')}
          style={{ height }}
        />
      ) : null}
    </div>
  )
}
