import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ArrowLeft, Check, LoaderCircle, MessageCircleQuestion, Send, ShieldCheck, TriangleAlert } from 'lucide-react'

import { createRecipe, draftRecipe } from '@/api/recipes'
import { RichMarkdown } from '@/components/RichMarkdown'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { toastApiError } from '@/lib/api-error'
import { navigate } from '@/lib/router'
import type { DraftResult, RecipeDraftFields } from '@/types/recipes'
import { AskComposer, EXAMPLES } from './gallery'
import { Chip, RecipeIcon } from './parts'

const THINKING = [
  'Reading your request…',
  'Choosing the tools Archie will need…',
  'Writing the steps…',
  'Shaping the output…',
  'Checking it against the guardrails…',
]

/** The quoted request inside an alternative (“…”), if there is one. */
function quoted(text: string): string | null {
  const m = text.match(/[“"]([^”"]{8,})[”"]/)
  return m ? m[1] : null
}

function Thinking() {
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = window.setInterval(() => setI((n) => Math.min(n + 1, THINKING.length - 1)), 3500)
    return () => window.clearInterval(t)
  }, [])
  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-center gap-2.5">
        <span className="relative flex size-2">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-primary/60" />
          <span className="relative inline-flex size-2 rounded-full bg-primary" />
        </span>
        <p className="text-sm font-semibold">Archie is writing your recipe</p>
      </div>
      <ol className="mt-4 space-y-2">
        {THINKING.slice(0, i + 1).map((line, idx) => (
          <li key={line} className="flex items-center gap-2.5 text-sm text-muted-foreground">
            {idx === i ? <LoaderCircle className="size-3.5 animate-spin text-primary" /> : <Check className="size-3.5 text-positive" />}
            <span className={idx === i ? 'text-foreground' : undefined}>{line}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}

/** `/recipes/new` — ask in a sentence → preview → save. */
export function NewRecipe({ initialPrompt, autoStart }: { initialPrompt: string; autoStart: boolean }) {
  const [prompt, setPrompt] = useState(initialPrompt)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<DraftResult | null>(null)
  const [saving, setSaving] = useState(false)
  const [telegram, setTelegram] = useState(false)
  const started = useRef(false)

  const run = async (text: string) => {
    setPrompt(text)
    setBusy(true)
    setResult(null)
    try {
      const out = await draftRecipe(text)
      setResult(out)
      if (out.ok) setTelegram(Boolean(out.draft.delivery.telegram))
    } catch (err) {
      toastApiError(err, 'Archie couldn’t draft that')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (autoStart && initialPrompt && !started.current) {
      started.current = true
      navigate(`/recipes/new?prompt=${encodeURIComponent(initialPrompt)}`, { replace: true })
      void run(initialPrompt)
    }
  }, [autoStart, initialPrompt]) // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (draft: RecipeDraftFields) => {
    setSaving(true)
    try {
      const recipe = await createRecipe({
        title: draft.title,
        summary: draft.summary,
        icon: draft.icon,
        body: draft.body,
        trigger: draft.trigger,
        delivery: { telegram },
        created_by: 'archie',
        note: `Asked for: “${prompt.slice(0, 200)}”`,
      })
      toast.success(`${recipe.title} is on`, { description: recipe.trigger_summary })
      navigate(`/recipes/${recipe.slug}`)
    } catch (err) {
      toastApiError(err, 'Could not save the recipe')
      setSaving(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8 pt-2 sm:pt-6">
      <button type="button" onClick={() => navigate('/recipes')} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Recipes
      </button>
      <div className="space-y-2 text-center">
        <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">New recipe</p>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">What should Archie do for you?</h1>
        <p className="text-sm text-muted-foreground">Describe the job and when. Archie writes the recipe; you check it before it goes live.</p>
      </div>

      <AskComposer size="lg" autoFocus={!autoStart} initial={prompt} busy={busy} onSubmit={(p) => void run(p)} />

      {!result && !busy ? (
        <div className="space-y-3">
          <p className="text-center text-xs text-muted-foreground">Try one of these</p>
          <div className="flex flex-wrap justify-center gap-2">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => void run(ex)}
                className="rounded-full border border-border bg-card/50 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-foreground/20 hover:text-foreground"
              >
                {ex}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {busy ? <Thinking /> : null}

      {result && !result.ok ? (
        <div className="space-y-4 rounded-xl border border-border bg-card p-5">
          <div className="flex items-start gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-warning/12 text-warning ring-1 ring-warning/25">
              <MessageCircleQuestion className="size-4" />
            </span>
            <div className="min-w-0 space-y-1">
              <p className="text-sm font-semibold">That’s not one Archie can take on as asked</p>
              <p className="text-sm leading-relaxed text-muted-foreground">{result.reason}</p>
            </div>
          </div>
          {result.alternative ? (
            <div className="rounded-lg border border-dashed border-border px-3.5 py-3">
              <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Instead, try</p>
              <p className="mt-1 text-sm">{result.alternative}</p>
              {quoted(result.alternative) ? (
                <Button variant="outline" size="sm" className="mt-3" onClick={() => void run(quoted(result.alternative)!)}>
                  <Send /> Use this instead
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {result && result.ok ? (
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <div className="space-y-4 p-5">
            <div className="flex items-start gap-3">
              <RecipeIcon icon={result.draft.icon} size="lg" />
              <div className="min-w-0">
                <p className="text-lg font-semibold tracking-tight">{result.draft.title}</p>
                <p className="mt-0.5 text-sm leading-relaxed text-muted-foreground">{result.draft.summary}</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Chip>{result.draft.trigger_summary}</Chip>
              <Chip>Lands on Home</Chip>
              <Chip>
                <ShieldCheck className="size-3" /> Proposes only
              </Chip>
            </div>
            {result.warnings.length ? (
              <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" />
                <span>{result.warnings.join(' · ')}</span>
              </div>
            ) : null}
            <details className="group rounded-lg border border-border/80 bg-background/50">
              <summary className="cursor-pointer select-none px-3.5 py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground">
                The instructions Archie will follow
              </summary>
              <div className="border-t border-border/70 px-4 py-3 text-sm [&_h2]:mt-3 [&_h2]:text-sm">
                <RichMarkdown markdown={result.draft.body} />
              </div>
            </details>
            <label className="flex items-center justify-between gap-3 text-sm">
              <span>
                Also send to Telegram
                <span className="block text-xs text-muted-foreground">Results always land on Home and in Activity.</span>
              </span>
              <Switch checked={telegram} onCheckedChange={setTelegram} />
            </label>
          </div>
          <div className="flex items-center justify-between gap-3 border-t border-border bg-muted/30 px-5 py-3">
            <Button variant="ghost" size="sm" onClick={() => setResult(null)}>
              Discard
            </Button>
            <Button onClick={() => void save(result.draft)} disabled={saving}>
              {saving ? <LoaderCircle className="animate-spin" /> : <Check />}
              {result.draft.trigger.kind === 'manual' ? 'Save recipe' : 'Save & turn on'}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
