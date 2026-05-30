import dayjs from 'dayjs'
import { Lightbulb } from 'lucide-react'

import { SectionCard } from '@/components/kit'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { accountTag } from '@/utils/format'
import type { Thesis } from '@/types'

interface Props {
  theses: Thesis[]
  onArchive: (id: string) => void
  onActivate: (id: string) => void
}

function statusVariant(status: string): 'default' | 'secondary' | 'outline' {
  if (status === 'active') return 'default'
  if (status === 'archived') return 'outline'
  return 'secondary'
}

export function ThesisBoard({ theses, onArchive, onActivate }: Props) {
  return (
    <SectionCard
      title="Thesis Board"
      description="Persistent AI theses and invalidation logic"
    >
      {theses.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <Lightbulb className="size-5 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">No theses yet — run the agent with Claude configured.</p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {theses.slice(0, 20).map((thesis) => (
            <div
              key={thesis.id}
              className="flex flex-col gap-2 rounded-lg border border-border/60 bg-muted/20 p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">{accountTag(thesis.account_kind)}</Badge>
                  <span className="font-medium">{thesis.symbol}</span>
                  <span className="font-mono text-xs text-muted-foreground tabular-nums">
                    {dayjs(thesis.created_at).format('MMM D HH:mm')}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="font-mono text-xs text-muted-foreground tabular-nums">
                    {Math.round((thesis.confidence || 0) * 100)}%
                  </span>
                  <Badge variant={statusVariant(thesis.status)}>{thesis.status}</Badge>
                </div>
              </div>

              <p className="text-sm font-medium">{thesis.title}</p>
              <p className="text-sm text-muted-foreground">{thesis.thesis}</p>

              {thesis.catalysts?.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">Catalysts:</span> {thesis.catalysts.join(', ')}
                </p>
              )}
              {thesis.invalidation && (
                <p className="text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">Invalidation:</span> {thesis.invalidation}
                </p>
              )}

              <div className="flex items-center justify-end gap-2">
                {thesis.status !== 'archived' ? (
                  <Button variant="ghost" size="sm" onClick={() => onArchive(thesis.id)}>
                    Archive
                  </Button>
                ) : (
                  <Button variant="outline" size="sm" onClick={() => onActivate(thesis.id)}>
                    Activate
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  )
}
