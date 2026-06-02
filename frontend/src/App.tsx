import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dayjs from 'dayjs'
import { Loader2, Menu, Plus, RefreshCw, X } from 'lucide-react'

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
  updateThesisStatus,
  type ApiError,
} from './api/client'
import { AgentChatPanel } from './components/AgentChatPanel'
import { BacktestLab } from './components/BacktestLab'
import { EventsFeed } from './components/EventsFeed'
import { IntentQueue } from './components/IntentQueue'
import { OrdersWorkspace } from './components/OrdersWorkspace'
import { RuntimeDiagnosticsPanel } from './components/RuntimeDiagnosticsPanel'
import { SettingsPanel } from './components/SettingsPanel'
import { ThesisBoard } from './components/ThesisBoard'
import { WatchlistBoard } from './components/WatchlistBoard'
import { LeveragedWorkspace } from './components/LeveragedWorkspace'
import { ScheduledJobsWorkspace } from './components/ScheduledJobsWorkspace'
import { ArtifactsWorkspace } from './components/ArtifactsWorkspace'
import { CostsWorkspace } from './components/CostsWorkspace'
import { AppSidebar, SidebarBody, type SectionKey } from '@/components/layout/app-sidebar'
import { AttentionFeed, AttentionChip } from '@/components/attention/attention-feed'
import { PortfolioOverview } from '@/components/portfolio/portfolio-overview'
import { ResearchDesk } from '@/components/research/research-desk'
import { HelpGuide } from '@/components/help/help-guide'
import { SectionCard } from '@/components/kit'
import {
  PrivacyProvider,
  SCRAMBLE_MONEY_FACTOR,
  loadPrivacyMode,
  nextPrivacyMode,
  privacyModeLabel,
  savePrivacyMode,
  type PrivacyMode,
} from '@/lib/privacy'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { toast } from 'sonner'
import { toastApiError } from '@/lib/api-error'
import type { AgentRun, AgentRunDetail, AppConfig, ChatSession, ExecutionEvent, PortfolioSnapshot, PositionItem, Thesis, TradeIntent } from './types'

function parseApiError(error: unknown): string {
  const detail = (error as ApiError)?.response?.data?.detail as unknown
  // New endpoints return a typed envelope ({code, message, meta}); older ones a string.
  if (detail && typeof detail === 'object') {
    return (detail as { message?: string }).message || 'Request failed'
  }
  if (typeof detail === 'string' && detail) return detail
  return error instanceof Error ? error.message : 'Unexpected error'
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
      name: row.name,
      yfinance_ticker: row.yfinance_ticker,
      instrument_currency: row.instrument_currency,
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
  const moneyFactor = SCRAMBLE_MONEY_FACTOR // == quantityFactor * priceFactor; shared so the chart matches

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

const SECTION_LABELS: Record<SectionKey, string> = {
  overview: 'Portfolio',
  attention: 'Attention',
  watchlist: 'Watchlist',
  chat: 'Archie',
  execution: 'Execution',
  orders: 'Orders',
  leveraged: 'Leveraged Desk',
  jobs: 'Scheduled Jobs',
  artifacts: 'Artifacts',
  analysis: 'Research Desk',
  research: 'Insights',
  costs: 'Usage',
  diagnostics: 'Diagnostics',
  help: 'Help & Guide',
}

const SECTION_DESCRIPTIONS: Partial<Record<SectionKey, string>> = {
  overview: 'Holdings, allocation, and risk at a glance.',
  attention: "What Archie's spotted across your holdings, news, and macro — ranked.",
  watchlist: 'Tracked ideas — Archie watches these and flags what\'s worth noticing.',
  execution: 'Review and act on proposed trade intents.',
  orders: 'Live broker orders — view in-flight orders, cancel, and browse history.',
  leveraged: 'Leveraged positions, rails, and signal queue.',
  jobs: 'Automated agent routines on a schedule.',
  artifacts: 'Generated briefings and reports.',
  analysis: 'Ask Archie to analyze a holding or a new idea — live data, forecast, verdict.',
  research: 'Theses, backtests, and agent reasoning history.',
  costs: 'Token usage on your Claude subscription (estimated).',
  diagnostics: 'Runtime, MCP servers, and capabilities.',
  help: 'What the app can do and how to use it.',
}

export default function App() {
  const [activeSection, setActiveSection] = useState<SectionKey>('overview')
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
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [chatRailOpen, setChatRailOpen] = useState(false)
  const [navCollapsed, setNavCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem('openpf:nav-collapsed') === '1' } catch { return false }
  })
  const toggleNavCollapsed = useCallback(() => {
    setNavCollapsed((prev) => {
      const next = !prev
      try { localStorage.setItem('openpf:nav-collapsed', next ? '1' : '0') } catch { /* ignore */ }
      return next
    })
  }, [])
  const [privacyMode, setPrivacyMode] = useState<PrivacyMode>(() => loadPrivacyMode())

  // Guards for the background (fire-and-forget) snapshot re-pull in loadAll: don't
  // setState after unmount, and don't let a stale refresh clobber a newer load.
  const mountedRef = useRef(true)
  const loadIdRef = useRef(0)
  useEffect(() => () => { mountedRef.current = false }, [])

  const loadAll = useCallback(async (
    withRefresh = false,
    selectedAccount: 'all' | 'invest' | 'stocks_isa' = accountView,
    selectedCurrency: 'GBP' | 'USD' = displayCurrency,
    force = false
  ) => {
    const myLoadId = ++loadIdRef.current
    setBusy(true)
    setError(null)
    try {
      if (withRefresh) {
        // Kick the live refresh off WITHOUT blocking the dashboard render. The
        // snapshot endpoint serves the last stored snapshot (and self-populates on
        // a cold start), so the UI paints immediately; when the background refresh
        // lands we re-pull the snapshot to show fresh numbers. Previously we
        // awaited the refresh first, so a slow/queued refresh (e.g. stuck behind a
        // scheduled job) hung the whole dashboard until the 30s client timeout.
        void refreshPortfolio(force)
          .then(async () => {
            const fresh = await getSnapshot(selectedAccount, selectedCurrency)
            // Bail if we unmounted or a newer load started, so a stale background
            // refresh never clobbers the current account/currency selection.
            if (!mountedRef.current || loadIdRef.current !== myLoadId) return
            setSnapshot(fresh)
            setLastUpdate(new Date().toISOString())
          })
          .catch(() => {
            /* non-fatal — the 60s poll / next load will retry */
          })
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
    // Refresh-on-load: pull live data when the dashboard opens (server cooldown
    // collapses rapid reloads, so this is cheap).
    void loadAll(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Silent snapshot refresh used by the 60s poll + tab-focus — no busy spinner,
  // snapshot-only, errors swallowed (a transient poll failure shouldn't banner).
  const refreshSnapshotSilently = useCallback(async () => {
    try {
      await refreshPortfolio()
      const snap = await getSnapshot(accountView, displayCurrency)
      setSnapshot(snap)
      setLastUpdate(new Date().toISOString())
    } catch {
      /* silent — poll failures are non-fatal */
    }
  }, [accountView, displayCurrency])

  // Auto-refresh the dashboard every 60s while the Portfolio tab is active and
  // the browser tab is visible, plus immediately when the tab regains focus.
  useEffect(() => {
    function maybeRefresh() {
      if (activeSection === 'overview' && document.visibilityState === 'visible') {
        void refreshSnapshotSilently()
      }
    }
    const id = window.setInterval(maybeRefresh, 60_000)
    document.addEventListener('visibilitychange', maybeRefresh)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', maybeRefresh)
    }
  }, [activeSection, refreshSnapshotSilently])

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
    function isTypingTarget(): boolean {
      const el = document.activeElement
      if (!el) return false
      const tag = el.tagName
      return (
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        (el as HTMLElement).isContentEditable === true
      )
    }
    function onKeydown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setSettingsOpen(false)
        return
      }
      if ((event.metaKey || event.ctrlKey) && event.key === '/') {
        event.preventDefault()
        setActiveSection((prev) => (prev === 'chat' ? 'overview' : 'chat'))
        return
      }
      // `p` cycles privacy mode — but only as a bare key, never while typing
      // in a field or with a modifier held (so it doesn't hijack Cmd+P etc.).
      if (
        (event.key === 'p' || event.key === 'P') &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.altKey &&
        !isTypingTarget()
      ) {
        event.preventDefault()
        setPrivacyMode((prev) => nextPrivacyMode(prev))
      }
      // `/` toggles the sidebar collapse — bare key only, never while typing or
      // with a modifier held (Cmd/Ctrl+/ still switches section).
      if (
        event.key === '/' &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.altKey &&
        !isTypingTarget()
      ) {
        event.preventDefault()
        toggleNavCollapsed()
      }
    }
    window.addEventListener('keydown', onKeydown)
    return () => window.removeEventListener('keydown', onKeydown)
  }, [toggleNavCollapsed])

  useEffect(() => {
    savePrivacyMode(privacyMode)
  }, [privacyMode])

  // Scramble swaps in non-real numbers upstream; blur/off render the real
  // snapshot (blur is applied visually by the figure components downstream).
  const displaySnapshot = useMemo(() => {
    if (!snapshot) return null
    return privacyMode === 'scramble' ? obfuscateSnapshot(snapshot) : snapshot
  }, [snapshot, privacyMode])

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

  async function handleExecute(id: string, accountKind?: 'invest' | 'stocks_isa') {
    try {
      await executeIntent(id, false, accountKind)
      const acct = accountKind === 'stocks_isa' ? 'Stocks ISA' : accountKind === 'invest' ? 'Invest' : undefined
      const paper = config?.broker.broker_mode === 'paper'
      toast.success(paper ? 'Paper fill recorded' : 'Order submitted to broker', {
        description: acct ? `Account: ${acct}` : undefined,
      })
      await loadAll(true)
    } catch (err) {
      toastApiError(err, 'Execution failed')
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
    <SectionCard title="Agent Run History" description="Most recent reasoning cycles" noPadding>
      <ScrollArea className="max-h-[420px]">
        <div className="divide-y divide-border/50">
          {runs.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted-foreground">No agent runs yet.</p>
          ) : (
            runs.map((run) => (
              <button
                key={run.id}
                className={cn(
                  'flex w-full items-center justify-between gap-3 px-5 py-3 text-left text-sm transition-colors hover:bg-muted/40',
                  activeRun?.run_id === run.id && 'bg-muted/50',
                )}
                onClick={async () => {
                  try {
                    const detail = await getRun(run.id)
                    setActiveRun(detail)
                  } catch (err) {
                    setError(parseApiError(err))
                  }
                }}
              >
                <span className="font-mono text-xs tabular-nums text-muted-foreground">
                  {dayjs(run.created_at).format('MMM D HH:mm')}
                </span>
                <span className="capitalize">{run.market_regime}</span>
                <span className="font-mono text-xs tabular-nums">{(run.portfolio_score * 100).toFixed(1)}</span>
              </button>
            ))
          )}
        </div>
      </ScrollArea>
    </SectionCard>
  )

  const chatRail = (
    <>
      <div className="flex items-center justify-between px-3 py-2.5">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          Conversations
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={() => void handleCreateChatSession()}
          disabled={chatSessionBusy}
        >
          <Plus className="size-4" />
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <div className="flex flex-col gap-0.5 px-2 pb-2">
          {chatSessions.length === 0 ? (
            <p className="px-2 py-3 text-xs text-muted-foreground">No chats yet.</p>
          ) : (
            chatSessions.map((session) => (
              <div
                key={session.id}
                className={cn(
                  'group flex items-center gap-1 rounded-lg px-2.5 py-2 text-sm transition-colors',
                  session.id === activeChatSessionId
                    ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                    : 'text-muted-foreground hover:bg-sidebar-accent/50',
                )}
              >
                <button
                  className="flex min-w-0 flex-1 flex-col items-start text-left"
                  onClick={() => {
                    setActiveChatSessionId(session.id)
                    setChatRailOpen(false)
                  }}
                  disabled={chatSessionBusy || Boolean(deletingChatSessionId)}
                >
                  <span className="line-clamp-2 w-full font-medium leading-snug text-foreground" title={session.title}>{session.title}</span>
                  <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
                    {dayjs(session.updated_at).format('MMM D HH:mm')}
                  </span>
                </button>
                <button
                  className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition hover:text-negative group-hover:opacity-100 disabled:opacity-50"
                  onClick={() => void handleDeleteChatSession(session.id)}
                  disabled={chatSessionBusy || deletingChatSessionId === session.id}
                  title="Delete chat"
                >
                  {deletingChatSessionId === session.id ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <X className="size-3.5" />
                  )}
                </button>
              </div>
            ))
          )}
        </div>
      </div>
    </>
  )

  const statusBits: string[] = []
  if (config) {
    statusBits.push(`${config.broker.broker_mode.toUpperCase()} · ${config.broker.t212_base_env.toUpperCase()}`)
  }
  if (pendingIntents.length) statusBits.push(`${pendingIntents.length} pending`)
  if (lastUpdate) statusBits.push(`updated ${dayjs(lastUpdate).format('HH:mm:ss')}`)
  if (privacyMode !== 'off') statusBits.push(`privacy: ${privacyModeLabel(privacyMode).toLowerCase()}`)

  function renderSection() {
    if (activeSection === 'overview') {
      if (!displaySnapshot) {
        return (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 rounded-xl" />
            ))}
          </div>
        )
      }
      return (
        <div className="space-y-4">
          <AttentionChip onOpen={() => setActiveSection('attention')} />
          <PortfolioOverview
            snapshot={displaySnapshot}
            positions={displayPositions}
            accountView={accountView}
            displayCurrency={displayCurrency}
          />
        </div>
      )
    }

    if (activeSection === 'execution') {
      return (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="min-w-0 lg:col-span-2">
            <IntentQueue intents={queueIntents} onApprove={handleApprove} onReject={handleReject} onExecute={handleExecute} />
          </div>
          <div className="min-w-0 lg:col-span-1">
            <EventsFeed events={events} />
          </div>
        </div>
      )
    }

    if (activeSection === 'research') {
      return (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="min-w-0 space-y-4 lg:col-span-2">
            <ThesisBoard theses={theses} onArchive={handleArchiveThesis} onActivate={handleActivateThesis} />
            <BacktestLab onError={setError} />
          </div>
          <div className="min-w-0 lg:col-span-1">{runHistoryCard}</div>
        </div>
      )
    }

    if (activeSection === 'orders') return <OrdersWorkspace onError={setError} />
    if (activeSection === 'attention') return <AttentionFeed onError={setError} />
    if (activeSection === 'watchlist') return <WatchlistBoard onError={setError} />
    if (activeSection === 'analysis') return <ResearchDesk accountView={accountView} onError={setError} />
    if (activeSection === 'leveraged') return <LeveragedWorkspace onError={setError} />
    if (activeSection === 'jobs') return <ScheduledJobsWorkspace onError={setError} />
    if (activeSection === 'artifacts') return <ArtifactsWorkspace onError={setError} />
    if (activeSection === 'costs') return <CostsWorkspace onError={setError} />
    if (activeSection === 'diagnostics') return <RuntimeDiagnosticsPanel onError={setError} />
    if (activeSection === 'help') return <HelpGuide />
    return null
  }

  return (
    <PrivacyProvider mode={privacyMode}>
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <AppSidebar
        active={activeSection}
        onSelect={setActiveSection}
        onOpenSettings={() => setSettingsOpen(true)}
        pendingIntents={pendingIntents.length}
        privacyMode={privacyMode}
        onCyclePrivacyMode={() => setPrivacyMode((prev) => nextPrivacyMode(prev))}
        collapsed={navCollapsed}
        onToggleCollapsed={toggleNavCollapsed}
      />

      <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
        <SheetContent
          side="left"
          showCloseButton={false}
          className="flex w-72 max-w-[85vw] flex-col gap-6 border-r border-border bg-sidebar px-3 py-5"
        >
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SidebarBody
            active={activeSection}
            onSelect={setActiveSection}
            onOpenSettings={() => setSettingsOpen(true)}
            onNavigate={() => setSidebarOpen(false)}
            pendingIntents={pendingIntents.length}
            privacyMode={privacyMode}
            onCyclePrivacyMode={() => setPrivacyMode((prev) => nextPrivacyMode(prev))}
          />
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className={cn('flex items-center justify-between gap-3 border-b border-border bg-background/80 px-4 py-3 backdrop-blur-sm sm:px-6', activeSection === 'chat' && 'md:hidden')}>
          <div className="flex min-w-0 items-center gap-2.5">
            <Button
              variant="ghost"
              size="icon"
              className="size-9 shrink-0 md:hidden"
              aria-label="Open navigation"
              onClick={() => setSidebarOpen(true)}
            >
              <Menu className="size-5" />
            </Button>
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold tracking-tight sm:text-lg">{SECTION_LABELS[activeSection]}</h1>
              <p className="truncate text-xs text-muted-foreground">
                {SECTION_DESCRIPTIONS[activeSection] ?? statusBits.join(' · ')}
              </p>
            </div>
          </div>
          {activeSection === 'overview' ? (
          <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
            <Select
              value={accountView}
              onValueChange={(v) => {
                const next = v as 'all' | 'invest' | 'stocks_isa'
                setAccountView(next)
                void loadAll(false, next, displayCurrency)
              }}
            >
              <SelectTrigger size="sm" className="w-[96px] sm:w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Accounts</SelectItem>
                <SelectItem value="invest">Invest</SelectItem>
                <SelectItem value="stocks_isa">Stocks ISA</SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={displayCurrency}
              onValueChange={(v) => {
                const next = v as 'GBP' | 'USD'
                setDisplayCurrency(next)
                void loadAll(false, accountView, next)
              }}
            >
              <SelectTrigger size="sm" className="hidden w-[80px] sm:flex">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="GBP">GBP</SelectItem>
                <SelectItem value="USD">USD</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="icon"
              onClick={() => void loadAll(true, accountView, displayCurrency, true)}
              disabled={busy}
              aria-label="Refresh"
              title="Refresh live data"
            >
              <RefreshCw className={cn('size-4', busy && 'animate-spin')} />
            </Button>
          </div>
          ) : null}
        </header>

        <main className="min-h-0 flex-1 overflow-hidden">
          {/* Chat stays MOUNTED across navigation (just hidden) so an in-flight
              Archie stream keeps rendering and isn't lost when you switch tabs. */}
          <div className={cn('h-full min-h-0', activeSection === 'chat' ? 'flex' : 'hidden')}>
            <div className="hidden w-64 shrink-0 flex-col border-r border-border bg-card/30 md:flex">
              {chatRail}
            </div>
            <Sheet open={chatRailOpen} onOpenChange={setChatRailOpen}>
              <SheetContent
                side="left"
                showCloseButton={false}
                className="flex w-72 max-w-[85vw] flex-col border-r border-border bg-card p-0"
              >
                <SheetTitle className="sr-only">Conversations</SheetTitle>
                {chatRail}
              </SheetContent>
            </Sheet>
            <div className="min-w-0 flex-1">
              <AgentChatPanel
                activeSessionId={activeChatSessionId}
                activeSessionTitle={activeChatSession?.title || null}
                accountView={accountView}
                displayCurrency={displayCurrency}
                presentationMask={privacyMode !== 'off'}
                onSessionTouched={handleChatSessionTouched}
                onError={setError}
                deletingSessionId={deletingChatSessionId}
                onOpenSessions={() => setChatRailOpen(true)}
              />
            </div>
          </div>

          {activeSection !== 'chat' ? (
            <ScrollArea className="h-full">
              <div className="mx-auto w-full max-w-[1440px] space-y-6 p-4 sm:p-6">
                {error ? (
                  <Alert variant="destructive">
                    <AlertDescription>{error}</AlertDescription>
                  </Alert>
                ) : null}
                {renderSection()}
              </div>
            </ScrollArea>
          ) : null}
        </main>
      </div>

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="max-h-[88vh] w-[calc(100vw-2rem)] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>Control Tower</DialogTitle>
          </DialogHeader>
          <SettingsPanel
            config={config}
            onReload={() => void loadAll(false)}
            onError={(msg) => setError(msg)}
            hideHeader
            privacyMode={privacyMode}
            onPrivacyModeChange={setPrivacyMode}
          />
        </DialogContent>
      </Dialog>
    </div>
    </PrivacyProvider>
  )
}
