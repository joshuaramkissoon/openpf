import { useCallback, useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'
import { FileText, RefreshCw, X } from 'lucide-react'

import { listArtifacts, getArtifact } from '../api/client'
import { RichMarkdown } from './RichMarkdown'
import { SectionCard } from '@/components/kit'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import type { ArtifactItem, ArtifactDetail } from '../types'

interface Props {
  onError: (message: string | null) => void
}

type TypeFilter = string

/** Markdown wrapper matching the dashboard prose style. */
const MARKDOWN_PROSE =
  'space-y-2 text-sm leading-relaxed [&_a]:text-primary [&_a]:underline [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs [&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:font-semibold [&_strong]:font-semibold [&_table]:w-full [&_ul]:list-disc [&_ul]:pl-5'

function relativeTime(iso: string): string {
  const now = dayjs()
  const then = dayjs(iso)
  const diffMin = now.diff(then, 'minute')
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHour = now.diff(then, 'hour')
  if (diffHour < 24) return `${diffHour}h ago`
  const diffDay = now.diff(then, 'day')
  if (diffDay < 30) return `${diffDay}d ago`
  return then.format('MMM D YYYY')
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function pathBreadcrumb(path: string): string {
  const parts = path.split('/')
  if (parts.length <= 1) return path
  return parts.slice(0, -1).join(' / ')
}

/** Pretty-print any artifact type string for display. */
function typeLabel(type: string): string {
  // Known friendly labels
  const KNOWN: Record<string, string> = {
    scheduled: 'Scheduled',
    chat: 'Chat',
    adhoc: 'Ad-hoc',
  }
  if (KNOWN[type]) return KNOWN[type]
  // Fallback: capitalize first letter, replace underscores/hyphens with spaces
  return type
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export function ArtifactsWorkspace({ onError }: Props) {
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [busy, setBusy] = useState(false)
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')

  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [detail, setDetail] = useState<ArtifactDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const loadArtifacts = useCallback(async () => {
    setBusy(true)
    try {
      const rows = await listArtifacts()
      setArtifacts(rows)
      onError(null)
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to load artifacts')
    } finally {
      setBusy(false)
    }
  }, [onError])

  useEffect(() => {
    void loadArtifacts()
  }, [loadArtifacts])

  // Derive unique type values from whatever the API returns
  const uniqueTypes = useMemo(() => {
    const types = Array.from(new Set(artifacts.map((a) => a.type)))
    types.sort()
    return types
  }, [artifacts])

  const filteredArtifacts = useMemo(() => {
    if (typeFilter === 'all') return artifacts
    return artifacts.filter((a) => a.type === typeFilter)
  }, [artifacts, typeFilter])

  async function openArtifact(path: string) {
    if (selectedPath === path) return
    setSelectedPath(path)
    setDetail(null)
    setDetailLoading(true)
    try {
      const d = await getArtifact(path)
      setDetail(d)
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to load artifact')
    } finally {
      setDetailLoading(false)
    }
  }

  function closeViewer() {
    setSelectedPath(null)
    setDetail(null)
  }

  const headerActions = (
    <>
      <Select value={typeFilter} onValueChange={(v) => setTypeFilter(v as TypeFilter)}>
        <SelectTrigger size="sm" className="w-[150px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All Types</SelectItem>
          {uniqueTypes.map((t) => (
            <SelectItem key={t} value={t}>{typeLabel(t)}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button variant="outline" size="sm" onClick={() => void loadArtifacts()} disabled={busy}>
        <RefreshCw className={cn('size-3.5', busy && 'animate-spin')} />
        Refresh
      </Button>
    </>
  )

  return (
    <div className="space-y-6">
      <div className={cn('grid gap-4', selectedPath && 'lg:grid-cols-2')}>
        {/* Artifact list */}
        <SectionCard title="Artifacts" action={<div className="flex items-center gap-2">{headerActions}</div>} noPadding>
          <div className="border-b border-border/60 px-5 py-2.5 text-xs text-muted-foreground">
            <span className="font-mono tabular-nums text-foreground">{filteredArtifacts.length}</span>
            {typeFilter === 'all' ? ' total' : ` ${typeLabel(typeFilter)}`}
          </div>

          {busy && filteredArtifacts.length === 0 ? (
            <div className="divide-y divide-border/60">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="space-y-2 px-5 py-3.5">
                  <div className="flex items-center gap-2">
                    <Skeleton className="h-5 w-20 rounded-full" />
                    <Skeleton className="h-4 w-48" />
                  </div>
                  <Skeleton className="h-3 w-40" />
                </div>
              ))}
            </div>
          ) : filteredArtifacts.length === 0 ? (
            <div className="flex flex-col items-center gap-2 px-6 py-16 text-center">
              <FileText className="size-8 text-muted-foreground" />
              <p className="text-sm font-medium">No artifacts yet</p>
              <p className="max-w-md text-xs text-muted-foreground">
                Archie produces artifacts from scheduled jobs and chat analysis. They'll appear
                here automatically.
              </p>
            </div>
          ) : (
            <div className="divide-y divide-border/60">
              {filteredArtifacts.map((artifact) => (
                <button
                  key={artifact.path}
                  type="button"
                  onClick={() => void openArtifact(artifact.path)}
                  className={cn(
                    'flex w-full flex-col gap-1.5 px-5 py-3.5 text-left transition-colors hover:bg-muted/40',
                    selectedPath === artifact.path && 'bg-muted/50'
                  )}
                >
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary">{typeLabel(artifact.type)}</Badge>
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{artifact.title}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {relativeTime(artifact.created_at)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="min-w-0 flex-1 truncate font-mono">{pathBreadcrumb(artifact.path)}</span>
                    {artifact.task_name && (
                      <Badge variant="outline" className="text-muted-foreground">{artifact.task_name}</Badge>
                    )}
                    <span className="shrink-0 font-mono tabular-nums">{formatBytes(artifact.size_bytes)}</span>
                  </div>
                  {artifact.tags && artifact.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {artifact.tags.map((tag) => (
                        <Badge key={tag} variant="outline" className="text-muted-foreground">{tag}</Badge>
                      ))}
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}
        </SectionCard>

        {/* Artifact viewer panel */}
        {selectedPath && (
          <SectionCard
            title={
              detail ? (
                <span className="flex items-center gap-2">
                  <Badge variant="secondary">
                    {typeLabel((detail.metadata?.type as string) || 'adhoc')}
                  </Badge>
                  <span className="truncate">{(detail.metadata?.title as string) || selectedPath}</span>
                </span>
              ) : (
                <span className="text-muted-foreground">{detailLoading ? 'Loading…' : 'Artifact'}</span>
              )
            }
            action={
              <Button variant="ghost" size="icon-sm" title="Close" onClick={closeViewer}>
                <X className="size-3.5" />
              </Button>
            }
          >
            <div className="space-y-4">
              {detail && detail.metadata && (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
                  {detail.metadata.created_at && (
                    <span className="font-mono tabular-nums">
                      {dayjs(detail.metadata.created_at as string).format('ddd D MMM YYYY HH:mm')}
                    </span>
                  )}
                  {detail.metadata.task_name && (
                    <Badge variant="outline" className="text-muted-foreground">
                      {detail.metadata.task_name as string}
                    </Badge>
                  )}
                  {Array.isArray(detail.metadata.tags) && detail.metadata.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {(detail.metadata.tags as string[]).map((tag) => (
                        <Badge key={tag} variant="outline" className="text-muted-foreground">{tag}</Badge>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <ScrollArea className="max-h-[560px]">
                {detailLoading && (
                  <div className="space-y-2">
                    <Skeleton className="h-4 w-3/4" />
                    <Skeleton className="h-4 w-full" />
                    <Skeleton className="h-4 w-5/6" />
                    <Skeleton className="h-4 w-2/3" />
                  </div>
                )}
                {!detailLoading && detail && (
                  <div className={MARKDOWN_PROSE}>
                    <RichMarkdown markdown={detail.content} />
                  </div>
                )}
                {!detailLoading && !detail && (
                  <p className="text-sm text-muted-foreground">Failed to load artifact content.</p>
                )}
              </ScrollArea>
            </div>
          </SectionCard>
        )}
      </div>
    </div>
  )
}
