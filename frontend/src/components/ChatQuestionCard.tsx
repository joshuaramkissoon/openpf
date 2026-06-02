import { useMemo, useState } from 'react'

import { CheckCircle2, Send, Sparkles } from 'lucide-react'

import type { ChatQuestionAnswers, ChatQuestionSpec } from '@/types'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface QState {
  selected: string[]
  otherActive: boolean
  otherText: string
}

function initialState(questions: ChatQuestionSpec[]): QState[] {
  return questions.map(() => ({ selected: [], otherActive: false, otherText: '' }))
}

interface Props {
  questionId: string
  questions: ChatQuestionSpec[]
  onSubmit: (questionId: string, answers: ChatQuestionAnswers) => void
  onCancel: (questionId: string) => void
}

/** Interactive card for Archie's AskUserQuestion clarifying prompts. Renders each
 * question's options as selectable rows (single- or multi-select) plus an "Other"
 * free-text escape hatch, and sends the chosen answers back over the chat socket. */
export function ChatQuestionCard({ questionId, questions, onSubmit, onCancel }: Props) {
  const [states, setStates] = useState<QState[]>(() => initialState(questions))
  const [submitted, setSubmitted] = useState(false)

  const update = (qi: number, fn: (s: QState) => QState) =>
    setStates((prev) => prev.map((s, i) => (i === qi ? fn(s) : s)))

  const chooseOption = (qi: number, label: string, multi: boolean) =>
    update(qi, (s) => {
      if (multi) {
        const has = s.selected.includes(label)
        return { ...s, selected: has ? s.selected.filter((l) => l !== label) : [...s.selected, label] }
      }
      return { ...s, selected: [label], otherActive: false }
    })

  const toggleOther = (qi: number, multi: boolean) =>
    update(qi, (s) =>
      multi ? { ...s, otherActive: !s.otherActive } : { ...s, otherActive: true, selected: [] }
    )

  const isComplete = (s: QState) => s.selected.length > 0 || (s.otherActive && s.otherText.trim().length > 0)
  const allComplete = useMemo(() => states.every(isComplete), [states])

  const submit = () => {
    if (!allComplete || submitted) return
    const answers: ChatQuestionAnswers = {}
    questions.forEach((q, qi) => {
      const s = states[qi]
      const labels = [...s.selected]
      if (s.otherActive && s.otherText.trim()) labels.push(s.otherText.trim())
      answers[q.question] = q.multiSelect ? labels : labels[0] ?? ''
    })
    setSubmitted(true)
    onSubmit(questionId, answers)
  }

  const skip = () => {
    if (submitted) return
    setSubmitted(true)
    onCancel(questionId)
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card/60">
      <div className="flex items-center gap-1.5 border-b border-border/60 bg-muted/30 px-3 py-1.5 text-xs font-medium text-muted-foreground">
        <Sparkles className="size-3.5 text-primary" />
        <span>Archie needs a quick answer</span>
      </div>

      <div className="space-y-4 px-3 py-3">
        {questions.map((q, qi) => {
          const s = states[qi]
          const multi = !!q.multiSelect
          return (
            <div key={qi} className="space-y-2">
              <div className="space-y-0.5">
                {q.header && (
                  <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground/80">
                    {q.header}
                  </div>
                )}
                <div className="text-sm font-medium text-foreground">{q.question}</div>
                {multi && <div className="text-xs text-muted-foreground">Select all that apply</div>}
              </div>

              <div className="space-y-1.5">
                {q.options.map((opt) => {
                  const active = s.selected.includes(opt.label)
                  return (
                    <OptionRow
                      key={opt.label}
                      active={active}
                      multi={multi}
                      disabled={submitted}
                      label={opt.label}
                      description={opt.description}
                      onClick={() => chooseOption(qi, opt.label, multi)}
                    />
                  )
                })}

                <OptionRow
                  active={s.otherActive}
                  multi={multi}
                  disabled={submitted}
                  label="Something else…"
                  onClick={() => toggleOther(qi, multi)}
                />
                {s.otherActive && (
                  <Input
                    autoFocus
                    disabled={submitted}
                    value={s.otherText}
                    onChange={(e) => update(qi, (st) => ({ ...st, otherText: e.target.value }))}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !multi) {
                        e.preventDefault()
                        submit()
                      }
                    }}
                    placeholder="Type your answer…"
                    className="mt-1 h-8 text-sm"
                  />
                )}
              </div>
            </div>
          )
        })}

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" size="sm" onClick={skip} disabled={submitted}>
            Skip
          </Button>
          <Button type="button" size="sm" onClick={submit} disabled={!allComplete || submitted}>
            <Send className="size-3.5" />
            Send
          </Button>
        </div>
      </div>
    </div>
  )
}

function OptionRow({
  active,
  multi,
  disabled,
  label,
  description,
  onClick,
}: {
  active: boolean
  multi: boolean
  disabled: boolean
  label: string
  description?: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'flex w-full items-start gap-2.5 rounded-md border px-3 py-2 text-left transition-colors disabled:opacity-60',
        active ? 'border-primary/60 bg-primary/5' : 'border-border hover:bg-muted/50'
      )}
    >
      <span className="mt-0.5 shrink-0">
        {active ? (
          <CheckCircle2 className="size-4 text-primary" />
        ) : (
          <span className={cn('block size-4 border border-muted-foreground/40', multi ? 'rounded-[4px]' : 'rounded-full')} />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium text-foreground">{label}</span>
        {description && <span className="mt-0.5 block text-xs text-muted-foreground">{description}</span>}
      </span>
    </button>
  )
}
