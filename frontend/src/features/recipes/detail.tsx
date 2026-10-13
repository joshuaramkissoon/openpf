import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  ArrowLeft,
  ArrowUp,
  Bot,
  Check,
  ChevronRight,
  FileText,
  History,
  LoaderCircle,
  MoreHorizontal,
  Pencil,
  Play,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  Undo2,
  X,
} from 'lucide-react'

import { getArtifact } from '@/api/client'
import {
  deleteRecipe,
  getRecipe,
  getVersionDiff,
  patchRecipe,
  resetRecipe,
  reviseRecipe,
  revertRecipe,
  runRecipe,
  setRecipeEnabled,
} from '@/api/recipes'
import { RichMarkdown } from '@/components/RichMarkdown'
import { resolveArtifactRelativePath } from '@/components/scheduled-jobs/artifact'
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { toastApiError } from '@/lib/api-error'
import { navigate } from '@/lib/router'
import { cn } from '@/lib/utils'
import type { DiffLine, DraftResult, RecipeDetail, RecipeRun, RecipeVersion } from '@/types/recipes'
import { RecipeEditor } from './editor'
import { ago, AUTHOR_LABEL, Chip, DiffView, nextRunLabel, RecipeIcon, StatusDot, utc } from './parts'
import { SuggestionList } from './suggestions'

/** `/recipes/:slug` */
export function RecipeDetailPage({ slug }: { slug: string }) {
  const [recipe, setRecipe] = useState<RecipeDetail | null>(null)
  const [missing, setMissing] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [starting, setStarting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [report, setReport] = useState<RecipeRun | null>(null)

  const load = useCallback(async () => {
    try {
      setRecipe(await getRecipe(slug))
      setMissing(false)
    } catch {
      setMissing(true)
    }
  }, [slug])

  useEffect(() => {
    void load()
  }, [load])

  // While a run is in flight, poll until it lands.
  useEffect(() => {
    if (!recipe?.running) return
    const before = recipe.runs[0]?.id
    const t = window.setInterval(async () => {
      try {
        const next = await getRecipe(slug)
        setRecipe(next)
        if (!next.running) {
          window.clearInterval(t)
          const run = next.runs[0]
          if (run && run.id !== before) {
            if (run.status === 'ok') toast.success(`${next.title} finished`, { description: run.summary || undefined, action: { label: 'Read', onClick: () => setReport(run) } })
            else toast.error(`${next.title} didn’t finish`, { description: run.message })
          }
        }
      } catch {
        /* keep polling */
      }
    }, 3000)
    return () => window.clearInterval(t)
  }, [recipe?.running, slug]) // eslint-disable-line react-hooks/exhaustive-deps

  if (missing) {
    return (
      <div className="mx-auto max-w-md space-y-4 pt-16 text-center">
        <p className="text-sm text-muted-foreground">This recipe doesn’t exist any more.</p>
        <Button variant="outline" onClick={() => navigate('/recipes')}>
          All recipes
        </Button>
      </div>
    )
  }
  if (!recipe) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-12 w-80 rounded-lg" />
        <Skeleton className="h-10 w-full rounded-lg" />
        <div className="grid gap-6 lg:grid-cols-5">
          <Skeleton className="h-64 rounded-xl lg:col-span-3" />
          <Skeleton className="h-64 rounded-xl lg:col-span-2" />
        </div>
      </div>
    )
  }

  const toggle = async (enabled: boolean) => {
    setRecipe({ ...recipe, enabled })
    try {
      const next = await setRecipeEnabled(slug, enabled)
      setRecipe((r) => (r ? { ...r, ...next } : r))
      toast.success(enabled ? 'Recipe on' : 'Recipe paused', { description: enabled ? next.trigger_summary : undefined })
    } catch (err) {
      toastApiError(err, 'Could not update the recipe')
      void load()
    }
  }

  const runNow = async () => {
    setStarting(true)
    try {
      const out = await runRecipe(slug)
      if (out.status === 'already_running') toast.info('Already running')
      else toast('Running now', { description: 'Usually a minute or two. The result lands here, on Home and in Activity.' })
      setRecipe((r) => (r ? { ...r, ...out.recipe, running: true } : r))
    } catch (err) {
      toastApiError(err, 'Could not start the run')
    } finally {
      setStarting(false)
    }
  }

  const saveEdit = async (fields: Parameters<typeof patchRecipe>[1]) => {
    setSaving(true)
    try {
      await patchRecipe(slug, { ...fields, note: 'Edited by hand' })
      await load()
      setEditing(false)
      toast.success('Saved as a new version')
    } catch (err) {
      toastApiError(err, 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    try {
      await deleteRecipe(slug)
      toast.success('Recipe deleted')
      navigate('/recipes')
    } catch (err) {
      toastApiError(err, 'Could not delete')
    }
  }

  const reset = async () => {
    try {
      await resetRecipe(slug)
      await load()
      toast.success('Back to the original — your edits are still in the history')
    } catch (err) {
      toastApiError(err, 'Could not reset')
    }
  }

  const lastOk = recipe.runs.find((r) => r.status === 'ok' && r.output_path)

  return (
    <div className="space-y-7">
      <button type="button" onClick={() => navigate('/recipes')} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Recipes
      </button>

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3.5">
          <RecipeIcon icon={recipe.icon} size="lg" muted={!recipe.enabled} />
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">{recipe.title}</h1>
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">{recipe.summary}</p>
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <Chip className={recipe.enabled ? 'border-primary/30 text-foreground' : undefined}>{recipe.trigger_summary}</Chip>
              {recipe.enabled && recipe.next_run_at ? <Chip>next {nextRunLabel(recipe.next_run_at)}</Chip> : null}
              {!recipe.enabled ? <Chip>Paused</Chip> : null}
              <Chip>{recipe.delivery.telegram ? 'Home + Telegram' : 'Home'}</Chip>
              <Chip>
                {recipe.created_by === 'archie' ? <Bot className="size-3" /> : null}
                {recipe.source === 'builtin' ? (recipe.modified ? 'Built-in · edited' : 'Built-in') : recipe.created_by === 'archie' ? 'Made by Archie' : 'Yours'}
              </Chip>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 rounded-lg px-2 py-1 text-sm text-muted-foreground">
            {recipe.enabled ? 'On' : 'Off'}
            <Switch checked={recipe.enabled} onCheckedChange={toggle} aria-label={recipe.enabled ? 'Pause recipe' : 'Turn recipe on'} />
          </label>
          <Button variant="outline" onClick={runNow} disabled={starting || recipe.running}>
            {starting || recipe.running ? <LoaderCircle className="animate-spin" /> : <Play />}
            {recipe.running ? 'Running…' : 'Run now'}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label="More" />}>
              <MoreHorizontal />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              <DropdownMenuItem onClick={() => setEditing(true)}>
                <Pencil /> Edit by hand
              </DropdownMenuItem>
              {recipe.source === 'builtin' ? (
                <DropdownMenuItem onClick={reset} disabled={!recipe.modified}>
                  <RotateCcw /> Reset to original
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem variant="destructive" onClick={() => setConfirmDelete(true)}>
                  <Trash2 /> Delete recipe
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{recipe.title}”?</AlertDialogTitle>
            <AlertDialogDescription>Its {recipe.versions.length} versions are removed and it stops running. Past results stay in Activity.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={remove}>
              Delete recipe
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {!editing ? <ReviseBar slug={slug} onApplied={load} /> : null}

      <SuggestionList suggestions={recipe.suggestions} onResolved={() => void load()} />

      {editing ? (
        <div className="rounded-xl border border-border bg-card p-5">
          <h2 className="mb-4 text-sm font-semibold">Edit by hand</h2>
          <RecipeEditor recipe={recipe} saving={saving} onSave={saveEdit} onCancel={() => setEditing(false)} />
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-5">
          <div className="space-y-6 lg:col-span-3">
            <LatestResult run={lastOk ?? null} last={recipe.runs[0] ?? null} running={recipe.running} onOpen={setReport} onRun={runNow} />
            <RunsList runs={recipe.runs} onOpen={setReport} />
          </div>
          <div className="space-y-6 lg:col-span-2">
            <section className="rounded-xl border border-border bg-card">
              <div className="flex items-center justify-between border-b border-border/70 px-4 py-2.5">
                <h2 className="text-sm font-semibold">How Archie does it</h2>
                <Button variant="ghost" size="xs" onClick={() => setEditing(true)}>
                  <Pencil /> Edit
                </Button>
              </div>
              <div className="max-h-[520px] overflow-y-auto px-4 py-3 text-sm [&_h1]:hidden [&_h2]:mt-3 [&_h2]:text-[13px] [&_h2]:font-semibold [&_li]:text-[13px] [&_p]:text-[13px]">
                <RichMarkdown markdown={recipe.body} />
              </div>
              <p className="flex items-center gap-1.5 border-t border-border/70 px-4 py-2 text-[11px] text-muted-foreground">
                <ShieldCheck className="size-3" /> <span title={`Loaded by Archie as the skill ${recipe.skill}`}>Instructions only — proposes, never trades.</span>
              </p>
            </section>
            <VersionHistory slug={slug} versions={recipe.versions} current={recipe.current_version} onReverted={load} />
          </div>
        </div>
      )}

      <ReportSheet run={report} title={recipe.title} onClose={() => setReport(null)} />
    </div>
  )
}

// ── edit with a sentence ───────────────────────────────────────────────
function ReviseBar({ slug, onApplied }: { slug: string; onApplied: () => Promise<void> | void }) {
  const [instruction, setInstruction] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<{ instruction: string; result: DraftResult } | null>(null)
  const [applying, setApplying] = useState(false)

  const submit = async () => {
    const text = instruction.trim()
    if (!text || busy) return
    setBusy(true)
    setPreview(null)
    try {
      setPreview({ instruction: text, result: await reviseRecipe(slug, text) })
    } catch (err) {
      toastApiError(err, 'Archie couldn’t make that change')
    } finally {
      setBusy(false)
    }
  }

  const apply = async () => {
    if (!preview || !preview.result.ok) return
    const d = preview.result.draft
    setApplying(true)
    try {
      await patchRecipe(slug, {
        title: d.title,
        summary: d.summary,
        icon: d.icon,
        body: d.body,
        trigger: d.trigger,
        delivery: { telegram: d.delivery.telegram },
        author: 'archie',
        note: preview.result.note || `“${preview.instruction.slice(0, 200)}”`,
      })
      setPreview(null)
      setInstruction('')
      await onApplied()
      toast.success('Applied — saved as a new version', { description: 'Revert any time from History.' })
    } catch (err) {
      toastApiError(err, 'Could not apply the change')
    } finally {
      setApplying(false)
    }
  }

  return (
    <div className="space-y-3">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
        className="flex items-center gap-2 rounded-xl border border-border bg-card/60 py-1.5 pl-3 pr-1.5 transition-colors focus-within:border-primary/50"
      >
        {busy ? <LoaderCircle className="size-4 shrink-0 animate-spin text-primary" /> : <Sparkles className="size-4 shrink-0 text-primary" />}
        <input
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          disabled={busy}
          maxLength={2000}
          placeholder={busy ? 'Archie is rewriting the recipe…' : 'Change it with a sentence — “shorter”, “skip crypto”, “run at 8 instead”'}
          className="h-8 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:opacity-60"
        />
        <Button type="submit" size="icon-sm" disabled={!instruction.trim() || busy} aria-label="Ask Archie to change it">
          <ArrowUp />
        </Button>
      </form>

      {preview && !preview.result.ok ? (
        <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3 text-sm">
          <div className="min-w-0 space-y-1">
            <p>{preview.result.reason}</p>
            {preview.result.alternative ? <p className="text-xs text-muted-foreground">{preview.result.alternative}</p> : null}
          </div>
          <Button variant="ghost" size="sm" onClick={() => setPreview(null)}>
            <X /> Close
          </Button>
        </div>
      ) : null}

      {preview && preview.result.ok ? (
        <div className="space-y-3 rounded-xl border border-primary/25 bg-primary/[0.04] p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">{preview.result.note || 'Archie’s change'}</p>
              <p className="text-xs text-muted-foreground">Review the diff — nothing changes until you apply it.</p>
            </div>
            <div className="flex items-center gap-1.5">
              <Button variant="ghost" size="sm" onClick={() => setPreview(null)} disabled={applying}>
                Discard
              </Button>
              <Button size="sm" onClick={apply} disabled={applying}>
                {applying ? <LoaderCircle className="animate-spin" /> : <Check />} Apply
              </Button>
            </div>
          </div>
          <DiffView lines={preview.result.diff ?? []} />
        </div>
      ) : null}
    </div>
  )
}

// ── results ────────────────────────────────────────────────────────────
function useReport(run: RecipeRun | null) {
  const [content, setContent] = useState<string | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    setContent(null)
    setError(false)
    if (!run?.output_path) return
    let alive = true
    getArtifact(resolveArtifactRelativePath(run.output_path)).then(
      (d) => alive && setContent(d.content.replace(/^---[\s\S]*?---\s*/, '')),
      () => alive && setError(true),
    )
    return () => {
      alive = false
    }
  }, [run?.id, run?.output_path])
  return { content, error }
}

function LatestResult({ run, last, running, onOpen, onRun }: {
  run: RecipeRun | null
  last: RecipeRun | null
  running: boolean
  onOpen: (r: RecipeRun) => void
  onRun: () => void
}) {
  const { content } = useReport(run)
  const failedSince = last && last.status === 'error' && (!run || last.id !== run.id) ? last : null
  return (
    <section className="rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between gap-3 border-b border-border/70 px-4 py-2.5">
        <h2 className="text-sm font-semibold">Latest result</h2>
        {run ? <span className="text-xs text-muted-foreground">{utc(run.at).format('ddd D MMM, HH:mm')}</span> : null}
      </div>
      {failedSince ? (
        <p className="flex items-start gap-2 border-b border-border/60 bg-negative/5 px-4 py-2 text-xs text-negative">
          <StatusDot status="error" className="mt-1" /> Last run {ago(failedSince.at)} failed: {failedSince.message}
        </p>
      ) : null}
      {running ? (
        <div className="flex items-center gap-2.5 px-4 py-6 text-sm text-muted-foreground">
          <LoaderCircle className="size-4 animate-spin text-primary" /> Archie is running this now…
        </div>
      ) : run ? (
        <div className="space-y-3 px-4 py-4">
          {run.trigger_context ? (
            <p className="rounded-lg bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">Why it ran: </span>
              {run.trigger_context.split('\n').slice(1).join(' · ') || run.trigger_context}
            </p>
          ) : null}
          {content ? (
            <div className="relative max-h-[340px] overflow-hidden text-sm">
              <RichMarkdown markdown={content} />
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-card to-transparent" />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{run.summary}</p>
          )}
          <button type="button" onClick={() => onOpen(run)} className="text-xs text-primary hover:underline">
            Read the full report →
          </button>
        </div>
      ) : (
        <div className="flex flex-col items-start gap-3 px-4 py-6">
          <p className="text-sm text-muted-foreground">No results yet. Run it once to see what you’ll get.</p>
          <Button size="sm" variant="outline" onClick={onRun}>
            <Play /> Run now
          </Button>
        </div>
      )}
    </section>
  )
}

function RunsList({ runs, onOpen }: { runs: RecipeRun[]; onOpen: (r: RecipeRun) => void }) {
  if (runs.length < 2) return null
  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold">Runs</h2>
      <ul className="divide-y divide-border/60 rounded-xl border border-border">
        {runs.map((r) => (
          <li key={r.id}>
            <button
              type="button"
              disabled={!r.output_path}
              onClick={() => onOpen(r)}
              className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors hover:bg-accent/40 disabled:cursor-default disabled:hover:bg-transparent"
            >
              <StatusDot status={r.status} />
              <span className="w-28 shrink-0 font-mono text-xs tabular-nums text-muted-foreground">{utc(r.at).format('D MMM HH:mm')}</span>
              <span className={cn('min-w-0 flex-1 truncate text-xs', r.status === 'error' ? 'text-negative' : 'text-muted-foreground')}>
                {r.status === 'error' ? r.message : r.summary || r.message}
              </span>
              {r.telegram ? <Send className="size-3 shrink-0 text-muted-foreground" aria-label="Sent to Telegram" /> : null}
              {r.output_path ? <FileText className="size-3.5 shrink-0 text-muted-foreground" /> : null}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ReportSheet({ run, title, onClose }: { run: RecipeRun | null; title: string; onClose: () => void }) {
  const { content, error } = useReport(run)
  return (
    <Sheet open={!!run} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
          <SheetDescription>{run ? utc(run.at).format('dddd D MMMM, HH:mm') : ''}{run?.version ? ` · recipe v${run.version}` : ''}</SheetDescription>
        </SheetHeader>
        <div className="px-4 pb-8">
          {content ? <RichMarkdown markdown={content} /> : error ? <p className="text-sm text-muted-foreground">The report file isn’t available any more.</p> : <Skeleton className="h-40 w-full" />}
        </div>
      </SheetContent>
    </Sheet>
  )
}

// ── history ────────────────────────────────────────────────────────────
function VersionHistory({ slug, versions, current, onReverted }: { slug: string; versions: RecipeVersion[]; current: number; onReverted: () => Promise<void> | void }) {
  const [open, setOpen] = useState<number | null>(null)
  const [diff, setDiff] = useState<DiffLine[] | null>(null)
  const [restoring, setRestoring] = useState<number | null>(null)

  useEffect(() => {
    setDiff(null)
    if (open == null) return
    let alive = true
    getVersionDiff(slug, open).then((d) => alive && setDiff(d.diff), () => alive && setDiff([]))
    return () => {
      alive = false
    }
  }, [open, slug])

  const restore = async (version: number) => {
    setRestoring(version)
    try {
      await revertRecipe(slug, version)
      setOpen(null)
      await onReverted()
      toast.success(`Restored version ${version}`, { description: 'Saved as a new version, so you can undo this too.' })
    } catch (err) {
      toastApiError(err, 'Could not restore')
    } finally {
      setRestoring(null)
    }
  }

  return (
    <section>
      <h2 className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
        <History className="size-3.5 text-muted-foreground" /> History
      </h2>
      <ol className="relative space-y-0.5 border-l border-border pl-4">
        {versions.map((v) => {
          const isOpen = open === v.version
          return (
            <li key={v.version} className="relative">
              <span className={cn('absolute -left-[21px] top-3 size-2 rounded-full ring-2 ring-background', v.version === current ? 'bg-primary' : 'bg-border')} />
              <button
                type="button"
                onClick={() => setOpen(isOpen ? null : v.version)}
                className="flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left transition-colors hover:bg-accent/40"
                aria-expanded={isOpen}
              >
                <span className="w-7 shrink-0 font-mono text-xs tabular-nums text-muted-foreground">v{v.version}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px]">{v.note || 'Saved'}</span>
                  <span className="block text-[11px] text-muted-foreground">
                    {AUTHOR_LABEL[v.author] ?? v.author} · {ago(v.created_at)}
                    {v.version === current ? ' · current' : ''}
                  </span>
                </span>
                <ChevronRight className={cn('mt-0.5 size-3.5 shrink-0 text-muted-foreground transition-transform', isOpen && 'rotate-90')} />
              </button>
              {isOpen ? (
                <div className="space-y-2 pb-3 pl-2">
                  {diff ? <DiffView lines={diff} maxHeight={260} /> : <Skeleton className="h-24 w-full" />}
                  {v.version !== current ? (
                    <Button size="xs" variant="outline" onClick={() => restore(v.version)} disabled={restoring != null}>
                      {restoring === v.version ? <LoaderCircle className="animate-spin" /> : <Undo2 />} Restore this version
                    </Button>
                  ) : null}
                </div>
              ) : null}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
