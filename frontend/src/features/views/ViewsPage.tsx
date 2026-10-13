import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  ArrowRight,
  ArrowUp,
  Check,
  History,
  LayoutDashboard,
  LoaderCircle,
  Pin,
  PinOff,
  Plus,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
  TriangleAlert,
} from 'lucide-react'

import {
  deleteView,
  generateView,
  getView,
  getViewHtml,
  getViewJob,
  getViewState,
  installPreset,
  listPresets,
  markViewGood,
  patchView,
  retryView,
  revertView,
} from '@/api/views'
import { ViewSpecRenderer, VizBlock, WIDGET_PRESETS } from '@/components/viz'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { toastApiError } from '@/lib/api-error'
import { useDisplayPrefs } from '@/lib/display-prefs'
import { usePrivacyMode } from '@/lib/privacy'
import { navigate, type Route } from '@/lib/router'
import { cn } from '@/lib/utils'
import type { ViewDecline, ViewDocument, ViewJob, ViewMeta, ViewPreset, ViewSuggestion } from '@/types/views'
import { PresetGlyph } from './PresetGlyph'
import { ViewFrame, type ViewRuntimeError } from './ViewFrame'
import { viewIcon } from './view-icons'
import { patchCachedView, refreshViews, useViews } from './views-store'

const PLACEHOLDERS = [
  'My holdings as a treemap, coloured by today’s move',
  'Which sectors and countries am I most exposed to?',
  'Dividends by month this year, and who pays the most',
  'How my ISA compares to my Invest account',
  'Biggest winners and losers since I bought',
  'Portfolio value over the last year vs what I put in',
]

function ago(iso: string | null | undefined): string {
  if (!iso) return ''
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  const d = Math.floor(s / 86400)
  return d === 1 ? 'yesterday' : d < 30 ? `${d} days ago` : new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

function declineOf(err: unknown): ViewDecline | null {
  const r = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response
  const d = r?.data?.detail as ViewDecline | undefined
  return r?.status === 422 && d && typeof d === 'object' && 'message' in d ? d : null
}

export function ViewsPage({ route }: { route: Route }) {
  const [, second] = route.segments
  if (!second || second === 'new') return <ViewsHome key={second ?? 'home'} initialPrompt={route.query.get('prompt') ?? ''} focus={second === 'new'} />
  if (second === '_widgets') return <WidgetPreview />
  return <ViewDetail key={second} viewId={second} />
}

/** Dev preview of every widget preset at each supported size (/views/_widgets). */
function WidgetPreview() {
  return (
    <div className="space-y-8">
      <h1 className="text-xl font-semibold">Widget presets</h1>
      {WIDGET_PRESETS.map((w) => (
        <section key={w.id} className="space-y-2">
          <p className="text-sm font-medium">
            {w.title} <span className="text-muted-foreground">· {w.sizes.join(' / ')}</span>
          </p>
          <div className="flex flex-wrap items-start gap-4">
            {w.sizes.map((s) => (
              <VizBlock key={s} spec={w.spec} size={s} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

// ── shared bits ────────────────────────────────────────────────────────
function IconTile({ icon, size = 'md' }: { icon: string; size?: 'sm' | 'md' }) {
  const Icon = viewIcon(icon)
  return (
    <span
      className={cn(
        'flex shrink-0 items-center justify-center rounded-lg bg-primary/12 text-primary ring-1 ring-primary/20',
        size === 'md' ? 'size-9' : 'size-8',
      )}
    >
      <Icon className="size-4" strokeWidth={2} />
    </span>
  )
}

async function addPreset(slug: string) {
  const meta = await installPreset(slug)
  await refreshViews()
  navigate(`/views/${meta.id}`)
}

function SuggestionChips({ items, onPick, busy }: { items: ViewSuggestion[]; onPick: (slug: string) => void; busy: string | null }) {
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((s) => (
        <button
          key={s.slug}
          type="button"
          disabled={!!busy}
          onClick={() => onPick(s.slug)}
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs font-medium transition-colors hover:border-foreground/20 hover:bg-muted disabled:opacity-60"
        >
          {busy === s.slug ? <LoaderCircle className="size-3 animate-spin" /> : <Plus className="size-3" />}
          {s.title}
        </button>
      ))}
    </div>
  )
}

/** Explains why a request can't be a view, with the closest presets and example prompts. */
function DeclineCard({ decline, onExample }: { decline: ViewDecline; onExample: (text: string) => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const pick = async (slug: string) => {
    setBusy(slug)
    try {
      await addPreset(slug)
    } catch (err) {
      toastApiError(err, 'Could not add the preset')
      setBusy(null)
    }
  }
  return (
    <div className="space-y-3 rounded-xl border border-warning/30 bg-warning/5 p-4 text-sm">
      <p className="flex items-start gap-2">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
        <span>{decline.message}</span>
      </p>
      {decline.suggestions.length ? (
        <div className="space-y-1.5 pl-6">
          <p className="text-xs text-muted-foreground">Closest presets</p>
          <SuggestionChips items={decline.suggestions} onPick={pick} busy={busy} />
        </div>
      ) : null}
      {decline.examples.length ? (
        <div className="space-y-1.5 pl-6">
          <p className="text-xs text-muted-foreground">Or try</p>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {decline.examples.slice(0, 3).map((ex) => (
              <button key={ex} type="button" onClick={() => onExample(ex)} className="text-xs text-primary hover:underline">
                “{ex}”
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

// ── composer ───────────────────────────────────────────────────────────
function Composer({ initialPrompt, autoFocus }: { initialPrompt: string; autoFocus?: boolean }) {
  const [prompt, setPrompt] = useState(initialPrompt)
  const [busy, setBusy] = useState(false)
  const [decline, setDecline] = useState<ViewDecline | null>(null)
  const [ph, setPh] = useState(0)
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (autoFocus) ref.current?.focus()
  }, [autoFocus])
  useEffect(() => {
    const t = window.setInterval(() => setPh((i) => (i + 1) % PLACEHOLDERS.length), 4000)
    return () => window.clearInterval(t)
  }, [])

  const submit = async () => {
    const value = prompt.trim()
    if (busy) return
    setBusy(true)
    setDecline(null)
    try {
      const job = await generateView({ prompt: value })
      void refreshViews()
      navigate(`/views/${job.view_id}`)
    } catch (err) {
      const d = declineOf(err)
      if (d) setDecline(d)
      else toastApiError(err, 'Could not start the build')
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
        className="rounded-2xl border border-border bg-card p-2 shadow-sm transition-colors focus-within:border-primary/50 focus-within:ring-3 focus-within:ring-primary/15"
      >
        <textarea
          ref={ref}
          value={prompt}
          onChange={(e) => {
            setPrompt(e.target.value)
            if (decline) setDecline(null)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void submit()
            }
          }}
          rows={2}
          maxLength={4000}
          aria-label="Describe a view"
          placeholder={`Describe a view — e.g. “${PLACEHOLDERS[ph]}”`}
          className="block min-h-16 w-full resize-none bg-transparent px-3 py-2.5 text-[15px] leading-relaxed outline-none placeholder:text-muted-foreground"
        />
        <div className="flex items-center justify-between gap-3 px-2 pb-1 pt-1">
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <ShieldCheck className="size-3.5" /> Read-only · can’t trade · usually ready in seconds
          </span>
          <Button type="submit" disabled={!prompt.trim() || busy}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Sparkles />} Build view
          </Button>
        </div>
      </form>
      {decline ? (
        <DeclineCard
          decline={decline}
          onExample={(t) => {
            setPrompt(t)
            setDecline(null)
            ref.current?.focus()
          }}
        />
      ) : null}
    </div>
  )
}

// ── presets ────────────────────────────────────────────────────────────
function PresetGallery({ refreshKey }: { refreshKey: number }) {
  const { currency } = useDisplayPrefs()
  const [presets, setPresets] = useState<ViewPreset[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    listPresets()
      .then((p) => alive && setPresets(p))
      .catch(() => alive && setPresets([]))
    return () => {
      alive = false
    }
  }, [refreshKey, currency])

  const add = async (slug: string) => {
    setBusy(slug)
    try {
      await addPreset(slug)
    } catch (err) {
      toastApiError(err, 'Could not add the preset')
      setBusy(null)
    }
  }

  if (presets === null) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-[148px] rounded-xl" />
        ))}
      </div>
    )
  }
  if (!presets.length) return null
  const suggested = presets.filter((p) => p.suggested)
  const rest = presets.filter((p) => !p.suggested)

  const card = (p: ViewPreset, featured: boolean) => (
    <button
      key={p.slug}
      type="button"
      disabled={!!busy}
      onClick={() => add(p.slug)}
      className={cn(
        'group flex min-h-[148px] flex-col gap-3 rounded-xl border p-4 text-left transition-colors disabled:opacity-70',
        featured ? 'border-primary/25 bg-primary/[0.04] hover:border-primary/45' : 'border-border bg-card hover:border-foreground/15 hover:bg-accent/40',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <PresetGlyph slug={p.slug} />
        <span className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
          {busy === p.slug ? <LoaderCircle className="size-3.5 animate-spin" /> : p.installed ? <Check className="size-3.5" /> : <Plus className="size-3.5" />}
          {p.installed ? 'Add again' : 'Add'}
        </span>
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">{p.title}</p>
        <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{p.why || p.description}</p>
      </div>
    </button>
  )

  return (
    <section className="space-y-5">
      {suggested.length ? (
        <div className="space-y-3">
          <div>
            <h2 className="text-sm font-semibold">Suggested for your portfolio</h2>
            <p className="text-xs text-muted-foreground">Ready instantly. Customise any of them with a sentence afterwards.</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">{suggested.map((p) => card(p, true))}</div>
        </div>
      ) : null}
      <div className="space-y-3">
        <h2 className="text-sm font-semibold">{suggested.length ? 'More presets' : 'Presets'}</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{rest.map((p) => card(p, false))}</div>
      </div>
    </section>
  )
}

// ── home (/views, /views/new) ──────────────────────────────────────────
function ViewsHome({ initialPrompt, focus }: { initialPrompt: string; focus: boolean }) {
  const { views, loaded } = useViews()
  const ready = views.filter((v) => v.current_version > 0)
  const pending = views.filter((v) => v.current_version === 0)

  return (
    <div className="mx-auto max-w-5xl space-y-10 pt-2">
      <div className="space-y-4">
        <div className="space-y-1">
          <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">Views</p>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">What do you want to see?</h1>
          <p className="text-sm text-muted-foreground">Visual pages on your live portfolio. Start from a preset, or describe one in a sentence.</p>
        </div>
        <Composer initialPrompt={initialPrompt} autoFocus={focus} />
      </div>

      {!loaded ? (
        <div className="grid gap-3 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[112px] rounded-xl" />
          ))}
        </div>
      ) : ready.length || pending.length ? (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold">Your views</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {pending.map((v) => (
              <PendingCard key={v.id} view={v} />
            ))}
            {ready.map((v) => (
              <button
                key={v.id}
                type="button"
                onClick={() => navigate(`/views/${v.id}`)}
                className="group flex min-h-[112px] flex-col gap-3 rounded-xl border border-border bg-card p-4 text-left transition-colors hover:border-foreground/15 hover:bg-accent/40"
              >
                <div className="flex items-start justify-between gap-3">
                  <IconTile icon={v.icon} size="sm" />
                  {v.pinned ? <Pin className="size-3.5 text-muted-foreground/70" /> : null}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold">{v.title}</p>
                  <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{v.description}</p>
                </div>
                <p className="text-[11px] text-muted-foreground/80">updated {ago(v.updated_at)}</p>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <PresetGallery refreshKey={views.length} />
    </div>
  )
}

function PendingCard({ view }: { view: ViewMeta }) {
  const failed = view.build?.status === 'error'
  const [busy, setBusy] = useState(false)
  const prompt = view.build?.prompt ?? view.history[0]?.prompt ?? ''
  const retry = async () => {
    setBusy(true)
    try {
      await retryView(view.id)
      await refreshViews()
      navigate(`/views/${view.id}`)
    } catch (err) {
      toastApiError(err, 'Could not retry')
      setBusy(false)
    }
  }
  const dismiss = async () => {
    try {
      await deleteView(view.id)
      await refreshViews()
    } catch (err) {
      toastApiError(err, 'Could not remove it')
    }
  }
  return (
    <div className={cn('flex min-h-[112px] flex-col gap-2 rounded-xl border p-4', failed ? 'border-negative/25 bg-negative/[0.03]' : 'border-dashed border-border')}>
      <div className="flex items-center gap-2 text-sm font-semibold">
        {failed ? <TriangleAlert className="size-4 text-negative" /> : <LoaderCircle className="size-4 animate-spin text-primary" />}
        <button type="button" onClick={() => navigate(`/views/${view.id}`)} className="truncate hover:underline">
          {failed ? 'Couldn’t build' : 'Building…'} {view.title !== 'New view' ? `“${view.title}”` : ''}
        </button>
      </div>
      <p className="line-clamp-2 text-xs text-muted-foreground">{failed ? view.build?.error : `“${prompt}”`}</p>
      {failed ? (
        <div className="mt-auto flex gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={retry}>
            {busy ? <LoaderCircle className="animate-spin" /> : <RotateCcw />} Retry
          </Button>
          <Button size="sm" variant="ghost" onClick={dismiss}>
            Dismiss
          </Button>
        </div>
      ) : null}
    </div>
  )
}

// ── build progress ─────────────────────────────────────────────────────
function BuildProgress({ job }: { job: ViewJob }) {
  const [now, setNow] = useState(Date.now())
  const [startedAt] = useState(() => Date.now() - job.elapsed_s * 1000)
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(t)
  }, [])
  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000))
  const lines = job.progress.length ? job.progress : [{ at: 0, text: 'Starting…' }]
  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-primary/60" />
            <span className="relative inline-flex size-2 rounded-full bg-primary" />
          </span>
          <p className="text-sm font-semibold">{job.kind === 'create' ? 'Building your view' : job.kind === 'fix' ? 'Fixing this view' : 'Updating this view'}</p>
        </div>
        <span className="font-mono text-xs text-muted-foreground tabular-nums">{elapsed}s</span>
      </div>
      <ol className="space-y-2">
        {lines.map((line, i) => {
          const last = i === lines.length - 1
          return (
            <li key={`${i}-${line.text}`} className={cn('flex items-center gap-2.5 text-sm', last ? 'text-foreground' : 'text-muted-foreground')}>
              {last ? <LoaderCircle className="size-3.5 animate-spin text-primary" /> : <Check className="size-3.5 text-positive" />}
              {line.text}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

/** Failure panel for a build: what happened, Retry, and the closest presets. */
function BuildFailed({ message, suggestions, onRetry, retrying }: { message: string; suggestions: ViewSuggestion[]; onRetry: () => void; retrying: boolean }) {
  const [busy, setBusy] = useState<string | null>(null)
  return (
    <div className="space-y-3 rounded-xl border border-negative/25 bg-negative/[0.04] p-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="flex min-w-0 items-start gap-2">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-negative" />
          <span>{message}</span>
        </p>
        <Button variant="outline" size="sm" onClick={onRetry} disabled={retrying}>
          {retrying ? <LoaderCircle className="animate-spin" /> : <RotateCcw />} Retry
        </Button>
      </div>
      {suggestions.length ? (
        <div className="space-y-1.5 pl-6">
          <p className="text-xs text-muted-foreground">Or start from a preset</p>
          <SuggestionChips
            items={suggestions}
            busy={busy}
            onPick={async (slug) => {
              setBusy(slug)
              try {
                await addPreset(slug)
              } catch (err) {
                toastApiError(err, 'Could not add the preset')
                setBusy(null)
              }
            }}
          />
        </div>
      ) : null}
    </div>
  )
}

// ── detail (/views/:id) ────────────────────────────────────────────────
function ViewDetail({ viewId }: { viewId: string }) {
  const privacy = usePrivacyMode()
  const { currency } = useDisplayPrefs()
  const [meta, setMeta] = useState<ViewMeta | null>(null)
  const [doc, setDoc] = useState<ViewDocument | null>(null)
  const [viewState, setViewState] = useState<Record<string, unknown> | null>(null)
  const [job, setJob] = useState<ViewJob | null>(null)
  const [missing, setMissing] = useState(false)
  const [failed, setFailed] = useState<{ message: string; suggestions: ViewSuggestion[]; prompt: string; kind?: ViewJob['kind'] } | null>(null)
  const [instruction, setInstruction] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [decline, setDecline] = useState<ViewDecline | null>(null)
  const [broken, setBroken] = useState<'error' | 'escaped' | null>(null)
  const [frameKey, setFrameKey] = useState(0)

  const loadDocument = useCallback(
    async (m: ViewMeta) => {
      if (!m.current_version) return
      const [d, st] = await Promise.all([getViewHtml(viewId, m.current_version), getViewState(viewId).catch(() => ({}))])
      setViewState(st)
      setDoc(d)
      setBroken(null)
    },
    [viewId],
  )

  const load = useCallback(async () => {
    try {
      const m = await getView(viewId)
      setMeta(m)
      setMissing(false)
      if (m.active_job) setJob(m.active_job)
      else if (m.build?.status === 'error') setFailed({ message: m.build.error ?? 'The build failed.', suggestions: m.build.suggestions ?? [], prompt: m.build.prompt ?? '' })
      await loadDocument(m)
    } catch {
      setMissing(true)
    }
  }, [viewId, loadDocument])

  useEffect(() => {
    void load()
  }, [load])

  // Queued from another process (Archie) and not picked up yet: poll the view.
  useEffect(() => {
    if (!meta || job || meta.current_version || meta.build?.status === 'error') return
    const t = window.setInterval(() => void load(), 2000)
    return () => window.clearInterval(t)
  }, [meta, job, load])

  // Poll an active build. Every job ends done or error; a vanished job (restart) reloads the view.
  useEffect(() => {
    if (!job || (job.status !== 'queued' && job.status !== 'running')) return
    let alive = true
    let misses = 0
    const t = window.setInterval(async () => {
      try {
        const next = await getViewJob(job.id)
        if (!alive) return
        setJob(next)
        if (next.status === 'done') {
          window.clearInterval(t)
          setJob(null)
          setFailed(null)
          await load()
          void refreshViews()
          toast.success(next.kind === 'create' ? 'Your view is ready' : `Saved as version ${next.version}`, { description: `Built in ${Math.round(next.elapsed_s)}s` })
        } else if (next.status === 'error') {
          window.clearInterval(t)
          setJob(null)
          setFailed({ message: next.error ?? 'The build failed.', suggestions: next.suggestions ?? [], prompt: next.prompt, kind: next.kind })
          void refreshViews()
        }
      } catch (err) {
        const status = (err as { response?: { status?: number } })?.response?.status
        if (status === 404 || ++misses > 20) {
          window.clearInterval(t)
          setJob(null)
          void load()
        }
      }
    }, 1000)
    return () => {
      alive = false
      window.clearInterval(t)
    }
  }, [job?.id, job?.status, load]) // eslint-disable-line react-hooks/exhaustive-deps

  const startEdit = async (prompt: string, kind?: 'fix') => {
    if (!prompt.trim() || submitting) return
    setSubmitting(true)
    setDecline(null)
    try {
      const j = await generateView({ prompt: prompt.trim(), view_id: viewId, kind })
      setFailed(null)
      setJob(j)
      setInstruction('')
    } catch (err) {
      const d = declineOf(err)
      if (d) setDecline(d)
      else toastApiError(err, 'Could not start the change')
    } finally {
      setSubmitting(false)
    }
  }

  const retry = async () => {
    setSubmitting(true)
    try {
      const j = await retryView(viewId)
      setFailed(null)
      setJob(j)
    } catch (err) {
      const d = declineOf(err)
      if (d) setDecline(d)
      else toastApiError(err, 'Could not retry')
    } finally {
      setSubmitting(false)
    }
  }

  const onFix = (error: ViewRuntimeError) => {
    void startEdit(`Runtime error: ${error.message}${error.stack ? `\n\nStack:\n${error.stack}` : ''}`, 'fix')
  }

  const togglePin = async () => {
    if (!meta) return
    const pinned = !meta.pinned
    setMeta({ ...meta, pinned })
    patchCachedView(meta.id, { pinned })
    try {
      await patchView(meta.id, { pinned })
      toast.success(pinned ? 'Pinned to sidebar' : 'Removed from sidebar')
    } catch {
      setMeta({ ...meta })
      void refreshViews()
    }
  }

  const revert = async (version: number) => {
    try {
      const m = await revertView(viewId, version)
      setMeta(m)
      await loadDocument(m)
      toast.success(`Restored version ${version}`)
    } catch (err) {
      toastApiError(err, 'Could not revert')
    }
  }

  const remove = async () => {
    try {
      await deleteView(viewId)
      await refreshViews()
      toast.success('View deleted')
      navigate('/views')
    } catch (err) {
      toastApiError(err, 'Could not delete')
    }
  }

  const building = !!job && (job.status === 'queued' || job.status === 'running')

  if (missing) {
    return (
      <div className="mx-auto max-w-lg space-y-4 pt-12 text-center">
        <LayoutDashboard className="mx-auto size-5 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">This view doesn’t exist any more.</p>
        <Button variant="outline" onClick={() => navigate('/views')}>
          All views
        </Button>
      </div>
    )
  }

  if (!meta) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-12 w-72 rounded-lg" />
        <Skeleton className="h-10 w-full rounded-lg" />
        <Skeleton className="h-[420px] w-full rounded-xl" />
      </div>
    )
  }

  const isNew = meta.current_version === 0
  const versions = [...meta.versions].reverse()
  const restoreTo =
    meta.last_good_version && meta.last_good_version !== meta.current_version
      ? meta.last_good_version
      : (versions.find((v) => v.version < meta.current_version)?.version ?? null)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          {isNew ? (
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
              {failed ? <TriangleAlert className="size-4 text-negative" /> : <LoaderCircle className="size-4 animate-spin text-muted-foreground" />}
            </span>
          ) : (
            <IconTile icon={meta.icon} />
          )}
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">{isNew ? (failed ? 'Couldn’t build this view' : 'Building your view…') : meta.title}</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">{isNew ? `“${meta.build?.prompt ?? meta.history[0]?.prompt ?? ''}”` : meta.description}</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {!isNew ? (
            <>
              <DropdownMenu>
                <DropdownMenuTrigger
                  disabled={building}
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                >
                  <History className="size-4" />
                  <span className="font-mono tabular-nums">v{meta.current_version}</span>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-72">
                  <DropdownMenuGroup>
                    <DropdownMenuLabel>Versions</DropdownMenuLabel>
                    {versions.map((v) => (
                      <DropdownMenuItem key={v.version} onClick={() => v.version !== meta.current_version && revert(v.version)} className="items-start gap-2.5 py-2">
                        <span className="mt-0.5 w-6 font-mono text-xs text-muted-foreground tabular-nums">v{v.version}</span>
                        <span className="min-w-0 flex-1">
                          <span className="line-clamp-2 text-xs">{v.note || 'Saved'}</span>
                          <span className="text-[11px] text-muted-foreground">{ago(v.created_at)}</span>
                        </span>
                        {v.version === meta.current_version ? <Check className="mt-0.5 size-3.5 text-primary" /> : null}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuGroup>
                </DropdownMenuContent>
              </DropdownMenu>
              <Button variant="ghost" size="icon" onClick={togglePin} title={meta.pinned ? 'Unpin from sidebar' : 'Pin to sidebar'} aria-label={meta.pinned ? 'Unpin from sidebar' : 'Pin to sidebar'}>
                {meta.pinned ? <PinOff className="text-muted-foreground" /> : <Pin className="text-muted-foreground" />}
              </Button>
            </>
          ) : null}
          <AlertDialog>
            <AlertDialogTrigger render={<Button variant="ghost" size="icon" aria-label="Delete view" title="Delete view" disabled={building} />}>
              <Trash2 className="text-muted-foreground" />
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete “{meta.title}”?</AlertDialogTitle>
                <AlertDialogDescription>All {meta.versions.length} versions and its saved inputs are removed. This can’t be undone.</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction variant="destructive" onClick={remove}>
                  Delete view
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      {!isNew ? (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void startEdit(instruction)
          }}
          className="flex items-center gap-2 rounded-xl border border-border bg-card/60 py-1.5 pl-3 pr-1.5 transition-colors focus-within:border-primary/50"
        >
          <Sparkles className="size-4 shrink-0 text-primary" />
          <input
            value={instruction}
            onChange={(e) => {
              setInstruction(e.target.value)
              if (decline) setDecline(null)
            }}
            disabled={building}
            maxLength={4000}
            aria-label="Change this view"
            placeholder="Customise — e.g. “group by sector instead” or “only my ISA”"
            className="h-8 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:opacity-60"
          />
          <Button type="submit" size="icon-sm" disabled={!instruction.trim() || building || submitting} aria-label="Apply change">
            {submitting ? <LoaderCircle className="animate-spin" /> : <ArrowUp />}
          </Button>
        </form>
      ) : null}

      {decline ? <DeclineCard decline={decline} onExample={(t) => setInstruction(t)} /> : null}

      {failed && !building ? <BuildFailed message={failed.message} suggestions={failed.suggestions} onRetry={retry} retrying={submitting} /> : null}

      {building && job ? <BuildProgress job={job} /> : null}

      {doc && doc.kind === 'spec' && doc.spec ? (
        <div className={cn('transition-opacity', building && 'pointer-events-none opacity-40')}>
          <ViewSpecRenderer spec={doc.spec} />
        </div>
      ) : doc && doc.kind !== 'spec' && doc.html && viewState ? (
        <div className={cn('space-y-3 transition-opacity', building && 'pointer-events-none opacity-40')}>
          {broken ? (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-negative/25 bg-negative/[0.04] px-4 py-3 text-sm">
              <span className="flex items-center gap-2">
                <TriangleAlert className="size-4 shrink-0 text-negative" />
                {broken === 'escaped' ? 'This view tried to leave its sandbox and was stopped.' : 'This view hit an error.'}
              </span>
              <span className="flex gap-2">
                <Button size="sm" variant="ghost" onClick={() => { setFrameKey((k) => k + 1); setBroken(null) }}>
                  <RotateCcw /> Reload
                </Button>
                {restoreTo ? (
                  <Button size="sm" variant="outline" onClick={() => revert(restoreTo)}>
                    Restore v{restoreTo} <ArrowRight />
                  </Button>
                ) : null}
              </span>
            </div>
          ) : null}
          <ViewFrame
            key={frameKey}
            viewId={viewId}
            html={doc.html}
            version={doc.version}
            state={viewState}
            currency={currency}
            privacy={privacy}
            onFix={onFix}
            onBroken={(kind) => setBroken(kind)}
            onHealthy={(v) => {
              if (meta.last_good_version !== v) {
                setMeta((m) => (m ? { ...m, last_good_version: v } : m))
                void markViewGood(viewId, v).catch(() => {})
              }
            }}
          />
        </div>
      ) : doc && doc.problems.length ? (
        <div className="space-y-3 rounded-xl border border-warning/30 bg-warning/5 p-5 text-sm">
          <p className="font-medium">This version was blocked by the safety check.</p>
          <p className="text-muted-foreground">{doc.problems[0]}</p>
          {restoreTo ? (
            <Button size="sm" variant="outline" onClick={() => revert(restoreTo)}>
              Restore v{restoreTo}
            </Button>
          ) : null}
        </div>
      ) : isNew && !failed ? (
        <div className="grid gap-4 sm:grid-cols-3" aria-hidden>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[104px] rounded-xl" />
          ))}
          <Skeleton className="h-[260px] rounded-xl sm:col-span-3" />
        </div>
      ) : null}
    </div>
  )
}
