import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Lightbulb, Loader2, RotateCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { parseApiError, toastApiError } from '@/lib/api-error'
import { useAppActions } from '@/lib/app-actions'
import { navigate as routerNavigate } from '@/lib/router'
import { getDraftJob, retryDraft, startDraft } from './api'
import { DraftProgressView } from './drafting'
import { StarterCryptoCard } from './starter-crypto'
import type { Conviction, DraftJob, PlayAccount } from './types'
import { Eyebrow, Segmented } from './ui'

export const EXAMPLE_IDEAS: { title: string; idea: string }[] = [
  {
    title: 'Nuclear renaissance',
    idea: 'Nuclear is back: uranium supply is tight, SMRs are getting funded, and AI datacentres need firm, carbon-free baseload power.',
  },
  {
    title: 'AI power & cooling',
    idea: 'The AI build-out is bottlenecked by power and cooling, not chips. Grid equipment, cooling and power-gen suppliers win regardless of which model lab does.',
  },
  {
    title: 'GLP-1 winners',
    idea: 'GLP-1 obesity drugs keep expanding. Back the makers and the supply chain (fill-finish, devices), and avoid the obvious losers.',
  },
]

export function NewPlay({ initialIdea, jobId }: { initialIdea?: string; jobId?: string | null }) {
  const { navigate } = useAppActions()
  const [idea, setIdea] = useState(initialIdea ?? '')
  const [budget, setBudget] = useState('')
  const [conviction, setConviction] = useState<Conviction | null>(null)
  const [horizon, setHorizon] = useState<string>('24')
  const [account, setAccount] = useState<PlayAccount>('stocks_isa')
  const [submitting, setSubmitting] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  if (jobId) return <Drafting jobId={jobId} />

  async function submit() {
    if (idea.trim().length < 3) {
      setProblem('Describe the theme in a few words — e.g. “Europe is rearming for a decade”.')
      return
    }
    setSubmitting(true)
    setProblem(null)
    try {
      const job = await startDraft({
        idea: idea.trim(),
        budget_gbp: budget && Number(budget) > 0 ? Number(budget) : null,
        conviction,
        horizon_months: Number(horizon) || null,
        account_kind: account,
      })
      navigate(`/plays?new=1&job=${job.job_id}`)
    } catch (e) {
      const status = (e as { response?: { status?: number } })?.response?.status
      if (status === 400 || status === 422) setProblem(parseApiError(e).message)
      else toastApiError(e, 'Could not start drafting')
      setSubmitting(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8 py-2">
      <button
        type="button"
        onClick={() => navigate('/plays')}
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Plays
      </button>

      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">What do you believe?</h1>
        <p className="text-sm text-muted-foreground">
          Describe the theme in plain English. Archie researches a basket, sizes it against your book and risk rails, stages
          the entry, and writes down what would prove you wrong. Nothing is bought until you approve it.
        </p>
      </div>

      <div className="space-y-3">
        <Textarea
          autoFocus
          value={idea}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? 'idea-problem' : undefined}
          onChange={(e) => {
            setIdea(e.target.value)
            if (problem) setProblem(null)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void submit()
          }}
          placeholder="e.g. Europe is rearming for a decade — defence primes and their suppliers will compound orders…"
          className="min-h-36 resize-y text-base leading-relaxed"
        />
        {problem ? (
          <p id="idea-problem" role="alert" className="flex items-start gap-2 text-sm text-warning animate-in fade-in duration-200">
            <Lightbulb className="mt-0.5 size-4 shrink-0" />
            <span>{problem}</span>
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {EXAMPLE_IDEAS.map((ex) => (
            <button
              key={ex.title}
              type="button"
              onClick={() => {
                setIdea(ex.idea)
                setProblem(null)
              }}
              className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground"
            >
              {ex.title}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
        <Field label="Budget" hint={budget ? 'Archie will flag it if it breaks your rails.' : 'Leave blank — Archie will size this.'}>
          <div className="relative">
            <span className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-sm text-muted-foreground">£</span>
            <Input
              inputMode="decimal"
              value={budget}
              onChange={(e) => setBudget(e.target.value.replace(/[^0-9.]/g, '').replace(/(\..*)\./g, '$1').slice(0, 10))}
              placeholder="Auto"
              className="pl-7 font-mono tabular-nums"
            />
          </div>
        </Field>
        <Field label="Conviction" hint={conviction ? undefined : 'Unset — Archie will suggest one.'}>
          <Segmented<Conviction>
            value={conviction}
            onChange={(v) => setConviction(v === conviction ? null : v)}
            options={[
              { value: 'low', label: 'Low' },
              { value: 'medium', label: 'Medium' },
              { value: 'high', label: 'High' },
            ]}
          />
        </Field>
        <Field label="Horizon">
          <Segmented<string>
            value={horizon}
            onChange={setHorizon}
            options={[
              { value: '6', label: '6m' },
              { value: '12', label: '1y' },
              { value: '24', label: '2y' },
              { value: '60', label: '5y' },
            ]}
          />
        </Field>
        <Field label="Account">
          <Segmented<PlayAccount>
            value={account}
            onChange={setAccount}
            options={[
              { value: 'stocks_isa', label: 'Stocks ISA' },
              { value: 'invest', label: 'Invest' },
            ]}
          />
        </Field>
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-border pt-5">
        <p className="text-xs text-muted-foreground">Takes about a minute.</p>
        <Button onClick={() => void submit()} disabled={submitting || idea.trim().length === 0}>
          {submitting ? <Loader2 className="size-4 animate-spin" /> : null}
          Draft the play
          <ArrowRight className="size-4" />
        </Button>
      </div>

      <div className="space-y-3 pt-4">
        <Eyebrow>Or start from a template</Eyebrow>
        <StarterCryptoCard />
      </div>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Eyebrow>{label}</Eyebrow>
      {children}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

function Drafting({ jobId }: { jobId: string }) {
  const { navigate } = useAppActions()
  const [job, setJob] = useState<DraftJob | null>(null)
  const [missing, setMissing] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const j = await getDraftJob(jobId)
        if (!active) return
        setJob(j)
        if (j.status === 'done' && j.play_id) {
          routerNavigate(`/plays/${j.play_id}`, { replace: true })
          return
        }
        if (j.status === 'error') return
      } catch {
        if (active) setMissing(true)
        return
      }
      timer.current = window.setTimeout(poll, 1500)
    }
    void poll()
    return () => {
      active = false
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [jobId])

  const [retrying, setRetrying] = useState(false)
  const failed = job?.status === 'error' || missing
  const kind = job?.error_kind
  const idea = job?.idea ?? job?.request?.idea ?? ''
  const heading = missing
    ? 'That draft has gone'
    : kind === 'unclear_idea'
      ? 'Archie needs a sharper idea'
      : kind === 'no_instruments'
        ? 'Nothing to buy on Trading 212'
        : kind === 'interrupted'
          ? 'Drafting was interrupted'
          : 'Archie could not finish this draft'

  async function retry() {
    setRetrying(true)
    try {
      const next = await retryDraft(jobId)
      routerNavigate(`/plays?new=1&job=${next.job_id}`, { replace: true })
    } catch (e) {
      toastApiError(e, 'Could not retry')
      setRetrying(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 py-10">
      <div className="space-y-2">
        <Eyebrow>{failed ? 'Drafting stopped' : 'Drafting your play'}</Eyebrow>
        <h1 className="text-2xl font-semibold tracking-tight">{failed ? heading : 'Archie is researching the theme'}</h1>
        <p className="text-sm text-muted-foreground">
          {failed
            ? missing
              ? 'It may have been cleared. Start again — your idea takes a minute to draft.'
              : job?.error || 'Something went wrong. Try again.'
            : 'Picking a basket, checking prices and fundamentals, then sizing it against your book. Takes about a minute.'}
        </p>
        {failed && idea ? (
          <p className="border-l-2 border-border pl-3 text-sm text-muted-foreground italic">“{idea}”</p>
        ) : null}
      </div>
      <DraftProgressView job={job} failed={failed} />
      {failed ? (
        <div className="flex flex-wrap items-center gap-2">
          {job?.retryable ? (
            <Button onClick={() => void retry()} disabled={retrying}>
              {retrying ? <Loader2 className="size-4 animate-spin" /> : <RotateCw className="size-4" />}
              Retry
            </Button>
          ) : null}
          <Button
            variant={job?.retryable ? 'ghost' : 'outline'}
            onClick={() => navigate(idea ? `/plays?new=1&idea=${encodeURIComponent(idea)}` : '/plays?new=1')}
          >
            {idea ? 'Edit the idea' : 'Start again'}
          </Button>
        </div>
      ) : null}
    </div>
  )
}
