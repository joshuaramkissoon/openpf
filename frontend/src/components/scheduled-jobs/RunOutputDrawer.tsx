import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import { ArrowRight, CheckCircle2, XCircle } from 'lucide-react'

import { getArtifact } from '../../api/client'
import type { ArtifactDetail } from '../../types'
import { ReportReader } from '@/components/rich/report-reader'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { resolveArtifactRelativePath } from './artifact'

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
    <ReportReader
      open={run !== null}
      onOpenChange={(open) => !open && onClose()}
      title={run?.name ?? 'Run output'}
      kind={run?.taskKind || 'Run output'}
      titleAdornment={run ? ok ? <CheckCircle2 className="size-4 shrink-0 text-positive" /> : <XCircle className="size-4 shrink-0 text-negative" /> : null}
      date={
        run ? (
          <>
            Ran <span className="tabular-nums">{dayjs(run.ranAt).format('ddd D MMM HH:mm')}</span>
            {' · '}
            <span className={cn(ok ? 'text-positive' : 'text-negative')}>{run.status}</span>
          </>
        ) : null
      }
      markdown={state.detail?.content ?? null}
      loading={state.loading}
      error={state.error ? [state.error, run?.message].filter(Boolean).join(' — ') : null}
      empty={run?.message || 'This run produced no output artifact.'}
      footer={
        run ? (
          <Button
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() => {
              onViewTask(run.taskId)
              onClose()
            }}
          >
            View task
            <ArrowRight className="size-3.5" />
          </Button>
        ) : null
      }
    />
  )
}
