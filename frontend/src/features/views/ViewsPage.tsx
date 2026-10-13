import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  ArrowRight,
  ArrowUp,
  Check,
  ChevronRight,
  History,
  LayoutDashboard,
  LoaderCircle,
  MoreHorizontal,
  Pin,
  PinOff,
  Plus,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
  TriangleAlert,
  Undo2,
  X,
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
import { HomeToggle } from './on-home'
import { ViewSpecRenderer, VizBlock, WIDGET_PRESETS, type VizBlockSpec } from '@/components/viz'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { toastApiError } from '@/lib/api-error'
import { useDisplayPrefs } from '@/lib/display-prefs'
import { usePrivacyMode } from '@/lib/privacy'
import { navigate, type Route } from '@/lib/router'
import { cn } from '@/lib/utils'
import type { ViewDecline, ViewDocument, ViewJob, ViewMeta, ViewPreset, ViewSuggestion } from '@/types/views'
import { BuildCanvas, BuildStrip } from './BuildCanvas'
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
  if (second === '_building') return <BuildPreview prompt={route.query.get('prompt') ?? 'My holdings as a treemap, coloured by today’s move'} at={Number(route.query.get('at') ?? 0)} code={route.query.has('code')} />
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

/**
 * Dev preview of the build state with a simulated job (/views/_building?prompt=…&at=12&code).
 * `at` starts the clock that many seconds in; `code` simulates the slow custom-code path.
 */
function BuildPreview({ prompt, at, code }: { prompt: string; at: number; code: boolean }) {
  const [t0] = useState(() => Date.now() - at * 1000)
  const [, tick] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => tick((n) => n + 1), 500)
    return () => window.clearInterval(id)
  }, [])
  const s = (Date.now() - t0) / 1000
  const script: [number, string][] = code
    ? [[0, 'Designing the view…'], [3, 'This needs custom code — building it the slower way…'], [26, 'Reading your portfolio…'], [34, 'Writing the view…'], [44, 'Checking it’s safe and valid…']]
    : [[0, 'Designing the view…'], [7, 'Checking it renders safely…']]
  const job: ViewJob = {
    id: 'preview',
    view_id: 'preview',
    kind: 'create',
    prompt,
    status: 'running',
    progress: script.filter(([when]) => s >= when).map(([when, text]) => ({ at: when, text })),
    error: null,
    version: null,
    elapsed_s: at,
    cost_usd: null,
    engine: code ? 'code' : 'spec',
  }
  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20">
          <Sparkles className="size-4 text-primary" />
        </span>
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">Building your view</h1>
          <p className="mt-0.5 line-clamp-2 text-sm text-muted-foreground">“{prompt}”</p>
        </div>
      </div>
      <BuildCanvas job={job} prompt={prompt} />
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
function PresetGallery({ refreshKey, quiet }: { refreshKey: number; quiet: boolean }) {
  const { currency } = useDisplayPrefs()
  const [presets, setPresets] = useState<ViewPreset[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)

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
  // Once you have views of your own, the full catalogue waits behind one link.
  const restOpen = !quiet || showAll || !suggested.length

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
            <h2 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">Suggested for your portfolio</h2>
            <p className="mt-1 text-xs text-muted-foreground">Ready instantly. Change any of them with a sentence afterwards.</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">{suggested.map((p) => card(p, true))}</div>
        </div>
      ) : null}
      {rest.length ? (
        restOpen ? (
          <div className="space-y-3">
            <h2 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">{suggested.length ? 'More presets' : 'Presets'}</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{rest.map((p) => card(p, false))}</div>
          </div>
        ) : (
          <button type="button" onClick={() => setShowAll(true)} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <ChevronRight className="size-3.5" /> Browse all {presets.length} presets
          </button>
        )
      ) : null}
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
          <h2 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">Your views</h2>
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

      <PresetGallery refreshKey={views.length} quiet={ready.length > 0} />
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
        {failed ? (
          <TriangleAlert className="size-4 text-negative" />
        ) : (
          <span className="relative flex size-2 shrink-0">
            <span className="absolute inset-0 animate-ping rounded-full bg-primary/40 [animation-duration:2s]" />
            <span className="relative size-2 rounded-full bg-primary" />
          </span>
        )}
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

// ── edit composer (on demand) ──────────────────────────────────────────
const EDIT_IDEAS = ['Group by sector instead', 'Only my ISA', 'Add a one-year chart', 'Make it simpler']

function EditComposer({
  target,
  onClearTarget,
  onSubmit,
  onClose,
  submitting,
  decline,
  onExample,
  versions,
  current,
  onRevert,
  showHistory,
  onToggleHistory,
}: {
  target: string | null
  onClearTarget: () => void
  onSubmit: (text: string) => void
  onClose: () => void
  submitting: boolean
  decline: ViewDecline | null
  onExample: (t: string) => void
  versions: ViewMeta['versions']
  current: number
  onRevert: (v: number) => void
  showHistory: boolean
  onToggleHistory: () => void
}) {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    ref.current?.focus()
  }, [target])
  const submit = (value = text) => {
    if (!value.trim() || submitting) return
    onSubmit(value.trim())
  }
  const list = [...versions].reverse()
  return (
    <div className="view-composer space-y-3 rounded-2xl border border-primary/25 bg-card p-3 shadow-sm shadow-black/5">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
        className="flex items-start gap-2 pl-1"
      >
        <Sparkles className="mt-2 size-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          {target ? (
            <span className="mb-1 inline-flex max-w-full items-center gap-1 rounded-md bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
              <span className="truncate">Changing “{target}”</span>
              <button type="button" onClick={onClearTarget} aria-label="Change the whole view instead" className="rounded hover:bg-primary/15">
                <X className="size-3" />
              </button>
            </span>
          ) : null}
          <textarea
            ref={ref}
            value={text}
            rows={1}
            onChange={(e) => {
              setText(e.target.value)
              if (decline) onExample('')
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit()
              }
              if (e.key === 'Escape') onClose()
            }}
            maxLength={4000}
            aria-label="Describe the change"
            placeholder={target ? 'What should change about this chart?' : 'What should change?'}
            className="block max-h-40 min-h-9 w-full resize-none bg-transparent py-2 text-sm leading-relaxed outline-none [field-sizing:content] placeholder:text-muted-foreground"
          />
        </div>
        <Button type="submit" size="icon-sm" className="mt-1" disabled={!text.trim() || submitting} aria-label="Apply change">
          {submitting ? <LoaderCircle className="animate-spin" /> : <ArrowUp />}
        </Button>
        <Button type="button" variant="ghost" size="icon-sm" className="mt-1" onClick={onClose} aria-label="Close">
          <X />
        </Button>
      </form>
      {decline ? (
        <DeclineCard decline={decline} onExample={(t) => setText(t)} />
      ) : !text ? (
        <div className="flex flex-wrap gap-1.5 pl-7">
          {(target ? ['Show it as a table', 'Top 5 only', 'Sort the other way'] : EDIT_IDEAS).map((idea) => (
            <button
              key={idea}
              type="button"
              onClick={() => submit(idea)}
              className="rounded-full border border-border bg-background/60 px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:border-foreground/20 hover:text-foreground"
            >
              {idea}
            </button>
          ))}
        </div>
      ) : null}
      <div className="border-t border-border/60 pl-7 pt-2">
        <button type="button" onClick={onToggleHistory} className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground" aria-expanded={showHistory}>
          <History className="size-3.5" /> Version {current} of {versions.length}
          <ChevronRight className={cn('size-3 transition-transform', showHistory && 'rotate-90')} />
        </button>
        {showHistory ? (
          <ol className="mt-2 max-h-64 space-y-0.5 overflow-y-auto pb-1">
            {list.map((v) => (
              <li key={v.version} className="flex items-start gap-2.5 rounded-lg px-2 py-1.5 text-xs hover:bg-muted/50">
                <span className="mt-px w-6 font-mono tabular-nums text-muted-foreground">v{v.version}</span>
                <span className="min-w-0 flex-1">
                  <span className="line-clamp-2">{v.note || 'Saved'}</span>
                  <span className="text-[11px] text-muted-foreground">{ago(v.created_at)}</span>
                </span>
                {v.version === current ? (
                  <span className="inline-flex items-center gap-1 text-[11px] text-primary">
                    <Check className="size-3" /> Showing
                  </span>
                ) : (
                  <button type="button" onClick={() => onRevert(v.version)} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground">
                    <Undo2 className="size-3" /> Restore
                  </button>
                )}
              </li>
            ))}
          </ol>
        ) : null}
      </div>
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
  const [submitting, setSubmitting] = useState(false)
  const [decline, setDecline] = useState<ViewDecline | null>(null)
  const [broken, setBroken] = useState<'error' | 'escaped' | null>(null)
  const [frameKey, setFrameKey] = useState(0)
  const [composer, setComposer] = useState<{ target: string | null } | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [reveal, setReveal] = useState(0)

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
          // Let "Putting it together" show for a beat before the reveal.
          await new Promise((r) => window.setTimeout(r, 450))
          await load()
          setJob(null)
          setFailed(null)
          setReveal((n) => n + 1)
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

  const building = !!job && (job.status === 'queued' || job.status === 'running')
  const isNew = !meta || meta.current_version === 0

  // "e" opens the composer (when not typing somewhere).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (e.metaKey || e.ctrlKey || e.altKey || (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)))) return
      if (e.key === 'e' && !isNew && !building) {
        e.preventDefault()
        setComposer({ target: null })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isNew, building])

  const startEdit = async (prompt: string, kind?: 'fix') => {
    if (!prompt.trim() || submitting) return
    setSubmitting(true)
    setDecline(null)
    try {
      const j = await generateView({ prompt: prompt.trim(), view_id: viewId, kind })
      setFailed(null)
      setJob(j)
      setComposer(null)
      setShowHistory(false)
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
      setReveal((n) => n + 1)
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

  const editBlock = useCallback((block: VizBlockSpec) => {
    setDecline(null)
    setComposer({ target: block.title || (block.type === 'hero' ? block.label : null) || `the ${block.type} block` })
    window.scrollTo({ top: 0, behavior: 'smooth' })
    document.querySelector('main')?.scrollTo?.({ top: 0, behavior: 'smooth' })
  }, [])

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
        <div className="flex items-start gap-3">
          <Skeleton className="size-9 rounded-lg" />
          <div className="space-y-2">
            <Skeleton className="h-7 w-64 rounded-lg" />
            <Skeleton className="h-4 w-80" />
          </div>
        </div>
        <Skeleton className="h-[420px] w-full rounded-xl" />
      </div>
    )
  }

  const versions = [...meta.versions].reverse()
  const restoreTo =
    meta.last_good_version && meta.last_good_version !== meta.current_version
      ? meta.last_good_version
      : (versions.find((v) => v.version < meta.current_version)?.version ?? null)
  const prompt = meta.build?.prompt ?? job?.prompt ?? meta.history[0]?.prompt ?? ''
  const updated = versions.find((v) => v.version === meta.current_version)?.created_at ?? meta.updated_at

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <div className="flex min-w-0 items-start gap-3">
          {isNew ? (
            <span className={cn('flex size-9 shrink-0 items-center justify-center rounded-lg', failed ? 'bg-negative/10' : 'bg-primary/10 ring-1 ring-primary/20')}>
              {failed ? <TriangleAlert className="size-4 text-negative" /> : <Sparkles className="size-4 text-primary" />}
            </span>
          ) : (
            <IconTile icon={meta.icon} />
          )}
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">{isNew ? (failed ? 'Couldn’t build this view' : 'Building your view') : meta.title}</h1>
            {isNew ? (
              <p className="mt-0.5 line-clamp-2 text-sm text-muted-foreground">“{prompt}”</p>
            ) : (
              <p className="mt-0.5 text-sm text-muted-foreground">
                {meta.description}
                {meta.description ? <span className="text-muted-foreground/50"> · </span> : null}
                <button
                  type="button"
                  disabled={building}
                  onClick={() => {
                    setComposer((c) => c ?? { target: null })
                    setShowHistory(true)
                  }}
                  className="text-muted-foreground/80 underline-offset-4 hover:text-foreground hover:underline disabled:pointer-events-none"
                  title="Version history"
                >
                  v{meta.current_version}
                  {updated ? ` · ${ago(updated)}` : ''}
                </button>
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {!isNew && !composer ? (
            <>
            <Button variant="outline" size="sm" className="rounded-full" disabled={building} onClick={() => setComposer({ target: null })}>
              <Sparkles className="text-primary" /> Edit
              <kbd className="ml-0.5 hidden rounded border border-border px-1 font-mono text-[10px] text-muted-foreground sm:inline">E</kbd>
            </Button>
              {doc && meta.current_version ? (
                <HomeToggle
                  viewId={meta.id}
                  starter={meta.starter}
                  title={meta.title}
                  block={doc.kind === 'spec' && doc.spec ? Math.max(0, doc.spec.blocks.findIndex((b) => b.type !== 'note')) : 0}
                  navigate={navigate}
                />
              ) : null}
            </>
          ) : null}
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="More" disabled={building} />}>
              <MoreHorizontal className="text-muted-foreground" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              {!isNew ? (
                <DropdownMenuItem onClick={togglePin}>
                  {meta.pinned ? <PinOff /> : <Pin />} {meta.pinned ? 'Unpin from sidebar' : 'Pin to sidebar'}
                </DropdownMenuItem>
              ) : null}
              {!isNew ? (
                <DropdownMenuItem
                  onClick={() => {
                    setComposer((c) => c ?? { target: null })
                    setShowHistory(true)
                  }}
                >
                  <History /> Version history
                </DropdownMenuItem>
              ) : null}
              {!isNew ? <DropdownMenuSeparator /> : null}
              <DropdownMenuItem variant="destructive" onClick={() => setConfirmDelete(true)}>
                <Trash2 /> Delete view
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
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

      {composer && !isNew && !building ? (
        <EditComposer
          target={composer.target}
          onClearTarget={() => setComposer({ target: null })}
          onSubmit={(text) => void startEdit(composer.target ? `Change the “${composer.target}” block: ${text}` : text)}
          onClose={() => {
            setComposer(null)
            setShowHistory(false)
            setDecline(null)
          }}
          submitting={submitting}
          decline={decline}
          onExample={() => setDecline(null)}
          versions={meta.versions}
          current={meta.current_version}
          onRevert={(v) => void revert(v)}
          showHistory={showHistory}
          onToggleHistory={() => setShowHistory((v) => !v)}
        />
      ) : null}

      {failed && !building ? <BuildFailed message={failed.message} suggestions={failed.suggestions} onRetry={retry} retrying={submitting} /> : null}

      {building && job && !isNew ? <BuildStrip job={job} /> : null}

      {isNew && !failed ? (
        <BuildCanvas job={job} prompt={prompt} />
      ) : doc && doc.kind === 'spec' && doc.spec ? (
        <div key={reveal} className={cn(reveal ? 'view-reveal' : '', building && 'view-dimmed')}>
          <ViewSpecRenderer spec={doc.spec} onEditBlock={building ? undefined : editBlock} />
        </div>
      ) : doc && doc.kind !== 'spec' && doc.html && viewState ? (
        <div className={cn('space-y-3', building && 'view-dimmed')}>
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
      ) : null}
    </div>
  )
}
