import dayjs from 'dayjs'
import { ScrollText } from 'lucide-react'

import { SectionCard } from '@/components/kit'
import { cn } from '@/lib/utils'
import type { ExecutionEvent } from '@/types'

interface Props {
  events: ExecutionEvent[]
}

function levelDot(level: string): string {
  if (level === 'error' || level === 'critical') return 'bg-negative'
  if (level === 'warning' || level === 'warn') return 'bg-warning'
  if (level === 'success') return 'bg-positive'
  return 'bg-muted-foreground'
}

export function EventsFeed({ events }: Props) {
  return (
    <SectionCard
      title="Execution Audit Trail"
      description="Every decision and outcome is logged"
      noPadding
    >
      {events.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <ScrollText className="size-5 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">No events yet — they appear as the agent acts.</p>
        </div>
      ) : (
        <ul className="divide-y divide-border/60">
          {events.slice(0, 40).map((event, idx) => (
            <li
              key={`${event.intent_id}-${event.created_at}-${idx}`}
              className="flex flex-wrap items-start gap-x-3 gap-y-1 px-5 py-2.5 sm:flex-nowrap"
            >
              <span className="mt-1.5 flex shrink-0 items-center">
                <i className={cn('size-2 rounded-full', levelDot(event.level))} />
              </span>
              <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums sm:w-32">
                {dayjs(event.created_at).format('MMM D HH:mm:ss')}
              </span>
              <span className="shrink-0 font-mono text-[11px] uppercase tracking-wider text-muted-foreground sm:w-16">
                {event.level.toUpperCase()}
              </span>
              <span className="min-w-0 flex-1 basis-full text-sm sm:basis-auto">{event.message}</span>
              <span className="hidden shrink-0 font-mono text-xs text-muted-foreground tabular-nums sm:inline">
                {event.intent_id.slice(0, 8)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  )
}
