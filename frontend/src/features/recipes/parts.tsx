import { useState } from 'react'
import dayjs from 'dayjs'
import calendar from 'dayjs/plugin/calendar'
import {
  Activity,
  Bell,
  Binoculars,
  CalendarCheck,
  CalendarClock,
  ChartLine,
  Clock,
  Coins,
  Compass,
  Eye,
  Flame,
  Globe,
  Landmark,
  type LucideIcon,
  Newspaper,
  PiggyBank,
  Radar,
  Scale,
  Shield,
  Sparkles,
  Sunrise,
  Target,
  TrendingDown,
  TrendingUp,
  Wallet,
  Zap,
} from 'lucide-react'

import { cn } from '@/lib/utils'
import type { DiffLine, Recipe, RecipeRun } from '@/types/recipes'

dayjs.extend(calendar)

const ICONS: Record<string, LucideIcon> = {
  activity: Activity,
  bell: Bell,
  binoculars: Binoculars,
  'calendar-check': CalendarCheck,
  'calendar-clock': CalendarClock,
  'chart-line': ChartLine,
  clock: Clock,
  coins: Coins,
  compass: Compass,
  eye: Eye,
  flame: Flame,
  globe: Globe,
  landmark: Landmark,
  newspaper: Newspaper,
  'piggy-bank': PiggyBank,
  radar: Radar,
  scale: Scale,
  shield: Shield,
  sparkles: Sparkles,
  sunrise: Sunrise,
  target: Target,
  'trending-down': TrendingDown,
  'trending-up': TrendingUp,
  wallet: Wallet,
  zap: Zap,
}

export function recipeIcon(name: string | undefined): LucideIcon {
  return (name && ICONS[name]) || Sparkles
}

export function RecipeIcon({ icon, size = 'md', muted = false }: { icon: string; size?: 'sm' | 'md' | 'lg'; muted?: boolean }) {
  const Icon = recipeIcon(icon)
  return (
    <span
      className={cn(
        'flex shrink-0 items-center justify-center rounded-lg ring-1 transition-colors',
        muted ? 'bg-muted text-muted-foreground ring-border' : 'bg-primary/12 text-primary ring-primary/20',
        size === 'lg' ? 'size-11 rounded-xl' : size === 'md' ? 'size-9' : 'size-8',
      )}
    >
      <Icon className={size === 'lg' ? 'size-5' : 'size-4'} strokeWidth={2} />
    </span>
  )
}

/** "Weekdays at 07:00" → "Every weekday at 07:00". */
export function whenSentence(r: Pick<Recipe, 'trigger' | 'trigger_summary'>): string {
  if (r.trigger.kind === 'manual') return 'Only when you run it'
  const s = r.trigger_summary || ''
  const m = s.match(/^(Weekdays|Daily|Every day|Mondays|Tuesdays|Wednesdays|Thursdays|Fridays|Saturdays|Sundays)\b(.*)$/)
  if (!m) return s
  const day = m[1] === 'Weekdays' ? 'weekday' : m[1] === 'Daily' || m[1] === 'Every day' ? 'day' : m[1].slice(0, -1)
  return `Every ${day}${m[2]}`
}

export function deliverySentence(r: Pick<Recipe, 'delivery'>): string {
  return r.delivery.telegram ? 'results on Home and Telegram' : 'results on Home'
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).getTime()
  const s = (Date.now() - t) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  const d = Math.floor(s / 86400)
  return d === 1 ? 'yesterday' : d < 30 ? `${d} days ago` : dayjs(t).format('D MMM')
}

/** Backend datetimes are naive UTC. */
export function utc(iso: string): dayjs.Dayjs {
  return dayjs(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`)
}

export function nextRunLabel(iso: string | null | undefined): string {
  if (!iso) return ''
  return utc(iso).calendar(null, {
    sameDay: '[today] HH:mm',
    nextDay: '[tomorrow] HH:mm',
    nextWeek: 'ddd HH:mm',
    sameElse: 'D MMM HH:mm',
  })
}

export function StatusDot({ status, className }: { status: string | undefined; className?: string }) {
  return (
    <span
      className={cn(
        'inline-block size-1.5 shrink-0 rounded-full',
        status === 'ok' ? 'bg-positive' : status === 'error' ? 'bg-negative' : status === 'running' ? 'animate-pulse bg-primary' : 'bg-muted-foreground/40',
        className,
      )}
    />
  )
}

export function LastRunLine({ run, running, className }: { run: RecipeRun | null; running?: boolean; className?: string }) {
  if (running) {
    return (
      <p className={cn('flex items-center gap-1.5 text-xs text-primary', className)}>
        <StatusDot status="running" /> Running now…
      </p>
    )
  }
  if (!run) return <p className={cn('text-xs text-muted-foreground/80', className)}>Hasn’t run yet</p>
  return (
    <p className={cn('flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground', className)} title={run.summary || run.message}>
      <StatusDot status={run.status} />
      <span className="shrink-0">{ago(run.at)}</span>
      {run.summary || run.status === 'error' ? <span className="text-muted-foreground/50">·</span> : null}
      <span className={cn('truncate', run.status === 'error' && 'text-negative')}>
        {run.status === 'error' ? run.message || 'Failed' : run.summary}
      </span>
    </p>
  )
}

export function Chip({ children, className, title }: { children: React.ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cn('inline-flex items-center gap-1 rounded-full border border-border bg-card/60 px-2 py-0.5 text-[11px] text-muted-foreground', className)}>
      {children}
    </span>
  )
}

/** A unified diff, rendered calmly: green adds, red removes, context folded. */
export function DiffView({ lines, className, maxHeight = 360 }: { lines: DiffLine[]; className?: string; maxHeight?: number }) {
  const [expanded, setExpanded] = useState(false)
  if (!lines.length) return <p className="text-xs text-muted-foreground">No changes.</p>
  const adds = lines.filter((l) => l.type === 'add').length
  const dels = lines.filter((l) => l.type === 'del').length
  return (
    <div className={cn('overflow-hidden rounded-lg border border-border bg-background/60', className)}>
      <div className="flex items-center gap-3 border-b border-border/70 px-3 py-1.5 font-mono text-[11px] text-muted-foreground">
        <span className="text-positive">+{adds}</span>
        <span className="text-negative">−{dels}</span>
      </div>
      <div className="overflow-auto font-mono text-[12px] leading-relaxed" style={{ maxHeight: expanded ? undefined : maxHeight }}>
        {lines.map((l, i) =>
          l.type === 'hunk' ? (
            <div key={i} className="select-none border-y border-border/40 bg-muted/40 px-3 py-0.5 text-[10px] text-muted-foreground/70">
              ⋯
            </div>
          ) : (
            <div
              key={i}
              className={cn(
                'flex gap-2 whitespace-pre-wrap break-words px-3',
                l.type === 'add' && 'bg-positive/10 text-foreground',
                l.type === 'del' && 'bg-negative/10 text-muted-foreground line-through decoration-negative/40',
                l.type === 'ctx' && 'text-muted-foreground',
              )}
            >
              <span className={cn('w-3 shrink-0 select-none', l.type === 'add' ? 'text-positive' : l.type === 'del' ? 'text-negative' : 'text-transparent')}>
                {l.type === 'add' ? '+' : l.type === 'del' ? '−' : ' '}
              </span>
              <span className="min-w-0 flex-1">{l.text || ' '}</span>
            </div>
          ),
        )}
      </div>
      {lines.length > 18 ? (
        <button type="button" onClick={() => setExpanded((v) => !v)} className="w-full border-t border-border/70 py-1 text-[11px] text-muted-foreground hover:text-foreground">
          {expanded ? 'Collapse' : 'Show all'}
        </button>
      ) : null}
    </div>
  )
}

export const AUTHOR_LABEL: Record<string, string> = {
  builtin: 'OpenPF',
  user: 'You',
  archie: 'Archie',
}
