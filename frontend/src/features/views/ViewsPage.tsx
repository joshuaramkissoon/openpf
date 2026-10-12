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
  ShieldCheck,
  Sparkles,
  Trash2,
} from 'lucide-react'

import {
  deleteView,
  generateView,
  getView,
  getViewHtml,
  getViewJob,
  getViewState,
  installStarter,
  listStarters,
  patchView,
  revertView,
} from '@/api/views'
import { useDisplayPrefs } from '@/lib/display-prefs'
import { PageHeader } from '@/components/kit'
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { toastApiError } from '@/lib/api-error'
import { usePrivacyMode } from '@/lib/privacy'
import { navigate, type Route } from '@/lib/router'
import { cn } from '@/lib/utils'
import type { ViewDocument, ViewJob, ViewMeta, ViewStarter } from '@/types/views'
import { ViewFrame, type ViewRuntimeError } from './ViewFrame'
import { viewIcon } from './view-icons'
import { patchCachedView, refreshViews, useViews } from './views-store'

const EXAMPLES = [
  'Dividend income by month',
  'Drift vs my target weights',
  'Biggest winners and losers since I bought',
  'How my ISA compares to my Invest account',
  'Daily moves of my top 10 holdings as a heatmap',
  'What I bought and sold each month',
]

/** Display currency for views — follows the shell's currency switch. */
function useDisplayCurrency(): 'GBP' | 'USD' {
  return useDisplayPrefs().currency
}

function ago(iso: string | null | undefined): string {
  if (!iso) return ''
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  const d = Math.floor(s / 86400)
  return d === 1 ? 'yesterday' : d < 30 ? `${d} days ago` : new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export function ViewsPage({ route }: { route: Route }) {
  const [, second] = route.segments
  if (!second) return <ViewsGallery />
  if (second === 'new') return <ViewBuilder initialPrompt={route.query.get('prompt') ?? ''} />
  return <ViewDetail key={second} viewId={second} />
}

// ── icon tile ──────────────────────────────────────────────────────────
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

// ── gallery (/views) ───────────────────────────────────────────────────
function ViewsGallery() {
  const { views, loaded } = useViews()
  const [starters, setStarters] = useState<ViewStarter[] | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)

  useEffect(() => {
    listStarters().then(setStarters, () => setStarters([]))
  }, [views.length])

  const install = async (slug: string) => {
    setInstalling(slug)
    try {
      const meta = await installStarter(slug)
      await refreshViews()
      navigate(`/views/${meta.id}`)
    } catch (err) {
      toastApiError(err, 'Could not add the template')
    } finally {
      setInstalling(null)
    }
  }

  const available = (starters ?? []).filter((s) => !s.installed)
  const ready = views.filter((v) => v.current_version > 0 || v.active_job)

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Views"
        title="Your views"
        description="Pages Archie builds on your live data. They can read your portfolio — never trade."
        actions={
          <Button onClick={() => navigate('/views/new')}>
            <Plus /> New view
          </Button>
        }
      />

      {!loaded ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[132px] rounded-xl" />
          ))}
        </div>
      ) : ready.length ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {ready.map((v) => (
            <button
              key={v.id}
              onClick={() => navigate(`/views/${v.id}`)}
              className="group flex min-h-[132px] flex-col gap-3 rounded-xl border border-border bg-card p-5 text-left transition-colors hover:border-foreground/15 hover:bg-accent/40"
            >
              <div className="flex items-start justify-between gap-3">
                <IconTile icon={v.icon} />
                {v.pinned ? <Pin className="size-3.5 text-muted-foreground/70" /> : null}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold">{v.title}</p>
                <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{v.description}</p>
              </div>
              <p className="font-mono text-[11px] text-muted-foreground/80 tabular-nums">
                v{v.current_version} · updated {ago(v.updated_at)}
              </p>
            </button>
          ))}
          <button
            onClick={() => navigate('/views/new')}
            className="flex min-h-[132px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border text-sm text-muted-foreground transition-colors hover:border-foreground/20 hover:text-foreground"
          >
            <Sparkles className="size-4" />
            Describe a new view
          </button>
        </div>
      ) : (
        <button
          onClick={() => navigate('/views/new')}
          className="flex w-full flex-col items-center gap-2 rounded-xl border border-dashed border-border px-6 py-12 text-center transition-colors hover:border-foreground/20"
        >
          <LayoutDashboard className="size-5 text-muted-foreground" />
          <p className="text-sm font-medium">No views yet</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            Describe what you want to see — “dividend income by month” — and it appears here and in your sidebar in about a minute.
          </p>
        </button>
      )}

      {available.length ? (
        <section className="space-y-3">
          <div>
            <h2 className="text-sm font-semibold">Templates</h2>
            <p className="text-xs text-muted-foreground">Hand-built starting points. Add one, then ask Archie to make it yours.</p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {available.map((s) => (
              <div key={s.slug} className="flex flex-col gap-3 rounded-xl border border-border bg-card/60 p-5">
                <IconTile icon={s.icon} size="sm" />
                <div className="flex-1">
                  <p className="text-sm font-semibold">{s.title}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{s.description}</p>
                </div>
                <Button variant="outline" size="sm" className="self-start" disabled={installing === s.slug} onClick={() => install(s.slug)}>
                  {installing === s.slug ? <LoaderCircle className="animate-spin" /> : <Plus />} Add view
                </Button>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  )
}

// ── builder (/views/new) ───────────────────────────────────────────────
function ViewBuilder({ initialPrompt }: { initialPrompt: string }) {
  const [prompt, setPrompt] = useState(initialPrompt)
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    ref.current?.focus()
  }, [])

  const submit = async (text = prompt) => {
    const value = text.trim()
    if (!value || busy) return
    setBusy(true)
    try {
      const job = await generateView({ prompt: value })
      void refreshViews()
      navigate(`/views/${job.view_id}`)
    } catch (err) {
      toastApiError(err, 'Could not start the build')
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8 pt-4 sm:pt-10">
      <div className="space-y-2 text-center">
        <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">New view</p>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">What do you want to see?</h1>
        <p className="text-sm text-muted-foreground">Describe a page. Archie builds it on your live data in about a minute and pins it to your sidebar.</p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
        className="rounded-xl border border-border bg-card p-2 transition-colors focus-within:border-primary/50 focus-within:ring-3 focus-within:ring-primary/15"
      >
        <textarea
          ref={ref}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              void submit()
            }
          }}
          rows={4}
          maxLength={4000}
          placeholder="e.g. A monthly view of dividends I've received, which holdings pay the most, and what's coming next"
          className="block min-h-28 w-full resize-none bg-transparent px-3 py-2.5 text-[15px] leading-relaxed outline-none placeholder:text-muted-foreground"
        />
        <div className="flex items-center justify-between gap-3 px-2 pb-1 pt-2">
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <ShieldCheck className="size-3.5" /> Read-only · sandboxed · can’t trade
          </span>
          <Button type="submit" disabled={!prompt.trim() || busy}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Sparkles />} Build view
          </Button>
        </div>
      </form>

      <div className="space-y-3">
        <p className="text-center text-xs text-muted-foreground">Try one of these</p>
        <div className="flex flex-wrap justify-center gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              disabled={busy}
              onClick={() => {
                setPrompt(ex)
                ref.current?.focus()
              }}
              className="rounded-full border border-border bg-card/50 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-foreground/20 hover:text-foreground disabled:opacity-50"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── build progress ─────────────────────────────────────────────────────
function BuildProgress({ job, overlay }: { job: ViewJob; overlay?: boolean }) {
  const [now, setNow] = useState(Date.now())
  const [startedAt] = useState(() => Date.now() - job.elapsed_s * 1000)
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(t)
  }, [])
  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000))
  const lines = job.progress.length ? job.progress : [{ at: 0, text: 'Starting…' }]

  return (
    <div className={cn('rounded-xl border border-border bg-card p-5', overlay && 'shadow-none')}>
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

// ── detail (/views/:id) ────────────────────────────────────────────────
function ViewDetail({ viewId }: { viewId: string }) {
  const privacy = usePrivacyMode()
  const currency = useDisplayCurrency()
  const [meta, setMeta] = useState<ViewMeta | null>(null)
  const [doc, setDoc] = useState<ViewDocument | null>(null)
  const [viewState, setViewState] = useState<Record<string, unknown> | null>(null)
  const [job, setJob] = useState<ViewJob | null>(null)
  const [missing, setMissing] = useState(false)
  const [failed, setFailed] = useState<ViewJob | null>(null)
  const [instruction, setInstruction] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const loadDocument = useCallback(
    async (m: ViewMeta) => {
      if (!m.current_version) return
      const [d, st] = await Promise.all([getViewHtml(viewId, m.current_version), getViewState(viewId).catch(() => ({}))])
      setViewState(st)
      setDoc(d)
    },
    [viewId],
  )

  const load = useCallback(async () => {
    try {
      const m = await getView(viewId)
      setMeta(m)
      setMissing(false)
      if (m.active_job) setJob(m.active_job)
      await loadDocument(m)
    } catch {
      setMissing(true)
    }
  }, [viewId, loadDocument])

  useEffect(() => {
    void load()
  }, [load])

  // Poll an active build.
  useEffect(() => {
    if (!job || (job.status !== 'queued' && job.status !== 'running')) return
    let alive = true
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
          toast.success(next.kind === 'create' ? 'Your view is ready' : `Saved as version ${next.version}`, {
            description: `Built in ${Math.round(next.elapsed_s)}s`,
          })
        } else if (next.status === 'error') {
          window.clearInterval(t)
          setJob(null)
          setFailed(next)
          void refreshViews()
          if (next.kind === 'create') setMissing(true)
        }
      } catch {
        /* transient — keep polling */
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
    try {
      const j = await generateView({ prompt: prompt.trim(), view_id: viewId, kind })
      setFailed(null)
      setJob(j)
      setInstruction('')
    } catch (err) {
      toastApiError(err, 'Could not start the change')
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
      toast.success(`Reverted to version ${version}`)
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
        {failed ? (
          <>
            <p className="text-sm font-medium">That view couldn’t be built</p>
            <p className="text-sm text-muted-foreground">{failed.error}</p>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">This view doesn’t exist any more.</p>
        )}
        <Button variant="outline" onClick={() => navigate(failed ? `/views/new?prompt=${encodeURIComponent(failed.prompt)}` : '/views')}>
          {failed ? 'Try again' : 'All views'}
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          {isNew ? (
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
              <LoaderCircle className="size-4 animate-spin text-muted-foreground" />
            </span>
          ) : (
            <IconTile icon={meta.icon} />
          )}
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">{isNew ? 'Building your view…' : meta.title}</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {isNew ? `“${meta.history[0]?.prompt ?? ''}”` : meta.description}
            </p>
          </div>
        </div>
        {!isNew ? (
          <div className="flex items-center gap-1">
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
        ) : null}
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
            onChange={(e) => setInstruction(e.target.value)}
            disabled={building}
            maxLength={4000}
            placeholder="Edit with Archie — e.g. “add the 12-month average” or “split by account”"
            className="h-8 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:opacity-60"
          />
          <Button type="submit" size="icon-sm" disabled={!instruction.trim() || building || submitting} aria-label="Apply change">
            {submitting ? <LoaderCircle className="animate-spin" /> : <ArrowUp />}
          </Button>
        </form>
      ) : null}

      {failed && !building ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-negative/30 bg-negative/5 px-4 py-3 text-sm">
          <span>
            <span className="font-medium">That change didn’t work.</span> <span className="text-muted-foreground">{failed.error}</span>
          </span>
          <Button variant="outline" size="sm" onClick={() => startEdit(failed.prompt, failed.kind === 'fix' ? 'fix' : undefined)}>
            Try again <ArrowRight />
          </Button>
        </div>
      ) : null}

      {building && job ? <BuildProgress job={job} /> : null}

      {doc && doc.html && viewState ? (
        <div className={cn('transition-opacity', building && 'pointer-events-none opacity-40')}>
          <ViewFrame
            viewId={viewId}
            html={doc.html}
            version={doc.version}
            state={viewState}
            currency={currency}
            privacy={privacy}
            onFix={onFix}
          />
        </div>
      ) : doc && doc.problems.length ? (
        <div className="rounded-xl border border-warning/30 bg-warning/5 p-5 text-sm">
          <p className="font-medium">This version was blocked by the safety check.</p>
          <p className="mt-1 text-muted-foreground">{doc.problems[0]}</p>
        </div>
      ) : isNew && building ? (
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
