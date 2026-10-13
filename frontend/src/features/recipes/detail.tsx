import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Check,
  ChevronRight,
  History,
  LoaderCircle,
  MoreHorizontal,
  Pencil,
  Play,
  RotateCcw,
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
import { ReportBody, ReportReader, useReport as useReportDoc } from '@/components/rich/report-reader'
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
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { toastApiError } from '@/lib/api-error'
import { navigate } from '@/lib/router'
import { cn } from '@/lib/utils'
import type { ReportSummary } from '@/types'
import type { DiffLine, DraftResult, RecipeDetail, RecipeRun, RecipeVersion } from '@/types/recipes'
import { RecipeEditor } from './editor'
import { ago, AUTHOR_LABEL, deliverySentence, DiffView, nextRunLabel, RecipeIcon, StatusDot, utc, whenSentence } from './parts'
import { humanSteps, outputParts } from './steps'
import { SuggestionList } from './suggestions'
import './recipes.css'

/** `/recipes/:slug` */
export function RecipeDetailPage({ slug }: { slug: string }) {
  const [recipe, setRecipe] = useState<RecipeDetail | null>(null)
  const [missing, setMissing] = useState(false)
  const [editing, setEditing] = useState(false)
  const [revising, setRevising] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
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
            if (run.status === 'ok') toast.success(`${next.title} finished`, { description: run.summary || undefined })
            else toast.error(`${next.title} didn’t finish`, { description: run.message })
          }
        }
      } catch {
        /* keep polling */
      }
    }, 3000)
    return () => window.clearInterval(t)
  }, [recipe?.running, slug]) // eslint-disable-line react-hooks/exhaustive-deps

  // "e" opens the sentence editor (when not typing somewhere).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (e.metaKey || e.ctrlKey || e.altKey || (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)))) return
      if (e.key === 'e' && !editing) {
        e.preventDefault()
        setRevising(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editing])

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
      <div className="mx-auto max-w-4xl space-y-8 pt-6">
        <div className="flex items-start gap-4">
          <Skeleton className="size-11 rounded-xl" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-7 w-64 rounded-lg" />
            <Skeleton className="h-4 w-full max-w-lg" />
          </div>
        </div>
        <Skeleton className="h-72 w-full rounded-2xl" />
      </div>
    )
  }

  const toggle = async (enabled: boolean) => {
    setRecipe({ ...recipe, enabled })
    try {
      const next = await setRecipeEnabled(slug, enabled)
      setRecipe((r) => (r ? { ...r, ...next } : r))
      toast.success(enabled ? 'Recipe on' : 'Recipe paused', { description: enabled ? whenSentence(next) : undefined })
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

  const lastOk = recipe.runs.find((r) => r.status === 'ok' && r.output_path) ?? null
  const busy = starting || recipe.running

  return (
    <div className="recipe-page mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── Lead: what it is, when it runs ───────────────────────────── */}
      <header className="space-y-5">
        <button type="button" onClick={() => navigate('/recipes')} className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground">
          <ArrowLeft className="size-3.5" /> Recipes
        </button>
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 items-start gap-4">
            <RecipeIcon icon={recipe.icon} size="lg" muted={!recipe.enabled} />
            <div className="min-w-0 space-y-2">
              <h1 className="text-[1.75rem] font-semibold leading-tight tracking-tight">{recipe.title}</h1>
              <p className="max-w-[60ch] text-[15px] leading-relaxed text-muted-foreground">{recipe.summary}</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 self-end sm:self-start">
            <Switch checked={recipe.enabled} onCheckedChange={toggle} aria-label={recipe.enabled ? 'Pause recipe' : 'Turn recipe on'} />
            <span className="mr-2 w-12 text-xs text-muted-foreground">{recipe.enabled ? 'On' : 'Paused'}</span>
            <Button variant="outline" size="sm" onClick={runNow} disabled={busy}>
              {busy ? <LoaderCircle className="animate-spin" /> : <Play />}
              {recipe.running ? 'Running…' : 'Run now'}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="More" />}>
                <MoreHorizontal />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuItem onClick={() => setRevising(true)}>
                  <Sparkles /> Change with a sentence
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setEditing(true)}>
                  <Pencil /> Edit by hand
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setHistoryOpen(true)}>
                  <History /> History · v{recipe.current_version}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
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

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 sm:pl-[3.75rem]">
          <p className="text-sm leading-relaxed">
            <span className={cn('relative mr-2 inline-flex size-2 align-middle', !recipe.enabled && 'opacity-60')}>
              {recipe.enabled ? <span className="recipe-breathe absolute inset-0 rounded-full bg-positive/50" /> : null}
              <span className={cn('relative size-2 rounded-full', recipe.enabled ? 'bg-positive' : 'bg-muted-foreground/50')} />
            </span>
            <span className={cn('font-medium', !recipe.enabled && 'text-muted-foreground')}>
              {recipe.enabled ? whenSentence(recipe) : `Paused · ${lower(whenSentence(recipe))}`}
            </span>
            <span className="text-muted-foreground"> · {deliverySentence(recipe)}</span>
            {recipe.enabled && recipe.next_run_at ? <span className="text-muted-foreground/80"> · next {nextRunLabel(recipe.next_run_at)}</span> : null}
          </p>
          {!revising && !editing ? (
            <button
              type="button"
              onClick={() => setRevising(true)}
              className="group inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <Sparkles className="size-3 text-primary/80 transition-colors group-hover:text-primary" /> Change it
              <kbd className="ml-0.5 hidden rounded border border-border px-1 font-mono text-[10px] text-muted-foreground/70 sm:inline">E</kbd>
            </button>
          ) : null}
        </div>

        {revising && !editing ? <ReviseComposer slug={slug} onApplied={load} onClose={() => setRevising(false)} /> : null}
      </header>

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

      <SuggestionList suggestions={recipe.suggestions} onResolved={() => void load()} />

      {editing ? (
        <section className="rounded-2xl border border-border bg-card p-5 sm:p-6">
          <h2 className="mb-4 text-sm font-semibold">Edit by hand</h2>
          <RecipeEditor recipe={recipe} saving={saving} onSave={saveEdit} onCancel={() => setEditing(false)} />
        </section>
      ) : (
        <>
          <ResultCard recipe={recipe} run={lastOk} last={recipe.runs[0] ?? null} running={recipe.running} busy={busy} onOpen={setReport} onRun={runNow} />
          <div className="grid gap-10 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:gap-12">
            <StepsTimeline recipe={recipe} />
            <RunsTimeline runs={recipe.runs} running={recipe.running} onOpen={setReport} />
          </div>
        </>
      )}

      <HistorySheet open={historyOpen} onOpenChange={setHistoryOpen} slug={slug} versions={recipe.versions} current={recipe.current_version} onReverted={load} />
      <RunReader run={report} title={recipe.title} onClose={() => setReport(null)} />
    </div>
  )
}

const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1)

// ── change it with a sentence (on demand) ─────────────────────────────
const IDEAS = ['Make it shorter', 'Skip crypto', 'Run at 8 instead', 'Also send it to Telegram']

function ReviseComposer({ slug, onApplied, onClose }: { slug: string; onApplied: () => Promise<void> | void; onClose: () => void }) {
  const [instruction, setInstruction] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<{ instruction: string; result: DraftResult } | null>(null)
  const [applying, setApplying] = useState(false)
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => {
    ref.current?.focus()
  }, [])

  const submit = async (text = instruction.trim()) => {
    if (!text || busy) return
    setInstruction(text)
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
      await onApplied()
      onClose()
      toast.success('Applied — saved as a new version', { description: 'Undo any time from History.' })
    } catch (err) {
      toastApiError(err, 'Could not apply the change')
    } finally {
      setApplying(false)
    }
  }

  return (
    <div className="recipe-unfold space-y-3 rounded-2xl border border-primary/25 bg-primary/[0.03] p-3 sm:ml-[3.75rem]">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
        className="flex items-center gap-2 pl-1"
      >
        {busy ? <LoaderCircle className="size-4 shrink-0 animate-spin text-primary" /> : <Sparkles className="size-4 shrink-0 text-primary" />}
        <input
          ref={ref}
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => e.key === 'Escape' && !busy && onClose()}
          disabled={busy}
          maxLength={2000}
          aria-label="Describe the change"
          placeholder={busy ? 'Archie is rewriting the recipe…' : 'What should change?'}
          className="h-9 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:opacity-60"
        />
        <Button type="submit" size="icon-sm" disabled={!instruction.trim() || busy} aria-label="Ask Archie to change it">
          <ArrowUp />
        </Button>
        <Button type="button" variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close" disabled={busy}>
          <X />
        </Button>
      </form>

      {!preview && !busy ? (
        <div className="flex flex-wrap gap-1.5 pl-7">
          {IDEAS.map((idea) => (
            <button
              key={idea}
              type="button"
              onClick={() => void submit(idea)}
              className="rounded-full border border-border bg-background/60 px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:border-foreground/20 hover:text-foreground"
            >
              {idea}
            </button>
          ))}
        </div>
      ) : null}

      {preview && !preview.result.ok ? (
        <div className="space-y-1 rounded-xl bg-background/70 px-4 py-3 text-sm">
          <p>{preview.result.reason}</p>
          {preview.result.alternative ? <p className="text-xs text-muted-foreground">{preview.result.alternative}</p> : null}
        </div>
      ) : null}

      {preview && preview.result.ok ? (
        <div className="space-y-3 rounded-xl bg-background/70 p-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">{preview.result.note || 'Archie’s change'}</p>
              <p className="text-xs text-muted-foreground">Nothing changes until you apply it.</p>
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

// ── the result ─────────────────────────────────────────────────────────
function useRunReport(run: RecipeRun | null) {
  const [state, setState] = useState<{ content: string | null; summary: ReportSummary | null; error: boolean }>({ content: null, summary: null, error: false })
  useEffect(() => {
    setState({ content: null, summary: null, error: false })
    if (!run?.output_path) return
    let alive = true
    getArtifact(resolveArtifactRelativePath(run.output_path)).then(
      (d) => alive && setState({ content: d.content.replace(/^---[\s\S]*?---\s*/, ''), summary: (d.metadata?.summary as ReportSummary | undefined) ?? null, error: false }),
      () => alive && setState({ content: null, summary: null, error: true }),
    )
    return () => {
      alive = false
    }
  }, [run?.id, run?.output_path])
  return state
}

function ResultCard({ recipe, run, last, running, busy, onOpen, onRun }: {
  recipe: RecipeDetail
  run: RecipeRun | null
  last: RecipeRun | null
  running: boolean
  busy: boolean
  onOpen: (r: RecipeRun) => void
  onRun: () => void
}) {
  const { content, summary } = useRunReport(run)
  const doc = useReportDoc(content)
  const failedSince = last && last.status === 'error' && (!run || last.id !== run.id) ? last : null

  if (!run) return <SampleResult recipe={recipe} running={running} busy={busy} failed={failedSince} onRun={onRun} />

  return (
    <section aria-labelledby="latest-result" className="overflow-hidden rounded-2xl border border-border bg-card">
      <div className="flex items-center justify-between gap-3 px-5 pt-4 sm:px-6">
        <h2 id="latest-result" className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Latest result
        </h2>
        <span className="text-xs text-muted-foreground">
          {running ? (
            <span className="inline-flex items-center gap-1.5 text-primary">
              <StatusDot status="running" /> Running a fresh one…
            </span>
          ) : (
            <span title={utc(run.at).format('dddd D MMMM, HH:mm')}>{utc(run.at).format('ddd D MMM · HH:mm')}</span>
          )}
        </span>
      </div>
      {failedSince ? (
        <p className="mx-5 mt-3 flex items-start gap-2 rounded-lg bg-negative/5 px-3 py-2 text-xs text-negative sm:mx-6">
          <StatusDot status="error" className="mt-1" /> The last run {ago(failedSince.at)} didn’t finish: {failedSince.message}
        </p>
      ) : null}
      {run.trigger_context ? (
        <p className="mx-5 mt-3 text-xs text-muted-foreground sm:mx-6">
          <span className="font-medium text-foreground">Why it ran: </span>
          {run.trigger_context.split('\n').slice(1).join(' · ') || run.trigger_context}
        </p>
      ) : null}
      <div className="relative max-h-[520px] overflow-hidden px-5 pb-2 pt-3 sm:px-6 [&_.rc-toc]:hidden">
        {doc && content ? (
          <ReportBody doc={doc} markdown={content} summary={summary} showKeyNumbers />
        ) : content === null && run.output_path ? (
          <div className="space-y-3 py-2">
            <Skeleton className="h-20 w-full rounded-xl" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-5/6" />
          </div>
        ) : (
          <p className="py-2 text-sm text-muted-foreground">{run.summary}</p>
        )}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-card via-card/80 to-transparent" />
      </div>
      <div className="flex items-center justify-between gap-3 border-t border-border/60 px-5 py-3 sm:px-6">
        <span className="text-xs text-muted-foreground">{run.version ? `Made with version ${run.version}` : ''}</span>
        <button type="button" onClick={() => onOpen(run)} className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline hover:underline-offset-4">
          Read the full report <ArrowRight className="size-3.5" />
        </button>
      </div>
    </section>
  )
}

/** Before the first run: a calm sample of what the report will contain, from the recipe's own Output section. */
function SampleResult({ recipe, running, busy, failed, onRun }: { recipe: RecipeDetail; running: boolean; busy: boolean; failed: RecipeRun | null; onRun: () => void }) {
  const parts = outputParts(recipe.body).slice(0, 4)
  const widths = ['w-11/12', 'w-4/5', 'w-2/3', 'w-3/4', 'w-5/6']
  return (
    <section aria-labelledby="sample-result" className="relative overflow-hidden rounded-2xl border border-border bg-card">
      <div className="recipe-paper" aria-hidden />
      <div className="relative grid gap-6 p-5 sm:p-7 md:grid-cols-[minmax(0,1fr)_15rem] md:gap-10">
        <div className={cn('space-y-5', running && 'recipe-writing')} aria-hidden={!parts.length}>
          <div className="flex items-center gap-2">
            <span className="rounded-full border border-dashed border-border px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">Sample</span>
            <span className="text-xs text-muted-foreground">what each result looks like</span>
          </div>
          <div className="rounded-xl border border-border/70 bg-background/40 p-4">
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-primary/80">TL;DR</p>
            <div className="mt-2 space-y-1.5">
              <div className="recipe-line h-3 w-10/12" />
              <div className="recipe-line h-3 w-7/12" />
            </div>
          </div>
          {(parts.length ? parts : [{ title: 'What matters', hint: '' }, { title: 'What to do', hint: '' }]).map((p, i) => (
            <div key={p.title} className="space-y-2">
              <p className="text-sm font-medium text-foreground/80">{p.title}</p>
              {p.hint ? <p className="max-w-[56ch] text-xs leading-relaxed text-muted-foreground/90">{p.hint}</p> : null}
              <div className="space-y-1.5 pt-0.5">
                <div className={cn('recipe-line h-2.5', widths[i % widths.length])} />
                <div className={cn('recipe-line h-2.5', widths[(i + 2) % widths.length])} />
              </div>
            </div>
          ))}
        </div>
        <div className="flex flex-col gap-5 md:border-l md:border-border/60 md:pl-8 md:pt-1">
          <div className="space-y-2">
            <h2 id="sample-result" className="text-base font-semibold tracking-tight">
              {running ? 'Archie is on it' : 'No results yet'}
            </h2>
            <p className="text-sm leading-relaxed text-muted-foreground">
              {running
                ? 'Usually a minute or two. The report lands here, on Home and in Activity.'
                : recipe.enabled && recipe.next_run_at
                  ? `The first one arrives ${nextRunLabel(recipe.next_run_at)}. Or run it now to see it straight away.`
                  : 'Run it once to see exactly what you’ll get.'}
            </p>
            {failed ? <p className="text-xs text-negative">The last try {ago(failed.at)} didn’t finish: {failed.message}</p> : null}
          </div>
          <Button onClick={onRun} disabled={busy} className="w-full md:w-auto md:self-start">
            {busy ? <LoaderCircle className="animate-spin" /> : <Play />} {running ? 'Running…' : 'Run it now'}
          </Button>
        </div>
      </div>
    </section>
  )
}

// ── how Archie does it ─────────────────────────────────────────────────
function StepsTimeline({ recipe }: { recipe: RecipeDetail }) {
  const [raw, setRaw] = useState(false)
  const steps = humanSteps(recipe.body)
  return (
    <section aria-labelledby="how-it-works" className="space-y-4">
      <h2 id="how-it-works" className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        How Archie does it
      </h2>
      {steps.length ? (
        <ol className="recipe-steps">
          {steps.map((s, i) => (
            <li key={i} className="recipe-step" style={{ animationDelay: `${i * 50}ms` }}>
              <span className="recipe-step-n" aria-hidden>
                {i + 1}
              </span>
              <div className="min-w-0 pb-5">
                <p className="text-sm font-medium leading-snug">{s.title}</p>
                {s.detail ? <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">{s.detail}</p> : null}
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-sm text-muted-foreground">Archie follows the written instructions below.</p>
      )}
      <div className="space-y-3">
        <button type="button" onClick={() => setRaw((v) => !v)} aria-expanded={raw || !steps.length} className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground">
          <ChevronRight className={cn('size-3.5 transition-transform', (raw || !steps.length) && 'rotate-90')} />
          {raw || !steps.length ? 'Hide the instructions' : 'Show the instructions'}
        </button>
        {raw || !steps.length ? (
          <div className="recipe-unfold rounded-xl border border-border bg-muted/30 px-4 py-3 text-sm [&_h1]:hidden [&_h2]:mt-3 [&_h2]:text-[13px] [&_h2]:font-semibold [&_li]:text-[13px] [&_p]:text-[13px]">
            <RichMarkdown markdown={recipe.body} />
            <p className="mt-3 flex items-center gap-1.5 border-t border-border/60 pt-2 text-[11px] text-muted-foreground">
              <ShieldCheck className="size-3" /> Instructions only. Archie can propose trades from a recipe, never place them.
            </p>
          </div>
        ) : null}
      </div>
    </section>
  )
}

// ── run history ────────────────────────────────────────────────────────
function RunsTimeline({ runs, running, onOpen }: { runs: RecipeRun[]; running: boolean; onOpen: (r: RecipeRun) => void }) {
  const [all, setAll] = useState(false)
  const shown = all ? runs : runs.slice(0, 6)
  return (
    <section aria-labelledby="runs" className="space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="runs" className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Runs
        </h2>
        {runs.length ? <span className="text-xs text-muted-foreground">{runs.length === 1 ? '1 so far' : `last ${runs.length}`}</span> : null}
      </div>
      {runs.length || running ? (
        <ol className="recipe-runs">
          {running ? (
            <li className="recipe-run">
              <StatusDot status="running" className="recipe-run-dot" />
              <span className="text-xs font-medium text-primary">Now</span>
              <span className="text-xs text-muted-foreground">Running…</span>
            </li>
          ) : null}
          {shown.map((r) => (
            <li key={r.id}>
              <button type="button" disabled={!r.output_path} onClick={() => onOpen(r)} className="recipe-run group w-full text-left disabled:cursor-default">
                <StatusDot status={r.status} className="recipe-run-dot" />
                <span className="whitespace-nowrap text-xs tabular-nums text-muted-foreground" title={utc(r.at).format('dddd D MMMM, HH:mm')}>
                  {utc(r.at).format('ddd D MMM')}
                </span>
                <span className={cn('min-w-0 truncate text-xs', r.status === 'error' ? 'text-negative' : 'text-foreground/85 group-enabled:group-hover:text-foreground')}>
                  {r.status === 'error' ? r.message || 'Didn’t finish' : r.summary || r.message || 'Done'}
                </span>
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-sm text-muted-foreground">Nothing yet. Each run shows up here with its headline.</p>
      )}
      {runs.length > 6 ? (
        <button type="button" onClick={() => setAll((v) => !v)} className="text-xs text-muted-foreground hover:text-foreground">
          {all ? 'Show fewer' : `Show all ${runs.length}`}
        </button>
      ) : null}
    </section>
  )
}

function RunReader({ run, title, onClose }: { run: RecipeRun | null; title: string; onClose: () => void }) {
  const { content, summary, error } = useRunReport(run)
  return (
    <ReportReader
      open={!!run}
      onOpenChange={(o) => !o && onClose()}
      title={title}
      kind="Recipe result"
      date={run ? `${utc(run.at).format('dddd D MMMM, HH:mm')}${run.version ? ` · version ${run.version}` : ''}` : null}
      markdown={content}
      summary={summary}
      loading={!!run?.output_path && content === null && !error}
      error={error ? 'The report file isn’t available any more.' : null}
      empty={run?.summary || run?.message}
    />
  )
}

// ── history (tucked away) ──────────────────────────────────────────────
function HistorySheet({ open, onOpenChange, slug, versions, current, onReverted }: {
  open: boolean
  onOpenChange: (o: boolean) => void
  slug: string
  versions: RecipeVersion[]
  current: number
  onReverted: () => Promise<void> | void
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>History</SheetTitle>
          <SheetDescription>Every change is a version. Restoring one saves it as a new version, so nothing is lost.</SheetDescription>
        </SheetHeader>
        <div className="px-4 pb-8">
          <VersionHistory slug={slug} versions={versions} current={current} onReverted={onReverted} />
        </div>
      </SheetContent>
    </Sheet>
  )
}

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
  )
}
