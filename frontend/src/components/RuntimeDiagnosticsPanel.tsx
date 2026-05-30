import { useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react'

import { SectionCard } from '@/components/kit'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

import { getChatRuntime, getMcpHealth } from '../api/client'
import type { ChatRuntimeInfo } from '../types'

interface Props {
  onError: (message: string) => void
}

type McpHealthMap = Record<string, { status: string; detail: string }>

type CapabilityStatus = 'ok' | 'error' | 'unchecked'

type Capability = {
  label: string
  status: CapabilityStatus
  detail: string
}

function statusLabel(s: CapabilityStatus): string {
  if (s === 'ok') return 'healthy'
  if (s === 'error') return 'error'
  return 'unchecked'
}

function statusDotClass(s: CapabilityStatus): string {
  if (s === 'ok') return 'bg-positive'
  if (s === 'error') return 'bg-negative'
  return 'bg-muted-foreground'
}

function statusTextClass(s: CapabilityStatus): string {
  if (s === 'ok') return 'text-positive'
  if (s === 'error') return 'text-negative'
  return 'text-muted-foreground'
}

export function RuntimeDiagnosticsPanel({ onError }: Props) {
  const [runtime, setRuntime] = useState<ChatRuntimeInfo | null>(null)
  const [mcpHealth, setMcpHealth] = useState<McpHealthMap | null>(null)
  const [busy, setBusy] = useState(false)
  const [healthLoading, setHealthLoading] = useState(false)
  const [lastChecked, setLastChecked] = useState<string | null>(null)
  const [showTools, setShowTools] = useState(false)

  async function refresh() {
    setBusy(true)
    try {
      const [data, health] = await Promise.all([
        getChatRuntime(),
        getMcpHealth().catch(() => null),
      ])
      setRuntime(data)
      setMcpHealth(health)
      setLastChecked(new Date().toISOString())
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Failed to load runtime diagnostics'
      onError(msg)
    } finally {
      setBusy(false)
    }
  }

  async function checkHealth() {
    setHealthLoading(true)
    try {
      const health = await getMcpHealth()
      setMcpHealth(health)
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'MCP health check failed'
      onError(msg)
    } finally {
      setHealthLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const groupedTools = useMemo(() => {
    const groups: Record<string, string[]> = {
      core: [],
      trading212: [],
      marketdata: [],
      scheduler: [],
      other: [],
    }

    for (const tool of runtime?.allowed_tools || []) {
      if (!tool.startsWith('mcp__')) {
        groups.core.push(tool)
        continue
      }
      if (tool.startsWith('mcp__trading212__')) groups.trading212.push(tool)
      else if (tool.startsWith('mcp__marketdata__')) groups.marketdata.push(tool)
      else if (tool.startsWith('mcp__scheduler__')) groups.scheduler.push(tool)
      else groups.other.push(tool)
    }

    return groups
  }, [runtime?.allowed_tools])

  /** Map tool-group key → MCP health key (null = no MCP dependency). */
  const mcpKeyForGroup: Record<string, string | null> = {
    core: null,
    trading212: 'trading212',
    marketdata: 'marketdata',
    scheduler: 'scheduler',
    other: null,
  }

  function isGroupHealthy(groupKey: string): boolean {
    const healthKey = mcpKeyForGroup[groupKey]
    if (!healthKey) return true // core tools have no MCP dependency
    if (!mcpHealth) return true // not checked yet — don't mark as broken
    const entry = mcpHealth[healthKey]
    return entry?.status === 'ok'
  }

  const skills = useMemo(() => {
    return (runtime?.skill_files || []).map((path) => {
      const parts = path.split('/')
      const label = parts.length >= 2 ? parts[parts.length - 2] : path
      return { label, path }
    })
  }, [runtime?.skill_files])

  const capabilities = useMemo<Capability[]>(() => {
    const tools = runtime?.allowed_tools || []

    function mcpCap(label: string, serverKey: string): Capability {
      if (!mcpHealth) {
        return { label, status: 'unchecked', detail: 'checking...' }
      }
      const entry = mcpHealth[serverKey]
      if (!entry) {
        return { label, status: 'error', detail: 'error: not configured' }
      }
      if (entry.status === 'ok') {
        return { label, status: 'ok', detail: entry.detail }
      }
      return { label, status: 'error', detail: `error: ${entry.detail}` }
    }

    return [
      {
        label: 'Skills',
        status: tools.includes('Skill') && skills.length > 0 ? 'ok' : 'error',
        detail: `${skills.length} skills discovered`,
      },
      mcpCap('Trading 212 MCP', 'trading212'),
      mcpCap('Market Data MCP', 'marketdata'),
      mcpCap('Scheduler MCP', 'scheduler'),
      {
        label: 'Write Access',
        status: tools.includes('Write') || tools.includes('Edit') ? 'ok' : 'error',
        detail: tools.includes('Write') || tools.includes('Edit') ? 'enabled' : 'read-only mode',
      },
    ]
  }, [runtime, skills.length, mcpHealth])

  const summaryOk = capabilities.every((item) => item.status === 'ok')

  return (
    <SectionCard
      title="Runtime Diagnostics"
      description="Archie's live capability and tool surface"
      action={
        <>
          <Badge variant={summaryOk ? 'secondary' : 'outline'} className="gap-1.5">
            <span className={cn('size-1.5 rounded-full', summaryOk ? 'bg-positive' : 'bg-warning')} />
            {summaryOk ? 'All systems ready' : 'Missing capabilities'}
          </Badge>
          <Button variant="ghost" size="sm" onClick={() => void checkHealth()} disabled={healthLoading}>
            {healthLoading ? 'Checking MCP…' : 'Check MCP Health'}
          </Button>
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={busy}>
            <RefreshCw className={busy ? 'animate-spin' : undefined} />
            {busy ? 'Refreshing…' : 'Refresh'}
          </Button>
        </>
      }
      contentClassName="space-y-5"
    >
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {capabilities.map((item) => (
          <Card key={item.label} className="gap-2 p-4">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium">{item.label}</span>
              <span className={cn('flex items-center gap-1.5 text-xs', statusTextClass(item.status))}>
                <span className={cn('size-1.5 rounded-full', statusDotClass(item.status))} />
                {statusLabel(item.status)}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">{item.detail}</p>
          </Card>
        ))}
      </div>

      {runtime && (
        <>
          <dl className="grid gap-x-6 gap-y-3 rounded-lg border border-border/60 bg-muted/30 p-4 sm:grid-cols-2 lg:grid-cols-3">
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">Model</dt>
              <dd className="font-mono text-sm">{runtime.claude_model}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">Memory model</dt>
              <dd className="font-mono text-sm">{runtime.claude_memory_model}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">Memory strategy</dt>
              <dd className="font-mono text-sm">{runtime.memory_strategy || 'n/a'}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">Setting sources</dt>
              <dd className="font-mono text-sm">{runtime.setting_sources.join(', ') || 'none'}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">MCP servers</dt>
              <dd className="font-mono text-sm">{runtime.mcp_servers.join(', ') || 'none'}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">Last check</dt>
              <dd className="font-mono text-sm">
                {lastChecked ? dayjs(lastChecked).format('MMM D HH:mm:ss') : '—'}
              </dd>
            </div>
            <div className="flex flex-col gap-0.5 sm:col-span-2 lg:col-span-3">
              <dt className="text-xs text-muted-foreground">CWD</dt>
              <dd className="break-all font-mono text-sm">{runtime.cwd}</dd>
            </div>
            <div className="flex flex-col gap-0.5 sm:col-span-2 lg:col-span-3">
              <dt className="text-xs text-muted-foreground">Skills dir</dt>
              <dd className="break-all font-mono text-sm">{runtime.skills_dir}</dd>
            </div>
            <div className="flex flex-col gap-0.5 sm:col-span-2 lg:col-span-3">
              <dt className="text-xs text-muted-foreground">Memory file</dt>
              <dd className="break-all font-mono text-sm">{runtime.memory_file}</dd>
            </div>
          </dl>

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold tracking-tight">Discovered Skills</h3>
              <span className="text-xs text-muted-foreground">{skills.length} loaded</span>
            </div>
            {skills.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No skills discovered from current setting sources.
              </p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {skills.map((skill) => (
                  <Badge key={skill.path} variant="outline" title={skill.path}>
                    {skill.label}
                  </Badge>
                ))}
              </div>
            )}
          </div>

          <div className="space-y-3">
            <Button variant="ghost" size="sm" onClick={() => setShowTools((prev) => !prev)}>
              {showTools ? <ChevronDown /> : <ChevronRight />}
              {showTools ? 'Hide tool map' : 'Show tool map'}
            </Button>

            {showTools && (
              <div className="space-y-4">
                {([
                  ['core', 'Core'],
                  ['trading212', 'Trading 212'],
                  ['marketdata', 'Market Data'],
                  ['scheduler', 'Scheduler'],
                ] as const).map(([key, label]) => {
                  const tools = groupedTools[key]
                  const healthy = isGroupHealthy(key)
                  const cleaned = tools
                    .map((t) => t.replace(/^mcp__[^_]+__/, ''))
                    .sort((a, b) => a.localeCompare(b))
                  return (
                    <div key={key} className="space-y-2">
                      <div className="flex items-center gap-2">
                        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                          {label}
                        </h4>
                        <Badge variant="secondary">{cleaned.length}</Badge>
                        {!healthy && <Badge variant="destructive">server error</Badge>}
                      </div>
                      {cleaned.length === 0 ? (
                        <p className="text-xs text-muted-foreground">none</p>
                      ) : (
                        <div className="flex flex-wrap gap-1.5">
                          {cleaned.map((t) => (
                            <Badge key={t} variant="outline" className="font-mono">
                              {t}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
                {groupedTools.other.length > 0 && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      Other MCP
                    </h4>
                    <div className="flex flex-wrap gap-1.5">
                      {groupedTools.other
                        .map((t) => t.replace(/^mcp__[^_]+__/, ''))
                        .sort((a, b) => a.localeCompare(b))
                        .map((t) => (
                          <Badge key={t} variant="outline" className="font-mono">
                            {t}
                          </Badge>
                        ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </SectionCard>
  )
}
