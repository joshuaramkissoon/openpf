import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { ArrowUp, ChevronRight, Cog, Plus, ShieldCheck, Sparkles } from 'lucide-react'

import { listRecipes, setRecipeEnabled } from '@/api/recipes'
import { ScheduledJobsWorkspace } from '@/components/ScheduledJobsWorkspace'
import { PageHeader } from '@/components/kit'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { toastApiError } from '@/lib/api-error'
import { navigate } from '@/lib/router'
import { cn } from '@/lib/utils'
import type { Recipe, RecipeSuggestion } from '@/types/recipes'
import { Chip, LastRunLine, nextRunLabel, RecipeIcon, whenSentence } from './parts'
import { SuggestionList } from './suggestions'

export const EXAMPLES = [
  'Every Friday, check my plays against their kill rules and tell me what moved',
  'When a holding drops 5% in a day, find out why',
  'First of the month: how did each account do vs the S&P?',
  'Before the Fed meets, tell me what it could mean for my book',
  'Every Sunday evening, a 5-line preview of the week ahead',
]

const SYSTEM_KINDS = ['recipe']

export function useRecipes() {
  const [recipes, setRecipes] = useState<Recipe[] | null>(null)
  const [suggestions, setSuggestions] = useState<RecipeSuggestion[]>([])
  const reload = useCallback(async () => {
    try {
      const data = await listRecipes()
      setRecipes(data.recipes)
      setSuggestions(data.suggestions)
    } catch (err) {
      setRecipes((prev) => prev ?? [])
      toastApiError(err, 'Could not load recipes')
    }
  }, [])
  useEffect(() => {
    void reload()
  }, [reload])
  return { recipes, suggestions, reload, setRecipes }
}

export function AskComposer({ autoFocus = false, initial = '', onSubmit, busy = false, size = 'md' }: {
  autoFocus?: boolean
  initial?: string
  onSubmit: (prompt: string) => void
  busy?: boolean
  size?: 'md' | 'lg'
}) {
  const [prompt, setPrompt] = useState(initial)
  useEffect(() => setPrompt(initial), [initial])
  const submit = () => {
    const v = prompt.trim()
    if (v && !busy) onSubmit(v)
  }
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
      className="rounded-xl border border-border bg-card p-1.5 transition-colors focus-within:border-primary/50 focus-within:ring-3 focus-within:ring-primary/15"
    >
      <div className={cn('flex gap-2', size === 'lg' ? 'items-start' : 'items-end')}>
        <Sparkles className={cn('ml-2 size-4 shrink-0 text-primary', size === 'lg' ? 'mt-2.5' : 'mb-2.5')} />
        <textarea
          autoFocus={autoFocus}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
          rows={size === 'lg' ? 3 : 1}
          maxLength={2000}
          disabled={busy}
          placeholder="Ask Archie for a recipe — “every Friday, check my plays”"
          className={cn(
            'block max-h-48 min-w-0 flex-1 resize-none bg-transparent py-2 [field-sizing:content] text-sm leading-relaxed outline-none placeholder:text-muted-foreground disabled:opacity-60',
            size === 'lg' && 'min-h-20 text-[15px]',
          )}
        />
        <Button type="submit" size="icon-sm" className={cn('mr-0.5', size === 'lg' ? 'mt-auto mb-1' : 'mb-1')} disabled={!prompt.trim() || busy} aria-label="Ask Archie">
          <ArrowUp />
        </Button>
      </div>
    </form>
  )
}

/** `/recipes` — what Archie does for you, then what you could turn on. */
export function RecipesGallery() {
  const { recipes, suggestions, reload, setRecipes } = useRecipes()
  const [systemOpen, setSystemOpen] = useState(false)

  const toggle = async (r: Recipe, enabled: boolean) => {
    setRecipes((prev) => prev?.map((x) => (x.slug === r.slug ? { ...x, enabled } : x)) ?? prev)
    try {
      const next = await setRecipeEnabled(r.slug, enabled)
      setRecipes((prev) => prev?.map((x) => (x.slug === r.slug ? next : x)) ?? prev)
      toast.success(enabled ? `${r.title} is on` : `${r.title} paused`, {
        description: enabled ? whenSentence(next) : undefined,
      })
    } catch (err) {
      toastApiError(err, 'Could not update the recipe')
      void reload()
    }
  }

  const all = recipes ?? []
  const on = all.filter((r) => r.enabled)
  const paused = all.filter((r) => !r.enabled && r.source !== 'builtin')
  const available = all.filter((r) => !r.enabled && r.source === 'builtin')

  return (
    <div className="mx-auto max-w-5xl space-y-12 pb-12">
      <PageHeader
        eyebrow="Recipes"
        title="What Archie does for you"
        description="Routines he runs on his own, on a schedule or when something happens. He can propose trades, never place them."
        actions={
          <Button onClick={() => navigate('/recipes/new')} className="rounded-full">
            <Plus /> New recipe
          </Button>
        }
      />

      <SuggestionList suggestions={suggestions} onResolved={() => void reload()} />

      <section className="space-y-3" aria-labelledby="running">
        <SectionTitle id="running" title="Running for you" count={recipes ? on.length : undefined} />
        {!recipes ? (
          <Skeleton className="h-[148px] rounded-2xl" />
        ) : on.length ? (
          <div className="divide-y divide-border/60 overflow-hidden rounded-2xl border border-border bg-card">
            {on.map((r) => (
              <RecipeRow key={r.slug} recipe={r} onToggle={(v) => toggle(r, v)} />
            ))}
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-border px-5 py-6 text-sm text-muted-foreground">
            Nothing is running yet. Turn on a routine below, or describe your own with <span className="text-foreground">New recipe</span>.
          </div>
        )}
      </section>

      {paused.length ? (
        <section className="space-y-3" aria-labelledby="paused">
          <SectionTitle id="paused" title="Paused" count={paused.length} />
          <div className="divide-y divide-border/60 overflow-hidden rounded-2xl border border-border/70">
            {paused.map((r) => (
              <RecipeRow key={r.slug} recipe={r} onToggle={(v) => toggle(r, v)} />
            ))}
          </div>
        </section>
      ) : null}

      {!recipes || available.length ? (
        <section className="space-y-3" aria-labelledby="available">
          <SectionTitle id="available" title="Ready to turn on" hint="Ready-made routines. Turn one on, then make it yours with a sentence." />
          {!recipes ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-[132px] rounded-2xl" />
              ))}
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {available.map((r) => (
                <RecipeCard key={r.slug} recipe={r} onToggle={(v) => toggle(r, v)} />
              ))}
            </div>
          )}
        </section>
      ) : null}

      <footer className="space-y-4 border-t border-border/60 pt-6">
        <button type="button" onClick={() => setSystemOpen((v) => !v)} className="flex items-center gap-1.5 text-left text-xs text-muted-foreground hover:text-foreground" aria-expanded={systemOpen}>
          <ChevronRight className={cn('size-3.5 transition-transform', systemOpen && 'rotate-90')} />
          <Cog className="size-3.5" /> System jobs <span className="text-muted-foreground/70">· watch cycle, leveraged monitors, alpha loop</span>
        </button>
        {systemOpen ? <ScheduledJobsWorkspace onError={() => {}} jobsOnly excludeKinds={SYSTEM_KINDS} /> : null}
        <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground/80">
          <ShieldCheck className="mt-px size-3.5 shrink-0" />
          Recipes are instructions only: they can’t add tools, change your risk rails or place orders. Changes Archie suggests during unattended runs wait for your approval.
        </p>
      </footer>
    </div>
  )
}

function SectionTitle({ id, title, count, hint }: { id: string; title: string; count?: number; hint?: string }) {
  return (
    <div>
      <h2 id={id} className="flex items-baseline gap-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {title}
        {count != null ? <span className="font-mono tabular-nums text-muted-foreground/70">{count}</span> : null}
      </h2>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

function RecipeRow({ recipe: r, onToggle }: { recipe: Recipe; onToggle: (v: boolean) => void }) {
  return (
    <div className="group flex items-center gap-4 px-4 py-3.5 transition-colors hover:bg-accent/30 sm:px-5">
      <button type="button" onClick={() => navigate(`/recipes/${r.slug}`)} className="flex min-w-0 flex-1 items-center gap-3.5 text-left">
        <RecipeIcon icon={r.icon} muted={!r.enabled} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className={cn('truncate text-sm font-medium', !r.enabled && 'text-muted-foreground')}>{r.title}</span>
            {r.pending_suggestions ? <Chip className="border-primary/30 text-primary">suggestion</Chip> : null}
          </span>
          <span className="block truncate text-xs text-muted-foreground">
            {whenSentence(r)}
            {r.enabled && r.next_run_at ? <span className="text-muted-foreground/70"> · next {nextRunLabel(r.next_run_at)}</span> : null}
          </span>
        </span>
        <LastRunLine run={r.last_run} running={r.running} className="hidden w-64 shrink-0 md:flex" />
      </button>
      <Switch checked={r.enabled} onCheckedChange={onToggle} aria-label={r.enabled ? `Pause ${r.title}` : `Turn on ${r.title}`} />
    </div>
  )
}

function RecipeCard({ recipe: r, onToggle }: { recipe: Recipe; onToggle: (v: boolean) => void }) {
  return (
    <div className="group relative flex min-h-[132px] min-w-0 flex-col gap-3 rounded-2xl border border-border/70 bg-card/40 p-4 transition-colors hover:border-foreground/15 hover:bg-card">
      <div className="flex items-start justify-between gap-3">
        <RecipeIcon icon={r.icon} muted />
        <span className="relative z-10 flex">
          <Switch checked={r.enabled} onCheckedChange={onToggle} aria-label={`Turn on ${r.title}`} />
        </span>
      </div>
      <button type="button" onClick={() => navigate(`/recipes/${r.slug}`)} className="flex min-w-0 flex-1 flex-col text-left after:absolute after:inset-0 after:content-['']">
        <span className="text-sm font-semibold">{r.title}</span>
        <span className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{r.summary}</span>
        <span className="mt-auto pt-2 text-[11px] text-muted-foreground/80">{whenSentence(r)}</span>
      </button>
    </div>
  )
}
