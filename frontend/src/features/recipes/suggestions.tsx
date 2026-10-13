import { useState } from 'react'
import { toast } from 'sonner'
import { Check, ChevronRight, LoaderCircle, Sparkles, X } from 'lucide-react'

import { resolveSuggestion } from '@/api/recipes'
import { Button } from '@/components/ui/button'
import { toastApiError } from '@/lib/api-error'
import { cn } from '@/lib/utils'
import type { RecipeSuggestion } from '@/types/recipes'
import { ago, DiffView } from './parts'

const ACTION_LABEL: Record<RecipeSuggestion['action'], string> = {
  create: 'New recipe',
  update: 'Change',
  enable: 'Turn on',
  disable: 'Pause',
}

/**
 * Changes Archie suggested during an unattended run. Nothing here is live
 * until the user approves it — news text can't rewrite the harness.
 */
export function SuggestionList({ suggestions, onResolved }: { suggestions: RecipeSuggestion[]; onResolved: () => void }) {
  if (!suggestions.length) return null
  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <Sparkles className="size-3.5 text-primary" />
        <h2 className="text-sm font-semibold tracking-tight">
          Archie suggests {suggestions.length === 1 ? 'a change' : `${suggestions.length} changes`}
        </h2>
      </div>
      <p className="text-xs text-muted-foreground">Made during a scheduled run, so they wait for you. Nothing changes until you approve.</p>
      <div className="space-y-2 pt-1">
        {suggestions.map((s) => (
          <SuggestionCard key={s.id} suggestion={s} onResolved={onResolved} />
        ))}
      </div>
    </section>
  )
}

function SuggestionCard({ suggestion: s, onResolved }: { suggestion: RecipeSuggestion; onResolved: () => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<'accept' | 'dismiss' | null>(null)

  const act = async (decision: 'accept' | 'dismiss') => {
    setBusy(decision)
    try {
      await resolveSuggestion(s.id, decision)
      toast.success(decision === 'accept' ? 'Change applied — saved as a new version' : 'Suggestion dismissed')
      onResolved()
    } catch (err) {
      toastApiError(err, 'Could not update the suggestion')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="rounded-xl border border-primary/25 bg-primary/[0.04] px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <button type="button" onClick={() => setOpen((v) => !v)} className="flex min-w-0 flex-1 items-start gap-2 text-left" aria-expanded={open}>
          <ChevronRight className={cn('mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-90')} />
          <span className="min-w-0">
            <span className="block text-sm font-medium">
              <span className="text-muted-foreground">{ACTION_LABEL[s.action]}:</span> {s.title || s.slug || 'recipe'}
            </span>
            {s.reason ? <span className="mt-0.5 block text-xs text-muted-foreground">“{s.reason}” · {ago(s.created_at)}</span> : null}
          </span>
        </button>
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => act('dismiss')}>
            {busy === 'dismiss' ? <LoaderCircle className="animate-spin" /> : <X />} Dismiss
          </Button>
          <Button size="sm" disabled={!!busy} onClick={() => act('accept')}>
            {busy === 'accept' ? <LoaderCircle className="animate-spin" /> : <Check />} Approve
          </Button>
        </div>
      </div>
      {open && s.diff.length ? <DiffView lines={s.diff} className="mt-3" /> : null}
    </div>
  )
}
