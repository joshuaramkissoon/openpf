import { useCallback, useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'

import { listArtifacts, getArtifact } from '../api/client'
import { RichMarkdown } from './RichMarkdown'
import type { ArtifactItem, ArtifactDetail } from '../types'

interface Props {
  onError: (message: string | null) => void
}

type TypeFilter = string

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

  return (
    <div className="artifacts-workspace">
      {/* Header */}
      <section className="glass-card artifacts-header-card">
        <div className="section-heading-row">
          <div className="artifacts-header-left">
            <h2>Artifacts</h2>
            <div className="artifacts-header-stats">
              <span className="jobs-stat">
                <span className="jobs-stat-count">{filteredArtifacts.length}</span>
                {typeFilter === 'all' ? ' total' : ` ${typeLabel(typeFilter)}`}
              </span>
            </div>
          </div>
          <div className="artifacts-header-actions">
            <select
              className="jobs-sort-select"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value as TypeFilter)}
            >
              <option value="all">All Types</option>
              {uniqueTypes.map((t) => (
                <option key={t} value={t}>{typeLabel(t)}</option>
              ))}
            </select>
            <button className="btn" onClick={() => void loadArtifacts()} disabled={busy}>
              {busy ? 'Loading...' : 'Refresh'}
            </button>
          </div>
        </div>
      </section>

      {/* Main content area */}
      <div className={`artifacts-body${selectedPath ? ' with-viewer' : ''}`}>
        {/* Artifact list */}
        <section className="glass-card artifacts-list-card">
          {filteredArtifacts.length === 0 && !busy && (
            <div className="artifacts-empty-state">
              <div className="artifacts-empty-icon">&#128196;</div>
              <p className="artifacts-empty-title">No artifacts yet</p>
              <p className="artifacts-empty-hint">
                Archie produces artifacts from scheduled jobs and chat analysis.
                They'll appear here automatically.
              </p>
            </div>
          )}

          {filteredArtifacts.length > 0 && (
            <div className="artifacts-list">
              {filteredArtifacts.map((artifact) => (
                <button
                  key={artifact.path}
                  className={`artifact-row${selectedPath === artifact.path ? ' active' : ''}`}
                  onClick={() => void openArtifact(artifact.path)}
                >
                  <div className="artifact-row-top">
                    <span className={`artifact-type-badge ${artifact.type.replace(/\s+/g, '-').toLowerCase()}`}>
                      {typeLabel(artifact.type)}
                    </span>
                    <span className="artifact-title">{artifact.title}</span>
                    <span className="artifact-time">{relativeTime(artifact.created_at)}</span>
                  </div>
                  <div className="artifact-row-bottom">
                    <span className="artifact-path">{pathBreadcrumb(artifact.path)}</span>
                    {artifact.task_name && (
                      <span className="artifact-task-name">{artifact.task_name}</span>
                    )}
                    <span className="artifact-size">{formatBytes(artifact.size_bytes)}</span>
                  </div>
                  {artifact.tags && artifact.tags.length > 0 && (
                    <div className="artifact-tags">
                      {artifact.tags.map((tag) => (
                        <span key={tag} className="artifact-tag">{tag}</span>
                      ))}
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}
        </section>

        {/* Artifact viewer panel */}
        {selectedPath && (
          <section className="glass-card artifact-viewer">
            <div className="artifact-viewer-header">
              <div className="artifact-viewer-title-row">
                {detail && (
                  <>
                    <span className={`artifact-type-badge ${((detail.metadata?.type as string) || 'adhoc').replace(/\s+/g, '-').toLowerCase()}`}>
                      {typeLabel((detail.metadata?.type as string) || 'adhoc')}
                    </span>
                    <strong>{detail.metadata?.title as string || selectedPath}</strong>
                  </>
                )}
                {!detail && detailLoading && <span className="muted">Loading...</span>}
              </div>
              <button className="btn ghost artifact-viewer-close" onClick={closeViewer}>
                Close
              </button>
            </div>

            {detail && detail.metadata && (
              <div className="artifact-viewer-meta">
                {detail.metadata.created_at && (
                  <span>{dayjs(detail.metadata.created_at as string).format('ddd D MMM YYYY HH:mm')}</span>
                )}
                {detail.metadata.task_name && (
                  <span className="artifact-task-name">{detail.metadata.task_name as string}</span>
                )}
                {Array.isArray(detail.metadata.tags) && detail.metadata.tags.length > 0 && (
                  <div className="artifact-tags">
                    {(detail.metadata.tags as string[]).map((tag) => (
                      <span key={tag} className="artifact-tag">{tag}</span>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="artifact-viewer-content">
              {detailLoading && <p className="muted">Loading artifact content...</p>}
              {!detailLoading && detail && (
                <div className="chat-markdown">
                  <RichMarkdown markdown={detail.content} />
                </div>
              )}
              {!detailLoading && !detail && (
                <p className="muted">Failed to load artifact content.</p>
              )}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
