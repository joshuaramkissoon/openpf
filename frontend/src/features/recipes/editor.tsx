import { useMemo, useState } from 'react'
import { LoaderCircle } from 'lucide-react'

import { Segmented } from '@/components/kit'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import type { Recipe, RecipeDraftFields, RecipeTrigger } from '@/types/recipes'

const DAYS = [
  { value: '1', label: 'Mon' },
  { value: '2', label: 'Tue' },
  { value: '3', label: 'Wed' },
  { value: '4', label: 'Thu' },
  { value: '5', label: 'Fri' },
  { value: '6', label: 'Sat' },
  { value: '0', label: 'Sun' },
]

type Cadence = 'weekdays' | 'daily' | 'weekly' | 'monthly' | 'custom'

function parseCron(cron: string): { cadence: Cadence; time: string; day: string; dom: string } {
  const [m, h, dom, mon, dow] = cron.trim().split(/\s+/)
  const time = /^\d+$/.test(m ?? '') && /^\d+$/.test(h ?? '') ? `${h.padStart(2, '0')}:${m.padStart(2, '0')}` : '07:00'
  const base = { time, day: '1', dom: '1' }
  if (mon !== '*' || !/^\d+$/.test(h ?? '')) return { ...base, cadence: 'custom' }
  if (dom === '*' && dow === '1-5') return { ...base, cadence: 'weekdays' }
  if (dom === '*' && dow === '*') return { ...base, cadence: 'daily' }
  if (dom === '*' && /^[0-6]$/.test(dow ?? '')) return { ...base, cadence: 'weekly', day: dow }
  if (/^\d+$/.test(dom ?? '') && dow === '*') return { ...base, cadence: 'monthly', dom }
  return { ...base, cadence: 'custom' }
}

function buildCron(cadence: Cadence, time: string, day: string, dom: string, custom: string): string {
  const [hh, mm] = (time || '07:00').split(':').map((x) => String(parseInt(x, 10) || 0))
  switch (cadence) {
    case 'weekdays':
      return `${mm} ${hh} * * 1-5`
    case 'daily':
      return `${mm} ${hh} * * *`
    case 'weekly':
      return `${mm} ${hh} * * ${day}`
    case 'monthly':
      return `${mm} ${hh} ${dom} * *`
    default:
      return custom.trim()
  }
}

const inputCls =
  'h-8 rounded-lg border border-border bg-background px-2.5 text-sm outline-none transition-colors focus:border-primary/50 focus:ring-3 focus:ring-primary/15'

/** Manual / schedule / event — plain controls that produce a backend trigger. */
export function TriggerEditor({ value, onChange }: { value: RecipeTrigger; onChange: (t: RecipeTrigger) => void }) {
  const initial = value.kind === 'schedule' ? parseCron(value.cron) : { cadence: 'weekdays' as Cadence, time: '07:00', day: '1', dom: '1' }
  const [cadence, setCadence] = useState<Cadence>(initial.cadence)
  const [time, setTime] = useState(initial.time)
  const [day, setDay] = useState(initial.day)
  const [dom, setDom] = useState(initial.dom)
  const [custom, setCustom] = useState(value.kind === 'schedule' ? value.cron : '0 7 * * 1-5')
  const tz = ('timezone' in value && value.timezone) || 'Europe/London'

  const emit = (patch: Partial<{ cadence: Cadence; time: string; day: string; dom: string; custom: string }>) => {
    const next = { cadence, time, day, dom, custom, ...patch }
    onChange({ kind: 'schedule', cron: buildCron(next.cadence, next.time, next.day, next.dom, next.custom), timezone: tz })
  }

  return (
    <div className="space-y-3">
      <Segmented
        ariaLabel="When it runs"
        value={value.kind}
        options={[
          { value: 'schedule', label: 'On a schedule' },
          { value: 'event', label: 'When something happens' },
          { value: 'manual', label: 'Only when I run it' },
        ]}
        onChange={(k) => {
          if (k === 'manual') onChange({ kind: 'manual' })
          else if (k === 'event') onChange({ kind: 'event', event: 'earnings_week', timezone: tz })
          else emit({})
        }}
      />

      {value.kind === 'schedule' ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <select
            value={cadence}
            onChange={(e) => {
              const c = e.target.value as Cadence
              setCadence(c)
              emit({ cadence: c })
            }}
            className={inputCls}
            aria-label="Cadence"
          >
            <option value="weekdays">Weekdays</option>
            <option value="daily">Every day</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
            <option value="custom">Custom (cron)</option>
          </select>
          {cadence === 'weekly' ? (
            <select value={day} onChange={(e) => (setDay(e.target.value), emit({ day: e.target.value }))} className={inputCls} aria-label="Day">
              {DAYS.map((d) => (
                <option key={d.value} value={d.value}>
                  on {d.label}
                </option>
              ))}
            </select>
          ) : null}
          {cadence === 'monthly' ? (
            <select value={dom} onChange={(e) => (setDom(e.target.value), emit({ dom: e.target.value }))} className={inputCls} aria-label="Day of month">
              {Array.from({ length: 28 }, (_, i) => String(i + 1)).map((d) => (
                <option key={d} value={d}>
                  on day {d}
                </option>
              ))}
            </select>
          ) : null}
          {cadence !== 'custom' ? (
            <>
              <span className="text-muted-foreground">at</span>
              <input type="time" value={time} onChange={(e) => (setTime(e.target.value), emit({ time: e.target.value }))} className={cn(inputCls, 'w-[7.5rem] font-mono')} aria-label="Time" />
            </>
          ) : (
            <input
              value={custom}
              onChange={(e) => (setCustom(e.target.value), emit({ custom: e.target.value }))}
              placeholder="0 7 * * 1-5"
              className={cn(inputCls, 'w-40 font-mono')}
              aria-label="Cron expression"
            />
          )}
          <span className="text-xs text-muted-foreground">{tz.replace('_', ' ')}</span>
        </div>
      ) : null}

      {value.kind === 'event' ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <select
            value={value.event}
            onChange={(e) => onChange({ kind: 'event', event: e.target.value as 'earnings_week' | 'big_move', threshold_pct: e.target.value === 'big_move' ? 5 : undefined, timezone: tz })}
            className={inputCls}
            aria-label="Event"
          >
            <option value="earnings_week">A holding reports earnings this week</option>
            <option value="big_move">A holding moves sharply in a day</option>
          </select>
          {value.event === 'big_move' ? (
            <>
              <span className="text-muted-foreground">by ±</span>
              <input
                type="number"
                min={2}
                max={30}
                step={0.5}
                value={value.threshold_pct ?? 5}
                onChange={(e) => onChange({ ...value, threshold_pct: Number(e.target.value) })}
                className={cn(inputCls, 'w-20 font-mono')}
                aria-label="Threshold percent"
              />
              <span className="text-muted-foreground">%</span>
            </>
          ) : null}
          <p className="basis-full text-xs text-muted-foreground">
            {value.event === 'big_move'
              ? 'Checked hourly through the trading day. Archie only runs when a holding crosses the line — once per name per day.'
              : 'Checked on Monday mornings. Archie only runs when one of your top holdings reports within 7 days.'}
          </p>
        </div>
      ) : null}

      {value.kind === 'manual' ? <p className="text-xs text-muted-foreground">Runs only when you press Run now (or ask Archie in chat).</p> : null}
    </div>
  )
}

/** Hand edit of everything a recipe is made of. */
export function RecipeEditor({
  recipe,
  saving,
  onSave,
  onCancel,
}: {
  recipe: Recipe
  saving: boolean
  onSave: (fields: Partial<RecipeDraftFields>) => void
  onCancel: () => void
}) {
  const [title, setTitle] = useState(recipe.title)
  const [summary, setSummary] = useState(recipe.summary)
  const [body, setBody] = useState(recipe.body)
  const [trigger, setTrigger] = useState<RecipeTrigger>(recipe.trigger)
  const [telegram, setTelegram] = useState(recipe.delivery.telegram)
  const dirty = useMemo(
    () =>
      title !== recipe.title ||
      summary !== recipe.summary ||
      body !== recipe.body ||
      telegram !== recipe.delivery.telegram ||
      JSON.stringify(trigger) !== JSON.stringify(recipe.trigger),
    [title, summary, body, trigger, telegram, recipe],
  )

  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault()
        onSave({ title, summary, body, trigger, delivery: { telegram } })
      }}
    >
      <label className="block space-y-1.5">
        <span className="text-xs font-medium text-muted-foreground">Title</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80} className={cn(inputCls, 'w-full')} />
      </label>
      <label className="block space-y-1.5">
        <span className="text-xs font-medium text-muted-foreground">What it does (one sentence)</span>
        <input value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={300} className={cn(inputCls, 'w-full')} />
      </label>
      <div className="space-y-1.5">
        <span className="text-xs font-medium text-muted-foreground">When it runs</span>
        <TriggerEditor value={trigger} onChange={setTrigger} />
      </div>
      <label className="block space-y-1.5">
        <span className="flex items-baseline justify-between text-xs font-medium text-muted-foreground">
          Instructions for Archie
          <span className="font-mono text-[11px] tabular-nums">{body.length}/8000</span>
        </span>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={16}
          maxLength={8000}
          className="block w-full resize-y rounded-lg border border-border bg-background px-3 py-2.5 font-mono text-[12.5px] leading-relaxed outline-none focus:border-primary/50 focus:ring-3 focus:ring-primary/15"
        />
      </label>
      <label className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2.5">
        <span>
          <span className="block text-sm">Also send to Telegram</span>
          <span className="block text-xs text-muted-foreground">Results always land on Home and in Activity.</span>
        </span>
        <Switch checked={telegram} onCheckedChange={setTelegram} />
      </label>
      <div className="flex items-center justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={!dirty || saving}>
          {saving ? <LoaderCircle className="animate-spin" /> : null} Save as new version
        </Button>
      </div>
    </form>
  )
}
