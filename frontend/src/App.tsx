import { useCallback, useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'

import {
  archiveThesis,
  approveIntent,
  createChatSession,
  deleteChatSession,
  executeIntent,
  getConfig,
  getChatSessions,
  getEvents,
  getIntents,
  getRun,
  getRuns,
  getSnapshot,
  getTheses,
  refreshPortfolio,
  rejectIntent,
  runAgent,
  updateThesisStatus,
  type ApiError,
} from './api/client'
import { AgentBrief } from './components/AgentBrief'
import { AgentChatPanel } from './components/AgentChatPanel'
import { AllocationChart } from './components/AllocationChart'
import { BacktestLab } from './components/BacktestLab'
import { EventsFeed } from './components/EventsFeed'
import { IntentQueue } from './components/IntentQueue'
import { MetricGrid } from './components/MetricGrid'
import { PortfolioTable } from './components/PortfolioTable'
import { RuntimeDiagnosticsPanel } from './components/RuntimeDiagnosticsPanel'
import { SettingsPanel } from './components/SettingsPanel'
import { ThesisBoard } from './components/ThesisBoard'
import { LeveragedWorkspace } from './components/LeveragedWorkspace'
import { ScheduledJobsWorkspace } from './components/ScheduledJobsWorkspace'
import { ArtifactsWorkspace } from './components/ArtifactsWorkspace'
import type { AgentRun, AgentRunDetail, AppConfig, ChatSession, ExecutionEvent, PortfolioSnapshot, PositionItem, Thesis, TradeIntent } from './types'

function parseApiError(error: unknown): string {
  const candidate = error as ApiError
  return candidate?.response?.data?.detail || (error instanceof Error ? error.message : 'Unexpected error')
}

function aggregatePositionsByTicker(positions: PositionItem[], portfolioTotal: number): PositionItem[] {
  const riskRank: Record<string, number> = { ok: 0, oversold: 1, overbought: 1, warning: 2, critical: 3 }
  const grouped = new Map<
    string,
    PositionItem & {
      _qty_total: number
      _total_cost: number
      _mom_weighted: number
      _mom_base: number
      _rsi_weighted: number
      _rsi_base: number
      _trend_weighted: number
      _trend_base: number
      _accounts: Set<string>
    }
  >()

  for (const row of positions) {
    const rowTotalCost = Number.isFinite(row.total_cost) ? row.total_cost : Math.max(row.value - row.ppl, 0)
    const key = row.ticker
    const existing = grouped.get(key)
    if (!existing) {
      const valueBase = Math.max(row.value, 0)
      const next = {
        ...row,
        total_cost: rowTotalCost,
        account_kind: row.account_kind,
        weight: 0,
        _qty_total: row.quantity,
        _total_cost: rowTotalCost,
        _mom_weighted: (row.momentum_63d ?? 0) * valueBase,
        _mom_base: row.momentum_63d === null || row.momentum_63d === undefined ? 0 : valueBase,
        _rsi_weighted: (row.rsi_14 ?? 0) * valueBase,
        _rsi_base: row.rsi_14 === null || row.rsi_14 === undefined ? 0 : valueBase,
        _trend_weighted: (row.trend_score ?? 0) * valueBase,
        _trend_base: row.trend_score === null || row.trend_score === undefined ? 0 : valueBase,
        _accounts: new Set([row.account_kind]),
      }
      grouped.set(key, next)
      continue
    }

    const valueBase = Math.max(row.value, 0)
    existing.quantity += row.quantity
    existing.value += row.value
    existing.ppl += row.ppl
    existing.total_cost += rowTotalCost
    existing._qty_total += row.quantity
    existing._total_cost += rowTotalCost
    existing._accounts.add(row.account_kind)

    if (row.momentum_63d !== null && row.momentum_63d !== undefined) {
      existing._mom_weighted += row.momentum_63d * valueBase
      existing._mom_base += valueBase
    }
    if (row.rsi_14 !== null && row.rsi_14 !== undefined) {
      existing._rsi_weighted += row.rsi_14 * valueBase
      existing._rsi_base += valueBase
    }
    if (row.trend_score !== null && row.trend_score !== undefined) {
      existing._trend_weighted += row.trend_score * valueBase
      existing._trend_base += valueBase
    }

    const currentRank = riskRank[(existing.risk_flag || 'ok').toLowerCase()] ?? 0
    const incomingRank = riskRank[(row.risk_flag || 'ok').toLowerCase()] ?? 0
    if (incomingRank > currentRank) {
      existing.risk_flag = row.risk_flag
    }
  }

  const rows = Array.from(grouped.values()).map((row) => {
    const qty = row._qty_total
    const avgPrice = qty > 0 && row._total_cost > 0 ? row._total_cost / qty : row.average_price
    const currentPrice = qty > 0 ? row.value / qty : row.current_price
    const accountKind = row._accounts.size > 1 ? 'all' : Array.from(row._accounts)[0] || row.account_kind
    return {
      account_kind: accountKind,
      ticker: row.ticker,
      instrument_code: row.instrument_code,
      quantity: row.quantity,
      average_price: avgPrice,
      current_price: currentPrice,
      total_cost: row.total_cost,
      value: row.value,
      ppl: row.ppl,
      weight: portfolioTotal > 0 ? row.value / portfolioTotal : 0,
      momentum_63d: row._mom_base > 0 ? row._mom_weighted / row._mom_base : null,
      rsi_14: row._rsi_base > 0 ? row._rsi_weighted / row._rsi_base : null,
      trend_score: row._trend_base > 0 ? row._trend_weighted / row._trend_base : null,
      risk_flag: row.risk_flag,
    } satisfies PositionItem
  })

  return rows.sort((a, b) => b.value - a.value)
}

function obfuscateSnapshot(snapshot: PortfolioSnapshot): PortfolioSnapshot {
  const quantityFactor = 1.11
  const priceFactor = 1.23
  const moneyFactor = quantityFactor * priceFactor

  const obfuscateAmount = (value: number) => (Number.isFinite(value) ? value * moneyFactor : value)
  const obfuscatePrice = (value: number) => (Number.isFinite(value) ? value * priceFactor : value)
  const obfuscateQty = (value: number) => (Number.isFinite(value) ? value * quantityFactor : value)

  return {
    ...snapshot,
    account: {
      ...snapshot.account,
      free_cash: obfuscateAmount(snapshot.account.free_cash),
      invested: obfuscateAmount(snapshot.account.invested),
      pie_cash: obfuscateAmount(snapshot.account.pie_cash),
      total: obfuscateAmount(snapshot.account.total),
      ppl: obfuscateAmount(snapshot.account.ppl),
    },
    accounts: snapshot.accounts.map((row) => ({
      ...row,
      free_cash: obfuscateAmount(row.free_cash),
      invested: obfuscateAmount(row.invested),
      pie_cash: obfuscateAmount(row.pie_cash),
      total: obfuscateAmount(row.total),
      ppl: obfuscateAmount(row.ppl),
    })),
    positions: snapshot.positions.map((row) => ({
      ...row,
      quantity: obfuscateQty(row.quantity),
      average_price: obfuscatePrice(row.average_price),
      current_price: obfuscatePrice(row.current_price),
      total_cost: obfuscateAmount(row.total_cost),
      value: obfuscateAmount(row.value),
      ppl: obfuscateAmount(row.ppl),
    })),
    metrics: {
      ...snapshot.metrics,
      total_value: obfuscateAmount(snapshot.metrics.total_value),
      free_cash: obfuscateAmount(snapshot.metrics.free_cash),
    },
  }
}

export default function App() {
  const [activeSection, setActiveSection] = useState<'overview' | 'research' | 'execution' | 'leveraged' | 'jobs' | 'artifacts' | 'chat' | 'diagnostics'>('overview')
  const [accountView, setAccountView] = useState<'all' | 'invest' | 'stocks_isa'>('all')
  const [displayCurrency, setDisplayCurrency] = useState<'GBP' | 'USD'>('GBP')
  const [snapshot, setSnapshot] = useState<PortfolioSnapshot | null>(null)
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [activeRun, setActiveRun] = useState<AgentRunDetail | null>(null)
  const [intents, setIntents] = useState<TradeIntent[]>([])
  const [events, setEvents] = useState<ExecutionEvent[]>([])
  const [theses, setTheses] = useState<Thesis[]>([])
  const [chatSessions, setChatSessions] = useState<ChatSession[]>([])
  const [activeChatSessionId, setActiveChatSessionId] = useState<string>('')
  const [deletingChatSessionId, setDeletingChatSessionId] = useState<string | null>(null)
  const [chatSessionBusy, setChatSessionBusy] = useState(false)

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdate, setLastUpdate] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [maskSensitiveValues, setMaskSensitiveValues] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false
    return window.localStorage.getItem('mypf.presentation.mask') === '1'
  })

  const loadAll = useCallback(async (
    withRefresh = false,
    selectedAccount: 'all' | 'invest' | 'stocks_isa' = accountView,
    selectedCurrency: 'GBP' | 'USD' = displayCurrency
  ) => {
    setBusy(true)
    setError(null)
    try {
      if (withRefresh) {
        await refreshPortfolio()
      }

      const [cfg, snap, runList, intentList, eventList, thesisList] = await Promise.all([
        getConfig(),
        getSnapshot(selectedAccount, selectedCurrency),
        getRuns(),
        getIntents(),
        getEvents(),
        getTheses(120),
      ])

      setConfig(cfg)
      setSnapshot(snap)
      setRuns(runList)
      setIntents(intentList)
      setEvents(eventList)
      setTheses(thesisList)

      if (runList[0]) {
        const detail = await getRun(runList[0].id)
        setActiveRun(detail)
      } else {
        setActiveRun(null)
      }

      setLastUpdate(new Date().toISOString())
    } catch (err) {
      setError(parseApiError(err))
    } finally {
      setBusy(false)
    }
  }, [accountView, displayCurrency])

  useEffect(() => {
    void loadAll(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const bootstrapChatSessions = useCallback(async () => {
    setChatSessionBusy(true)
    try {
      const rows = await getChatSessions()
      if (rows.length > 0) {
        setChatSessions(rows)
        setActiveChatSessionId((prev) => (prev && rows.some((row) => row.id === prev) ? prev : rows[0].id))
        return
      }
      const created = await createChatSession('Portfolio Chat')
      setChatSessions([created])
      setActiveChatSessionId(created.id)
    } catch (err) {
      setError(parseApiError(err))
    } finally {
      setChatSessionBusy(false)
    }
  }, [])

  useEffect(() => {
    void bootstrapChatSessions()
  }, [bootstrapChatSessions])

  useEffect(() => {
    function onKeydown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setSettingsOpen(false)
        return
      }
      if ((event.metaKey || event.ctrlKey) && event.key === '/') {
        event.preventDefault()
        setActiveSection((prev) => (prev === 'chat' ? 'overview' : 'chat'))
      }
    }
    window.addEventListener('keydown', onKeydown)
    return () => window.removeEventListener('keydown', onKeydown)
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem('mypf.presentation.mask', maskSensitiveValues ? '1' : '0')
  }, [maskSensitiveValues])

  const displaySnapshot = useMemo(() => {
    if (!snapshot) return null
    return maskSensitiveValues ? obfuscateSnapshot(snapshot) : snapshot
  }, [snapshot, maskSensitiveValues])

  const pendingIntents = useMemo(() => intents.filter((i) => ['proposed', 'approved', 'executing'].includes(i.status)), [intents])
  const queueIntents = useMemo(
    () => pendingIntents.concat(intents.filter((i) => !pendingIntents.includes(i)).slice(0, 18)),
    [intents, pendingIntents]
  )
  const displayPositions = useMemo(() => {
    if (!displaySnapshot) {
      return []
    }
    if (accountView === 'all') {
      return aggregatePositionsByTicker(displaySnapshot.positions, displaySnapshot.account.total)
    }
    return displaySnapshot.positions.slice().sort((a, b) => b.value - a.value)
  }, [displaySnapshot, accountView])
  const activeChatSession = useMemo(
    () => chatSessions.find((row) => row.id === activeChatSessionId) || null,
    [chatSessions, activeChatSessionId]
  )

  async function handleCreateChatSession() {
    if (chatSessionBusy || deletingChatSessionId) return
    setChatSessionBusy(true)
    try {
      const created = await createChatSession(`Chat ${dayjs().format('MMM D HH:mm')}`)
      setChatSessions((prev) => [created, ...prev])
      setActiveChatSessionId(created.id)
      setActiveSection('chat')
    } catch (err) {
      setError(parseApiError(err))
    } finally {
      setChatSessionBusy(false)
    }
  }

  async function handleDeleteChatSession(sessionId: string) {
    if (chatSessionBusy || deletingChatSessionId) return
    const session = chatSessions.find((row) => row.id === sessionId)
    if (!session) return
    const confirmed = window.confirm(`Delete chat "${session.title}"? This cannot be undone.`)
    if (!confirmed) return

    setDeletingChatSessionId(sessionId)
    try {
      await deleteChatSession(sessionId)
      const remaining = chatSessions.filter((row) => row.id !== sessionId)
      setChatSessions(remaining)
      if (activeChatSessionId === sessionId) {
        if (remaining.length > 0) {
          setActiveChatSessionId(remaining[0].id)
        } else {
          const created = await createChatSession('Portfolio Chat')
          setChatSessions([created])
          setActiveChatSessionId(created.id)
        }
      }
    } catch (err) {
      setError(parseApiError(err))
    } finally {
      setDeletingChatSessionId(null)
    }
  }

  function handleChatSessionTouched(session: ChatSession) {
    setChatSessions((prev) => {
      const next = prev.filter((row) => row.id !== session.id)
      return [session, ...next]
    })
    setActiveChatSessionId(session.id)
  }

  async function runAgentNow(executeAuto = false) {
    setBusy(true)
    setError(null)
    try {
      const detail = await runAgent(true, executeAuto)
      setActiveRun(detail)
      await loadAll(false)
    } catch (err) {
      setError(parseApiError(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleApprove(id: string) {
    try {
      await approveIntent(id)
      await loadAll(false)
    } catch (err) {
      setError(parseApiError(err))
    }
  }

  async function handleReject(id: string) {
    try {
      await rejectIntent(id)
      await loadAll(false)
    } catch (err) {
      setError(parseApiError(err))
    }
  }

  async function handleExecute(id: string) {
    try {
      await executeIntent(id, false)
      await loadAll(true)
    } catch (err) {
      setError(parseApiError(err))
    }
  }

  async function handleArchiveThesis(id: string) {
    try {
      await archiveThesis(id)
      await loadAll(false)
    } catch (err) {
      setError(parseApiError(err))
    }
  }

  async function handleActivateThesis(id: string) {
    try {
      await updateThesisStatus(id, 'active')
      await loadAll(false)
    } catch (err) {
      setError(parseApiError(err))
    }
  }

  const runHistoryCard = (
    <section className="glass-card runs-card">
      <div className="section-heading-row">
        <h2>Agent Run History</h2>
        <span className="hint">Most recent reasoning cycles</span>
      </div>
      <div className="runs-list">
        {runs.map((run) => (
          <button
            key={run.id}
            className={`run-item ${activeRun?.run_id === run.id ? 'active' : ''}`}
            onClick={async () => {
              try {
                const detail = await getRun(run.id)
                setActiveRun(detail)
              } catch (err) {
                setError(parseApiError(err))
              }
            }}
          >
            <span>{dayjs(run.created_at).format('MMM D HH:mm')}</span>
            <span>{run.market_regime}</span>
            <span>{(run.portfolio_score * 100).toFixed(1)} score</span>
          </button>
        ))}
      </div>
    </section>
  )

  return (
    <div className="workspace-shell">
      <aside className="glass-card left-nav">
        <p className="eyebrow">Workspace</p>
        <button
          className={`nav-btn ${activeSection === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveSection('overview')}
        >
          <span className="nav-icon">&#9670;</span> Overview
        </button>
        <button
          className={`nav-btn ${activeSection === 'research' ? 'active' : ''}`}
          onClick={() => setActiveSection('research')}
        >
          <span className="nav-icon">&#128270;</span> Research
        </button>
        <button
          className={`nav-btn ${activeSection === 'execution' ? 'active' : ''}`}
          onClick={() => setActiveSection('execution')}
        >
          <span className="nav-icon">&#9889;</span> Execution
        </button>
        <button
          className={`nav-btn ${activeSection === 'leveraged' ? 'active' : ''}`}
          onClick={() => setActiveSection('leveraged')}
        >
          <span className="nav-icon">&#9878;</span> Leveraged
        </button>
        <button
          className={`nav-btn ${activeSection === 'jobs' ? 'active' : ''}`}
          onClick={() => setActiveSection('jobs')}
        >
          <span className="nav-icon">&#128337;</span> Jobs
        </button>
        <button
          className={`nav-btn ${activeSection === 'artifacts' ? 'active' : ''}`}
          onClick={() => setActiveSection('artifacts')}
        >
          <span className="nav-icon">&#128196;</span> Artifacts
        </button>
        <button
          className={`nav-btn ${activeSection === 'chat' ? 'active' : ''}`}
          onClick={() => setActiveSection('chat')}
        >
          <span className="nav-icon">&#128172;</span> Archie
        </button>
        <button
          className={`nav-btn ${activeSection === 'diagnostics' ? 'active' : ''}`}
          onClick={() => setActiveSection('diagnostics')}
        >
          <span className="nav-icon">&#128269;</span> Diagnostics
        </button>
        {activeSection === 'chat' && (
          <div className="nav-chat">
            <div className="nav-chat-head">
              <span>Conversations</span>
              <button className="nav-chat-new" onClick={() => void handleCreateChatSession()} disabled={chatSessionBusy}>
                +
              </button>
            </div>
            <div className="nav-chat-list">
              {chatSessions.length === 0 && <span className="hint">No chats yet.</span>}
              {chatSessions.map((session) => (
                <div key={session.id} className={`nav-chat-row ${session.id === activeChatSessionId ? 'active' : ''}`}>
                  <button
                    className="nav-chat-item"
                    onClick={() => setActiveChatSessionId(session.id)}
                    disabled={chatSessionBusy || Boolean(deletingChatSessionId)}
                  >
                    <span>{session.title}</span>
                    <small>{dayjs(session.updated_at).format('MMM D HH:mm')}</small>
                  </button>
                  <button
                    className="nav-chat-delete"
                    onClick={() => void handleDeleteChatSession(session.id)}
                    disabled={chatSessionBusy || deletingChatSessionId === session.id}
                    title="Delete chat"
                  >
                    {deletingChatSessionId === session.id ? '...' : '×'}
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
        <button
          className="nav-btn"
          onClick={() => setSettingsOpen(true)}
        >
          <span className="nav-icon">&#9881;</span> Settings
        </button>
        <div className="nav-meta">
          <span>{pendingIntents.length} pending intents</span>
          <span>{theses.filter((row) => row.status === 'active').length} active theses</span>
        </div>
      </aside>

      <div className="app-shell">
        {activeSection !== 'chat' && (
        <header className="topbar">
          <div>
            <p className="eyebrow">MYPF</p>
            <h1>Portfolio Operator</h1>
            <p className="muted">
              Signal-rich dashboard with intent-driven execution and hard risk rails.
            </p>
          </div>
          <div className="topbar-actions">
            <select
              value={accountView}
              onChange={(e) => {
                const next = e.target.value as 'all' | 'invest' | 'stocks_isa'
                setAccountView(next)
                void loadAll(false, next, displayCurrency)
              }}
            >
              <option value="all">All Accounts</option>
              <option value="invest">Invest</option>
              <option value="stocks_isa">Stocks ISA</option>
            </select>
            <select
              value={displayCurrency}
              onChange={(e) => {
                const next = e.target.value as 'GBP' | 'USD'
                setDisplayCurrency(next)
                void loadAll(false, accountView, next)
              }}
            >
              <option value="GBP">GBP</option>
              <option value="USD">USD</option>
            </select>
            <button className="btn" onClick={() => void loadAll(true)} disabled={busy}>
              Refresh
            </button>
            <button className="btn primary" onClick={() => void runAgentNow(false)} disabled={busy}>
              Run Agent
            </button>
            <button className="btn danger" onClick={() => void runAgentNow(true)} disabled={busy || !config?.broker.autopilot_enabled}>
              Autopilot
            </button>
          </div>
        </header>
        )}

        {activeSection !== 'chat' && (
          <div className="status-row">
            <span>{busy ? 'Syncing...' : 'Ready'}</span>
            {lastUpdate && <span>Last update {dayjs(lastUpdate).format('MMM D HH:mm:ss')}</span>}
            {config && (
              <span>
                Mode {config.broker.broker_mode.toUpperCase()} / Env {config.broker.t212_base_env.toUpperCase()} / Autopilot{' '}
                {config.broker.autopilot_enabled ? 'ON' : 'OFF'}
              </span>
            )}
            {config && (
              <span>
                Invest {config.credentials.invest.configured ? 'configured' : 'missing'} / ISA{' '}
                {config.credentials.stocks_isa.configured ? 'configured' : 'missing'}
              </span>
            )}
            {maskSensitiveValues && <span>Presentation mode ON (obfuscated values)</span>}
          </div>
        )}

        {error && <div className="error-banner">{error}</div>}

        {activeSection !== 'chat' && activeSection !== 'leveraged' && activeSection !== 'jobs' && activeSection !== 'artifacts' && activeSection !== 'diagnostics' && displaySnapshot && (
          <MetricGrid snapshot={displaySnapshot} positions={displayPositions} accountView={accountView} />
        )}

        <main
          className={
            activeSection === 'leveraged'
              ? 'leveraged-stage'
              : activeSection === 'jobs'
                ? 'jobs-stage'
                : activeSection === 'artifacts'
                  ? 'artifacts-stage'
                  : activeSection === 'diagnostics'
                    ? 'diagnostics-stage'
                    : 'content-grid'
          }
          style={activeSection === 'chat' ? { display: 'none' } : undefined}
        >
          {activeSection === 'overview' && (
            <>
              <div className="left-stack">
                {displaySnapshot && <AllocationChart positions={displayPositions} />}
                <AgentBrief markdown={activeRun?.summary_markdown || null} />
              </div>
              <div className="right-stack">
                {displaySnapshot && (
                  <PortfolioTable positions={displayPositions} accountView={accountView} displayCurrency={displayCurrency} />
                )}
              </div>
            </>
          )}

          {activeSection === 'research' && (
            <>
              <div className="left-stack">
                <ThesisBoard theses={theses} onArchive={handleArchiveThesis} onActivate={handleActivateThesis} />
                <BacktestLab onError={setError} />
              </div>
              <div className="right-stack">
                <EventsFeed events={events} />
                {runHistoryCard}
              </div>
            </>
          )}

          {activeSection === 'execution' && (
            <>
              <div className="left-stack">
                <IntentQueue intents={queueIntents} onApprove={handleApprove} onReject={handleReject} onExecute={handleExecute} />
              </div>
              <div className="right-stack">
                {runHistoryCard}
                <EventsFeed events={events} />
              </div>
            </>
          )}

          {activeSection === 'leveraged' && (
            <LeveragedWorkspace onError={setError} />
          )}

          {activeSection === 'jobs' && (
            <ScheduledJobsWorkspace onError={setError} />
          )}

          {activeSection === 'artifacts' && (
            <ArtifactsWorkspace onError={setError} />
          )}

          <div style={activeSection === 'diagnostics' ? undefined : { display: 'none' }}>
            <RuntimeDiagnosticsPanel onError={setError} />
          </div>
        </main>

        <div
          className="chat-stage-wrap"
          style={activeSection === 'chat' ? undefined : { display: 'none' }}
        >
          <AgentChatPanel
            activeSessionId={activeChatSessionId}
            activeSessionTitle={activeChatSession?.title || null}
            accountView={accountView}
            displayCurrency={displayCurrency}
            presentationMask={maskSensitiveValues}
            onSessionTouched={handleChatSessionTouched}
            onError={setError}
            deletingSessionId={deletingChatSessionId}
          />
        </div>
      </div>

      {settingsOpen && (
        <div className="modal-overlay" onClick={() => setSettingsOpen(false)}>
          <div className="modal-sheet" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h2>Control Tower</h2>
              <button className="btn ghost" onClick={() => setSettingsOpen(false)}>
                Close
              </button>
            </div>
            <SettingsPanel
              config={config}
              onReload={() => void loadAll(false)}
              onError={(msg) => setError(msg)}
              hideHeader
              presentationMask={maskSensitiveValues}
              onTogglePresentationMask={setMaskSensitiveValues}
            />
          </div>
        </div>
      )}
    </div>
  )
}
