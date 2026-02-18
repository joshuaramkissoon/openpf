import dayjs from 'dayjs'

import type { ExecutionEvent } from '../types'

interface Props {
  events: ExecutionEvent[]
}

export function EventsFeed({ events }: Props) {
  return (
    <section className="glass-card events-card">
      <div className="section-heading-row">
        <h2>Execution Audit Trail</h2>
        <span className="hint">Every decision and outcome is logged</span>
      </div>
      <div className="events-list">
        {events.length === 0 && <p className="muted">No events yet.</p>}
        {events.slice(0, 40).map((event, idx) => (
          <div key={`${event.intent_id}-${event.created_at}-${idx}`} className={`event ${event.level}`}>
            <span className="event-time">{dayjs(event.created_at).format('MMM D HH:mm:ss')}</span>
            <span className="event-level">{event.level.toUpperCase()}</span>
            <span className="event-msg">{event.message}</span>
            <span className="event-intent">{event.intent_id.slice(0, 8)}</span>
          </div>
        ))}
      </div>
    </section>
  )
}
