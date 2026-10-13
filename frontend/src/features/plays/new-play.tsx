import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  CircleCheck,
  Clock3,
  Gauge,
  Landmark,
  Lightbulb,
  Loader2,
  type LucideIcon,
  RotateCw,
  Scale,
  Search,
  ShieldCheck,
  Wallet,
  X,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { parseApiError, toastApiError } from '@/lib/api-error'
import { useAppActions } from '@/lib/app-actions'
import { navigate as routerNavigate } from '@/lib/router'
import { cn } from '@/lib/utils'
import { getDraftJob, retryDraft, startDraft } from './api'
import { DraftProgressView } from './drafting'
import { StarterCryptoDialog } from './starter-crypto'
import {
  EXAMPLE_IDEAS,
  HORIZONS,
  PLACEHOLDER_IDEAS,
  PLAY_TEMPLATES,
  type PlayTemplate,
  RISK_LABEL,
  horizonLabel,
} from './templates'
import type { Conviction, DraftJob, PlayAccount } from './types'
import { Eyebrow } from './ui'


const STEPS: { Icon: LucideIcon; text: string }[] = [
  { Icon: Search, text: 'Archie finds the names' },
  { Icon: Scale, text: 'Sizes it to your book' },
  { Icon: CircleCheck, text: 'You approve each buy' },
]

const CONVICTIONS: { value: Conviction; label: string; hint: string }[] = [
  { value: 'low', label: 'Low', hint: 'A small first step' },
  { value: 'medium', label: 'Medium', hint: 'A meaningful slice' },
  { value: 'high', label: 'High', hint: 'One of your bigger bets' },
]

const ACCOUNTS: { value: PlayAccount; label: string; hint: string }[] = [
  { value: 'stocks_isa', label: 'Stocks ISA', hint: 'No tax on gains or income' },
  { value: 'invest', label: 'Invest', hint: 'Your general account' },
]

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

/** A wash of the template's palette colour, for icon tiles. */
function tint(color: number, pct = 14) {
  return { background: `color-mix(in oklab, var(--chart-${color}) ${pct}%, transparent)`, color: `var(--chart-${color})` }
}

export function NewPlay({
  initialIdea,
  templateId,
  jobId,
}: {
  initialIdea?: string
  templateId?: string | null
  jobId?: string | null
}) {
  if (jobId) return <Drafting jobId={jobId} />
  return <NewPlayForm initialIdea={initialIdea} templateId={templateId} />
}

function NewPlayForm({ initialIdea, templateId }: { initialIdea?: string; templateId?: string | null }) {
  const { navigate } = useAppActions()
  const initialTemplate = PLAY_TEMPLATES.find((t) => t.id === templateId && t.kind !== 'starter_crypto')
  const [idea, setIdea] = useState(initialIdea ?? initialTemplate?.thesis ?? '')
  const [budget, setBudget] = useState('')
  const [conviction, setConviction] = useState<Conviction | null>(initialTemplate?.conviction ?? null)
  const [horizon, setHorizon] = useState<string>(String(initialTemplate?.horizonMonths ?? 24))
  const [account, setAccount] = useState<PlayAccount>(initialTemplate?.account ?? 'stocks_isa')
  const [template, setTemplate] = useState<PlayTemplate | null>(initialTemplate ?? null)
  const [cryptoOpen, setCryptoOpen] = useState(templateId === 'starter-crypto')
  const [submitting, setSubmitting] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [flash, setFlash] = useState(0)
  const box = useRef<HTMLTextAreaElement>(null)

  async function submit() {
    if (idea.trim().length < 3) {
      setProblem('Say a little more, e.g. “Europe is rearming for a decade”.')
      box.current?.focus()
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

  function fill(text: string) {
    setIdea(text)
    setProblem(null)
    requestAnimationFrame(() => {
      const el = box.current
      if (!el) return
      el.focus()
      el.setSelectionRange(text.length, text.length)
    })
  }

  function applyTemplate(t: PlayTemplate) {
    if (t.kind === 'starter_crypto') {
      setCryptoOpen(true)
      return
    }
    setTemplate(t)
    setHorizon(String(t.horizonMonths))
    setConviction(t.conviction)
    if (t.account) setAccount(t.account)
    fill(t.thesis)
    setFlash((n) => n + 1)
    box.current?.closest('section')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="relative isolate mx-auto max-w-3xl pb-10">
      {/* A faint wash of the brand colour behind the hero. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-24 -z-10 h-[30rem]"
        style={{
          background:
            'radial-gradient(closest-side, color-mix(in oklab, var(--primary) 18%, transparent), transparent)',
        }}
      />

      <button
        type="button"
        onClick={() => navigate('/plays')}
        className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Plays
      </button>

      <header className="mt-8 animate-in fade-in slide-in-from-bottom-1 duration-500 fill-mode-both">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-[2.5rem] sm:leading-[1.1]">What do you believe?</h1>
        <p className="mt-2.5 text-base text-muted-foreground">Write it in a sentence. Archie does the research and builds the plan.</p>
        <ol className="mt-5 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-muted-foreground" aria-label="How it works">
          {STEPS.map(({ Icon, text }, i) => (
            <li key={text} className="flex items-center gap-3">
              {i > 0 ? <span aria-hidden className="hidden h-px w-5 bg-border sm:block" /> : null}
              <span className="inline-flex items-center gap-1.5">
                <Icon className="size-3.5 text-foreground/70" />
                {text}
              </span>
            </li>
          ))}
        </ol>
      </header>

      <section
        className="mt-8 scroll-mt-6 animate-in fade-in slide-in-from-bottom-2 duration-500 fill-mode-both [animation-delay:80ms]"
        aria-label="Your idea"
      >
        <div
          key={flash}
          className={cn(
            'rounded-2xl border bg-card shadow-sm transition-[border-color,box-shadow] duration-200 focus-within:border-foreground/25 focus-within:shadow-md',
            problem ? 'border-warning/50' : 'border-border',
            flash > 0 && 'animate-in zoom-in-[0.99] duration-300',
          )}
        >
          {template ? (
            <div className="flex items-center gap-2 px-5 pt-4">
              <span className="inline-flex items-center gap-1.5 rounded-full py-0.5 pr-1 pl-2 text-[11px] font-medium" style={tint(template.color, 12)}>
                <template.Icon className="size-3" />
                {template.name}
                <button
                  type="button"
                  aria-label="Clear template"
                  onClick={() => setTemplate(null)}
                  className="rounded-full p-0.5 opacity-70 transition-opacity hover:opacity-100"
                >
                  <X className="size-3" />
                </button>
              </span>
              <span className="text-[11px] text-muted-foreground">Make it yours: edit anything.</span>
            </div>
          ) : null}
          <div className="relative">
            <textarea
              ref={box}
              autoFocus
              value={idea}
              rows={4}
              aria-label="Your idea"
              aria-invalid={problem ? true : undefined}
              aria-describedby={problem ? 'idea-problem' : undefined}
              onChange={(e) => {
                setIdea(e.target.value)
                if (problem) setProblem(null)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault()
                  void submit()
                }
              }}
              className="block field-sizing-content min-h-32 w-full resize-none bg-transparent px-5 pt-4 pb-3 text-lg leading-relaxed tracking-[-0.01em] text-foreground outline-none sm:text-xl sm:leading-relaxed"
            />
            {idea ? null : <RotatingPlaceholder />}
          </div>

          <div className="flex flex-wrap items-center gap-1.5 border-t border-border/70 px-3.5 py-3">
            <BudgetPill budget={budget} setBudget={setBudget} />
            <ChoicePill<Conviction>
              Icon={Gauge}
              label={conviction ? `${CONVICTIONS.find((c) => c.value === conviction)?.label} conviction` : 'Auto conviction'}
              set={conviction != null}
              title="How sure are you?"
              value={conviction}
              options={CONVICTIONS}
              onChange={setConviction}
              auto={{ label: 'Let Archie suggest', onSelect: () => setConviction(null) }}
            />
            <ChoicePill<string>
              Icon={Clock3}
              label={horizonLabel(Number(horizon))}
              title="How long will you hold it?"
              value={horizon}
              options={HORIZONS.map((h) => ({ value: String(h.months), label: h.long }))}
              onChange={setHorizon}
            />
            <ChoicePill<PlayAccount>
              Icon={Landmark}
              label={ACCOUNTS.find((a) => a.value === account)?.label ?? 'Account'}
              title="Which account?"
              value={account}
              options={ACCOUNTS}
              onChange={setAccount}
            />
          </div>
        </div>

        {problem ? (
          <p id="idea-problem" role="alert" className="mt-2.5 flex items-start gap-2 text-sm text-warning animate-in fade-in duration-200">
            <Lightbulb className="mt-0.5 size-4 shrink-0" />
            <span>{problem}</span>
          </p>
        ) : null}

        <div className="mt-4 flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <ShieldCheck className="size-3.5 text-positive" />
            Nothing is bought until you approve
          </p>
          <Button size="lg" onClick={() => void submit()} disabled={submitting || idea.trim().length === 0} className="h-10 px-4 sm:w-auto">
            {submitting ? <Loader2 className="size-4 animate-spin" /> : null}
            Draft the play
            <kbd className="ml-1 hidden rounded border border-primary-foreground/25 px-1 font-sans text-[10px] leading-4 opacity-80 sm:inline">
              {isMac ? '⌘' : 'Ctrl'}↵
            </kbd>
          </Button>
        </div>

        <div className="mt-7">
          <p className="text-xs text-muted-foreground">Or try one of these</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            {EXAMPLE_IDEAS.map(({ title, idea: text, Icon }) => (
              <button
                key={title}
                type="button"
                onClick={() => {
                  setTemplate(null)
                  fill(text)
                }}
                className={cn(
                  'group inline-flex items-center gap-2 rounded-full border bg-card py-1.5 pr-3.5 pl-2 text-sm transition-colors',
                  idea === text ? 'border-foreground/30 text-foreground' : 'border-border text-muted-foreground hover:border-foreground/25 hover:text-foreground',
                )}
              >
                <span className="flex size-6 items-center justify-center rounded-full bg-muted text-foreground/75 transition-colors group-hover:text-foreground">
                  <Icon className="size-3.5" />
                </span>
                {title}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="mt-16 animate-in fade-in duration-700 fill-mode-both [animation-delay:160ms]" aria-labelledby="templates-heading">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h2 id="templates-heading" className="text-lg font-semibold tracking-tight">
              Start from a template
            </h2>
            <p className="mt-0.5 text-sm text-muted-foreground">A head start. Archie still sizes it to your book.</p>
          </div>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {PLAY_TEMPLATES.map((t) => (
            <TemplateCard key={t.id} template={t} selected={template?.id === t.id} onUse={() => applyTemplate(t)} />
          ))}
        </div>
      </section>

      <StarterCryptoDialog open={cryptoOpen} onOpenChange={setCryptoOpen} />
    </div>
  )
}

/** Cycles example theses while the box is empty. Purely visual: the real placeholder is blank. */
function RotatingPlaceholder() {
  const [i, setI] = useState(0)
  useEffect(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const t = window.setInterval(() => setI((n) => (n + 1) % PLACEHOLDER_IDEAS.length), 3800)
    return () => window.clearInterval(t)
  }, [])
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute inset-x-5 top-4 text-lg leading-relaxed tracking-[-0.01em] text-muted-foreground/70 sm:text-xl sm:leading-relaxed"
    >
      <span key={i} className="block animate-in fade-in slide-in-from-bottom-1 duration-700">
        {PLACEHOLDER_IDEAS[i]}
      </span>
    </span>
  )
}

function PillTrigger({ Icon, label, set }: { Icon: LucideIcon; label: string; set: boolean }) {
  return (
    <>
      <Icon className={cn('size-3.5', set ? 'text-foreground' : 'text-muted-foreground')} />
      {label}
      <ChevronDown className="size-3 opacity-50 transition-transform group-data-[popup-open]:rotate-180" />
    </>
  )
}

const pillClass = (set: boolean) =>
  cn(
    'group inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50 data-[popup-open]:border-foreground/30 data-[popup-open]:text-foreground',
    set ? 'border-foreground/15 bg-muted text-foreground' : 'border-dashed border-border text-muted-foreground hover:border-foreground/25 hover:text-foreground',
  )

function ChoicePill<T extends string>({
  Icon,
  label,
  set = true,
  title,
  value,
  options,
  onChange,
  auto,
}: {
  Icon: LucideIcon
  label: string
  set?: boolean
  title: string
  value: T | null
  options: { value: T; label: string; hint?: string }[]
  onChange: (v: T) => void
  auto?: { label: string; onSelect: () => void }
}) {
  const [open, setOpen] = useState(false)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className={pillClass(set)}>
        <PillTrigger Icon={Icon} label={label} set={set} />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-60 gap-0 p-1.5">
        <p className="px-2 pt-1 pb-1.5 text-[11px] text-muted-foreground">{title}</p>
        <div role="radiogroup" aria-label={title}>
          {auto ? (
            <ChoiceRow
              selected={value == null}
              label={auto.label}
              onSelect={() => {
                auto.onSelect()
                setOpen(false)
              }}
            />
          ) : null}
          {options.map((o) => (
            <ChoiceRow
              key={o.value}
              selected={value === o.value}
              label={o.label}
              hint={o.hint}
              onSelect={() => {
                onChange(o.value)
                setOpen(false)
              }}
            />
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}

function ChoiceRow({ selected, label, hint, onSelect }: { selected: boolean; label: string; hint?: string; onSelect: () => void }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={cn(
        'flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted',
        selected && 'bg-muted/60',
      )}
    >
      <span className="min-w-0 flex-1">
        <span className="block text-sm">{label}</span>
        {hint ? <span className="block text-xs text-muted-foreground">{hint}</span> : null}
      </span>
      <Check className={cn('size-3.5 shrink-0', selected ? 'text-foreground' : 'invisible')} />
    </button>
  )
}

function BudgetPill({ budget, setBudget }: { budget: string; setBudget: (v: string) => void }) {
  const [open, setOpen] = useState(false)
  const amount = Number(budget)
  const set = amount > 0
  const label = set ? `£${amount.toLocaleString('en-GB', { maximumFractionDigits: 2 })}` : 'Auto budget'
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className={pillClass(set)}>
        <PillTrigger Icon={Wallet} label={label} set={set} />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 gap-2 p-3">
        <p className="text-[11px] text-muted-foreground">How much, in total?</p>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            setOpen(false)
          }}
          className="relative"
        >
          <span className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-sm text-muted-foreground">£</span>
          <Input
            autoFocus
            inputMode="decimal"
            aria-label="Budget in pounds"
            value={budget}
            onChange={(e) => setBudget(e.target.value.replace(/[^0-9.]/g, '').replace(/(\..*)\./g, '$1').slice(0, 10))}
            placeholder="Auto"
            className="h-9 pl-7 font-mono tabular-nums"
          />
        </form>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {set ? 'Archie will tell you if it’s too big for your book.' : 'Leave it blank and Archie picks a size that fits your book.'}
        </p>
        {set ? (
          <button
            type="button"
            onClick={() => {
              setBudget('')
              setOpen(false)
            }}
            className="self-start text-xs text-foreground hover:underline"
          >
            Let Archie size it
          </button>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}

function RiskBars({ risk }: { risk: PlayTemplate['risk'] }) {
  const n = { lower: 1, medium: 2, higher: 3 }[risk]
  return (
    <span className="inline-flex items-center gap-1.5" title={RISK_LABEL[risk]}>
      <span className="inline-flex items-end gap-0.5" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className={cn('w-1 rounded-full', i < n ? (n === 3 ? 'bg-warning' : 'bg-foreground/60') : 'bg-border')}
            style={{ height: 5 + i * 2.5 }}
          />
        ))}
      </span>
      {RISK_LABEL[risk]}
    </span>
  )
}

function TemplateCard({ template: t, selected, onUse }: { template: PlayTemplate; selected: boolean; onUse: () => void }) {
  if (t.kind === 'starter_crypto') return <WideTemplateCard template={t} onUse={onUse} />
  return (
    <button
      type="button"
      onClick={onUse}
      aria-pressed={selected}
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-xl border bg-card p-4 text-left transition-colors duration-150 hover:bg-muted/40',
        selected ? 'border-foreground/35' : 'border-border hover:border-foreground/25',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="flex size-9 items-center justify-center rounded-lg bg-muted text-foreground/80">
          <t.Icon className="size-[18px]" />
        </span>
        {selected ? (
          <span className="inline-flex items-center gap-1 text-[11px] font-medium text-foreground">
            <Check className="size-3" /> In use
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-[11px] font-medium text-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 max-sm:text-muted-foreground max-sm:opacity-100">
            Use template <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
          </span>
        )}
      </div>
      <p className="mt-3.5 text-sm font-semibold tracking-tight">{t.name}</p>
      <p className="mt-1 text-[13px] leading-snug text-muted-foreground">{t.pitch}</p>
      <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-4 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5">
          <RiskBars risk={t.risk} />
        </span>
        <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5">
          <Clock3 className="size-3" />
          {t.horizonLabel ?? horizonLabel(t.horizonMonths)}
        </span>
      </div>
    </button>
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
          ? 'OpenPF restarted mid-draft'
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
    <div className="relative isolate mx-auto max-w-3xl space-y-8 pb-10">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-24 -z-10 h-[30rem]"
        style={{
          background: failed && kind !== 'interrupted'
            ? 'radial-gradient(closest-side, color-mix(in oklab, var(--warning) 10%, transparent), transparent)'
            : 'radial-gradient(closest-side, color-mix(in oklab, var(--primary) 18%, transparent), transparent)',
        }}
      />
      <button
        type="button"
        onClick={() => navigate('/plays')}
        className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Plays
      </button>
      <div className="space-y-2.5">
        <Eyebrow>{failed ? (kind === 'interrupted' ? 'Nothing went wrong with your idea' : 'Drafting stopped') : 'Drafting your play'}</Eyebrow>
        <h1 className="text-3xl font-semibold tracking-tight">{failed ? heading : 'Archie is on it'}</h1>
        <p className="text-base text-muted-foreground">
          {failed
            ? missing
              ? 'It may have been cleared. Start again; a draft takes about a minute.'
              : kind === 'interrupted'
                ? 'The draft stopped because OpenPF restarted. Retry picks up from the start; it takes about a minute.'
                : job?.error || 'Something went wrong. Try again.'
            : 'Finding the names, checking prices and fundamentals, then sizing it to your book. Usually about a minute.'}
        </p>
      </div>
      {idea ? (
        <blockquote className="rounded-2xl border border-border bg-card px-5 py-4 text-base leading-relaxed text-foreground/90 shadow-sm">
          “{idea}”
        </blockquote>
      ) : null}
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
      ) : (
        <p className="flex items-center gap-1.5 border-t border-border pt-5 text-xs text-muted-foreground">
          <ShieldCheck className="size-3.5 text-positive" />
          You'll review the draft first. Nothing is bought until you approve.
        </p>
      )}
    </div>
  )
}

/** The crypto starter runs its own weekly plan on the exchange, so it gets its own row. */
function WideTemplateCard({ template: t, onUse }: { template: PlayTemplate; onUse: () => void }) {
  return (
    <button
      type="button"
      onClick={onUse}
      className="group relative flex flex-col gap-4 overflow-hidden rounded-xl border border-border bg-card p-4 text-left transition-colors duration-150 hover:border-foreground/25 hover:bg-muted/40 sm:col-span-2 sm:flex-row sm:items-center lg:col-span-3"
    >
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground/80">
        <t.Icon className="size-[18px]" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold tracking-tight">{t.name}</span>
        <span className="mt-0.5 block text-[13px] leading-snug text-muted-foreground">
          {t.pitch} Runs on your crypto exchange.
        </span>
      </span>
      <span className="flex shrink-0 flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5">
          <RiskBars risk={t.risk} />
        </span>
        <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5">
          <Clock3 className="size-3" />
          {t.horizonLabel ?? horizonLabel(t.horizonMonths)}
        </span>
        <span className="ml-2 inline-flex items-center gap-1 font-medium text-foreground">
          Set it up <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
        </span>
      </span>
    </button>
  )
}
