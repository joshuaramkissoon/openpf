import dayjs from 'dayjs'

import type { Thesis } from '../types'

interface Props {
  theses: Thesis[]
  onArchive: (id: string) => void
  onActivate: (id: string) => void
}

export function ThesisBoard({ theses, onArchive, onActivate }: Props) {
  return (
    <section className="glass-card intents-card">
      <div className="section-heading-row">
        <h2>Thesis Board</h2>
        <span className="hint">Persistent AI theses and invalidation logic</span>
      </div>
      <div className="intents-list">
        {theses.length === 0 && <p className="muted">No theses yet. Run agent with Claude configured.</p>}
        {theses.slice(0, 20).map((thesis) => (
          <article key={thesis.id} className="intent-item">
            <div className="intent-head">
              <div>
                <span className="pill ok">{thesis.account_kind}</span>
                <strong>{thesis.symbol}</strong>
                <span className="muted">{dayjs(thesis.created_at).format('MMM D HH:mm')}</span>
              </div>
              <span className="status approved">{Math.round((thesis.confidence || 0) * 100)}%</span>
            </div>
            <p><strong>{thesis.title}</strong></p>
            <p>{thesis.thesis}</p>
            {thesis.catalysts?.length > 0 && (
              <p className="muted">Catalysts: {thesis.catalysts.join(', ')}</p>
            )}
            {thesis.invalidation && <p className="muted">Invalidation: {thesis.invalidation}</p>}
            <div className="intent-actions">
              {thesis.status !== 'archived' ? (
                <button className="btn ghost" onClick={() => onArchive(thesis.id)}>
                  Archive
                </button>
              ) : (
                <button className="btn ghost" onClick={() => onActivate(thesis.id)}>
                  Reactivate
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}
