import { useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'

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
  if (s === 'ok') return 'ok'
  if (s === 'error') return 'error'
  return 'warn'
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
    <section className="glass-card runtime-diag-card">
      <div className="runtime-diag-head">
        <h3>Runtime Diagnostics</h3>
        <div className="runtime-diag-actions">
          <span className={`status-chip ${summaryOk ? 'ok' : 'warn'}`}>
            {summaryOk ? 'All systems ready' : 'Missing capabilities'}
          </span>
          <button className="btn ghost" onClick={() => void checkHealth()} disabled={healthLoading}>
            {healthLoading ? 'Checking MCP…' : 'Check MCP Health'}
          </button>
          <button className="btn ghost" onClick={() => void refresh()} disabled={busy}>
            {busy ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      <div className="runtime-cap-grid">
        {capabilities.map((item) => (
          <article key={item.label} className={`runtime-cap-item ${statusDotClass(item.status)}`}>
            <div className="runtime-cap-title">
              <span>{item.label}</span>
              <span className={`runtime-cap-dot ${statusDotClass(item.status)}`}>{statusLabel(item.status)}</span>
            </div>
            <p>{item.detail}</p>
          </article>
        ))}
      </div>

      {runtime && (
        <>
          <div className="runtime-meta-grid">
            <div><span className="muted">Model</span><strong>{runtime.claude_model}</strong></div>
            <div><span className="muted">Memory model</span><strong>{runtime.claude_memory_model}</strong></div>
            <div><span className="muted">Memory strategy</span><strong>{runtime.memory_strategy || 'n/a'}</strong></div>
            <div><span className="muted">Setting sources</span><strong>{runtime.setting_sources.join(', ') || 'none'}</strong></div>
            <div><span className="muted">MCP servers</span><strong>{runtime.mcp_servers.join(', ') || 'none'}</strong></div>
            <div><span className="muted">Last check</span><strong>{lastChecked ? dayjs(lastChecked).format('MMM D HH:mm:ss') : '—'}</strong></div>
          </div>

          <div className="runtime-paths">
            <p><span className="muted">CWD</span> {runtime.cwd}</p>
            <p><span className="muted">Skills dir</span> {runtime.skills_dir}</p>
            <p><span className="muted">Memory file</span> {runtime.memory_file}</p>
          </div>

          <div className="runtime-skills">
            <div className="runtime-skills-head">
              <h4>Discovered Skills</h4>
              <span className="muted">{skills.length} loaded</span>
            </div>
            {skills.length === 0 ? (
              <p className="runtime-empty">No skills discovered from current setting sources.</p>
            ) : (
              <ul className="runtime-skill-list">
                {skills.map((skill) => (
                  <li key={skill.path}>
                    <span className="runtime-skill-name">{skill.label}</span>
                    <code>{skill.path}</code>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="runtime-tools-toggle">
            <button className="btn ghost" onClick={() => setShowTools((prev) => !prev)}>
              {showTools ? 'Hide tool map' : 'Show tool map'}
            </button>
          </div>

          {showTools && (
            <div className="runtime-tool-groups">
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
                  <div key={key} className={healthy ? '' : 'unhealthy'}>
                    <h4>
                      {label}
                      <span className="tool-group-count">{cleaned.length}</span>
                      {!healthy && <span className="tool-group-badge">server error</span>}
                    </h4>
                    {cleaned.length === 0 ? (
                      <p className="muted">none</p>
                    ) : (
                      <ul className="tool-grid">
                        {cleaned.map((t) => <li key={t}>{t}</li>)}
                      </ul>
                    )}
                  </div>
                )
              })}
              {groupedTools.other.length > 0 && (
                <div>
                  <h4>Other MCP</h4>
                  <ul className="tool-grid">
                    {groupedTools.other
                      .map((t) => t.replace(/^mcp__[^_]+__/, ''))
                      .sort((a, b) => a.localeCompare(b))
                      .map((t) => <li key={t}>{t}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
