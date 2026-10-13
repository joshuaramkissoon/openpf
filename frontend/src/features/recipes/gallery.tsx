import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { ArrowUp, Bot, ChevronRight, Cog, ShieldCheck, Sparkles } from 'lucide-react'

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
import { Chip, LastRunLine, nextRunLabel, RecipeIcon } from './parts'
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

/** `/recipes` — what Archie does on his own, and the gallery to add more. */
export function RecipesGallery() {
  const { recipes, suggestions, reload, setRecipes } = useRecipes()
  const [systemOpen, setSystemOpen] = useState(false)

  const toggle = async (r: Recipe, enabled: boolean) => {
    setRecipes((prev) => prev?.map((x) => (x.slug === r.slug ? { ...x, enabled } : x)) ?? prev)
    try {
      const next = await setRecipeEnabled(r.slug, enabled)
      setRecipes((prev) => prev?.map((x) => (x.slug === r.slug ? next : x)) ?? prev)
      toast.success(enabled ? `${r.title} is on` : `${r.title} paused`, {
        description: enabled ? (r.trigger.kind === 'manual' ? 'Runs when you press Run now.' : r.trigger_summary) : undefined,
      })
    } catch (err) {
      toastApiError(err, 'Could not update the recipe')
      void reload()
    }
  }

  const mine = (recipes ?? []).filter((r) => r.source !== 'builtin')
  const builtins = (recipes ?? []).filter((r) => r.source === 'builtin')
  const onCount = (recipes ?? []).filter((r) => r.enabled).length

  return (
    <div className="space-y-10">
      <div className="space-y-5">
        <PageHeader
          eyebrow="Recipes"
          title="What Archie does for you"
          description={
            <>
              Routines Archie runs on his own — on a schedule, or when something happens. He can propose trades, never place them.
              {recipes ? <span className="text-muted-foreground/80"> · {onCount} on</span> : null}
            </>
          }
        />
        <AskComposer onSubmit={(p) => navigate(`/recipes/new?prompt=${encodeURIComponent(p)}&go=1`)} />
        <div className="flex flex-wrap gap-1.5">
          {EXAMPLES.slice(0, 3).map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => navigate(`/recipes/new?prompt=${encodeURIComponent(ex)}&go=1`)}
              className="rounded-full border border-border bg-card/50 px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:border-foreground/20 hover:text-foreground"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>

      <SuggestionList suggestions={suggestions} onResolved={() => void reload()} />

      <section className="space-y-3">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-1.5 text-sm font-semibold tracking-tight">
              <Bot className="size-3.5 text-muted-foreground" /> Made by Archie
            </h2>
            <p className="text-xs text-muted-foreground">Recipes you asked for. Edit any of them with a sentence.</p>
          </div>
        </div>
        {!recipes ? (
          <Skeleton className="h-[72px] rounded-xl" />
        ) : mine.length ? (
          <div className="divide-y divide-border/70 overflow-hidden rounded-xl border border-border bg-card">
            {mine.map((r) => (
              <RecipeRow key={r.slug} recipe={r} onToggle={(v) => toggle(r, v)} />
            ))}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-border px-5 py-6 text-sm text-muted-foreground">
            <p className="font-medium text-foreground">Nothing yet.</p>
            <p className="mt-1">
              Ask above, or tell Archie in chat — “every Friday, check my plays and tell me what moved”. He writes the recipe, you approve it,
              and you can refine it any time (“shorter”, “skip crypto”).
            </p>
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">Built-in</h2>
          <p className="text-xs text-muted-foreground">Ready-made routines. Turn one on, then make it yours — every edit is versioned.</p>
        </div>
        {!recipes ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="h-[168px] rounded-xl" />
            ))}
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {builtins.map((r) => (
              <RecipeCard key={r.slug} recipe={r} onToggle={(v) => toggle(r, v)} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <button type="button" onClick={() => setSystemOpen((v) => !v)} className="flex items-center gap-1.5 text-left" aria-expanded={systemOpen}>
          <ChevronRight className={cn('size-4 text-muted-foreground transition-transform', systemOpen && 'rotate-90')} />
          <span>
            <span className="flex items-center gap-1.5 text-sm font-semibold tracking-tight">
              <Cog className="size-3.5 text-muted-foreground" /> System jobs
            </span>
            <span className="block text-xs text-muted-foreground">Deterministic engines (watch cycle, leveraged monitors, alpha loop). Advanced.</span>
          </span>
        </button>
        {systemOpen ? <ScheduledJobsWorkspace onError={() => {}} jobsOnly excludeKinds={SYSTEM_KINDS} /> : null}
      </section>

      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <ShieldCheck className="size-3.5" />
        Recipes are instructions only: they can’t add tools, change your risk rails or place orders. Changes Archie suggests during unattended runs wait for your approval.
      </p>
    </div>
  )
}

function RecipeRow({ recipe: r, onToggle }: { recipe: Recipe; onToggle: (v: boolean) => void }) {
  return (
    <div className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-accent/40">
      <button type="button" onClick={() => navigate(`/recipes/${r.slug}`)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
        <RecipeIcon icon={r.icon} muted={!r.enabled} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className={cn('truncate text-sm font-medium', !r.enabled && 'text-muted-foreground')}>{r.title}</span>
            {r.pending_suggestions ? <Chip className="border-primary/30 text-primary">suggestion</Chip> : null}
          </span>
          <span className="block truncate text-xs text-muted-foreground">
            {r.trigger_summary}
            {r.enabled && r.next_run_at ? ` · next ${nextRunLabel(r.next_run_at)}` : ''}
          </span>
          <LastRunLine run={r.last_run} running={r.running} className="mt-0.5 hidden sm:flex" />
        </span>
      </button>
      <Switch checked={r.enabled} onCheckedChange={onToggle} aria-label={r.enabled ? `Pause ${r.title}` : `Turn on ${r.title}`} />
    </div>
  )
}

function RecipeCard({ recipe: r, onToggle }: { recipe: Recipe; onToggle: (v: boolean) => void }) {
  return (
    <div
      className={cn(
        'group relative flex min-h-[168px] min-w-0 flex-col gap-3 rounded-xl border bg-card p-4 transition-colors',
        r.enabled ? 'border-border' : 'border-border/70 bg-card/50',
        'hover:border-foreground/15',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <RecipeIcon icon={r.icon} muted={!r.enabled} />
        <span className="relative z-10 flex">
          <Switch checked={r.enabled} onCheckedChange={onToggle} aria-label={r.enabled ? `Pause ${r.title}` : `Turn on ${r.title}`} />
        </span>
      </div>
      <button type="button" onClick={() => navigate(`/recipes/${r.slug}`)} className="flex min-w-0 flex-1 flex-col text-left after:absolute after:inset-0 after:content-['']">
        <span className="flex items-center gap-1.5">
          <span className="text-sm font-semibold">{r.title}</span>
          {r.modified ? <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">· edited</span> : null}
        </span>
        <span className="mt-0.5 text-[11px] font-medium text-muted-foreground/90">{r.trigger_summary}</span>
        <span className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{r.summary}</span>
      </button>
      {r.enabled ? (
        <LastRunLine run={r.last_run} running={r.running} />
      ) : (
        <p className="text-xs text-muted-foreground/70">Off</p>
      )}
    </div>
  )
}
