import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import { ArrowRight, CheckCircle2, XCircle } from 'lucide-react'

import { getArtifact } from '../../api/client'
import type { ArtifactDetail } from '../../types'
import { RichMarkdown } from '../RichMarkdown'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { resolveArtifactRelativePath } from './artifact'

const MARKDOWN_PROSE =
  'space-y-2 text-sm leading-relaxed [&_a]:text-primary [&_a]:underline [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs [&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:font-semibold [&_strong]:font-semibold [&_table]:w-full [&_ul]:list-disc [&_ul]:pl-5'

export interface DrawerRun {
  taskId: string
  name: string
  taskKind?: string | null
  status: string
  ranAt: string
  message?: string
  outputPath?: string | null
}

interface Props {
  run: DrawerRun | null
  onClose: () => void
  onViewTask: (taskId: string) => void
}

export function RunOutputDrawer({ run, onClose, onViewTask }: Props) {
  const [state, setState] = useState<{ loading: boolean; detail: ArtifactDetail | null; error: string | null }>({
    loading: false,
    detail: null,
    error: null,
  })

  const outputPath = run?.outputPath
  useEffect(() => {
    if (!outputPath) {
      setState({ loading: false, detail: null, error: null })
      return
    }
    let cancelled = false
    setState({ loading: true, detail: null, error: null })
    getArtifact(resolveArtifactRelativePath(outputPath))
      .then((detail) => {
        if (!cancelled) setState({ loading: false, detail, error: null })
      })
      .catch((err) => {
        if (!cancelled) {
          setState({ loading: false, detail: null, error: err instanceof Error ? err.message : "Couldn't load output" })
        }
      })
    return () => {
      cancelled = true
    }
  }, [outputPath])

  const ok = run?.status === 'ok'

  return (
    <Sheet open={run !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full gap-0 sm:!max-w-2xl">
        <SheetHeader className="border-b border-border/60">
          <SheetTitle className="flex items-center gap-2">
            {run && (
              ok ? <CheckCircle2 className="size-4 text-positive" /> : <XCircle className="size-4 text-negative" />
            )}
            <span className="truncate">{run?.name ?? 'Run output'}</span>
            {run?.taskKind ? <Badge variant="secondary">{run.taskKind}</Badge> : null}
          </SheetTitle>
          <SheetDescription>
            {run ? (
              <>
                Ran{' '}
                <span className="font-mono tabular-nums">{dayjs(run.ranAt).format('ddd D MMM HH:mm')}</span>
                {' · '}
                <span className={cn(ok ? 'text-positive' : 'text-negative')}>{run.status}</span>
              </>
            ) : null}
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {state.loading && (
            <div className="space-y-2">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          )}
          {!state.loading && state.error && (
            <div className="space-y-2 text-sm">
              <p className="text-negative">{state.error}</p>
              {run?.message ? <p className="text-muted-foreground">{run.message}</p> : null}
            </div>
          )}
          {!state.loading && !state.error && state.detail && (
            <div className={MARKDOWN_PROSE}>
              <RichMarkdown markdown={state.detail.content} />
            </div>
          )}
          {!state.loading && !state.error && !state.detail && !outputPath && (
            <p className="text-sm text-muted-foreground">
              {run?.message || 'This run produced no output artifact.'}
            </p>
          )}
        </div>

        <SheetFooter className="border-t border-border/60">
          {run && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                onViewTask(run.taskId)
                onClose()
              }}
            >
              View task
              <ArrowRight className="size-3.5" />
            </Button>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
