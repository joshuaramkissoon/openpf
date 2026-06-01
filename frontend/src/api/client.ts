import axios from 'axios'
import type {
  AgentRun,
  AgentRunDetail,
  AppConfig,
  ArtifactDetail,
  ArtifactItem,
  BacktestResult,
  ExecutionEvent,
  PortfolioSnapshot,
  ChatMessage,
  ChatSession,
  LeveragedConfig,
  LeveragedSnapshot,
  SchedulerTask,
  SchedulerTaskLog,
  ChatRuntimeInfo,
  Thesis,
  TradeIntent,
} from '../types'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api',
  timeout: 30000,
})

export async function getConfig() {
  const { data } = await api.get<AppConfig>('/config')
  return data
}

export async function updateRisk(payload: AppConfig['risk']) {
  const { data } = await api.put('/config/risk', payload)
  return data
}

export async function updateBroker(payload: AppConfig['broker']) {
  const { data } = await api.put('/config/broker', payload)
  return data
}

export async function setLeveragedAutoExecute(enabled: boolean) {
  const { data } = await api.put('/config/leveraged/auto-execute', null, { params: { enabled } })
  return data
}

export async function updateWatchlist(symbols: string[]) {
  const { data } = await api.put('/config/watchlist', { symbols })
  return data
}

export async function updateTelegram(payload: {
  enabled: boolean
  poll_enabled: boolean
  chat_id: string
  bot_token?: string | null
  high_conviction_threshold: number
  notify_general_updates: boolean
  allowed_user_ids: number[]
}) {
  const { data } = await api.put('/config/telegram', payload)
  return data
}

export async function updateLeveragedPolicy(payload: LeveragedConfig) {
  const { data } = await api.put<LeveragedConfig>('/config/leveraged', payload)
  return data
}

export async function updateAccountCredentials(
  account_kind: 'invest' | 'stocks_isa',
  payload: { t212_api_key: string; t212_api_secret: string; enabled: boolean }
) {
  const { data } = await api.put(`/config/credentials/${account_kind}`, payload)
  return data
}

export async function refreshPortfolio() {
  const { data } = await api.post('/portfolio/refresh')
  return data
}

export async function getSnapshot(
  account_kind: 'all' | 'invest' | 'stocks_isa' = 'all',
  display_currency: 'GBP' | 'USD' = 'GBP'
) {
  const { data } = await api.get<PortfolioSnapshot>('/portfolio/snapshot', { params: { account_kind, display_currency } })
  return data
}

export async function runAgent(include_watchlist = true, execute_auto = false) {
  const { data } = await api.post<AgentRunDetail>('/agent/run', { include_watchlist, execute_auto })
  return data
}

export async function getRuns() {
  const { data } = await api.get<AgentRun[]>('/agent/runs')
  return data
}

export async function getRun(id: string) {
  const { data } = await api.get<AgentRunDetail>(`/agent/runs/${id}`)
  return data
}

export async function getIntents() {
  const { data } = await api.get<TradeIntent[]>('/agent/intents')
  return data
}

export async function approveIntent(id: string, note?: string) {
  const { data } = await api.post(`/agent/intents/${id}/approve`, { note })
  return data
}

export async function rejectIntent(id: string, note?: string) {
  const { data } = await api.post(`/agent/intents/${id}/reject`, { note })
  return data
}

export async function executeIntent(id: string, forceLive = false) {
  const { data } = await api.post(`/agent/intents/${id}/execute`, { force_live: forceLive })
  return data
}

export async function getEvents() {
  const { data } = await api.get<ExecutionEvent[]>('/agent/events')
  return data
}

export async function listArtifacts() {
  const { data } = await api.get<ArtifactItem[]>('/agent/artifacts')
  return data
}

export async function getArtifact(path: string) {
  // Encode each segment individually to preserve path separators for FastAPI's {path:path}
  const safePath = path.split('/').map(encodeURIComponent).join('/')
  const { data } = await api.get<ArtifactDetail>('/agent/artifacts/' + safePath)
  return data
}

export async function testTelegram(message: string) {
  const { data } = await api.post('/telegram/test', { message })
  return data
}

export async function getTheses(limit = 100) {
  const { data } = await api.get<Thesis[]>('/theses', { params: { limit } })
  return data
}

export async function archiveThesis(id: string) {
  const { data } = await api.delete(`/theses/${id}`)
  return data
}

export async function updateThesisStatus(id: string, status: 'active' | 'archived') {
  const { data } = await api.patch<Thesis>(`/theses/${id}/status`, { status })
  return data
}

export async function getChatSessions() {
  const { data } = await api.get<ChatSession[]>('/agent/chat/sessions')
  return data
}

export async function createChatSession(title = 'Portfolio Chat') {
  const { data } = await api.post<ChatSession>('/agent/chat/sessions', { title })
  return data
}

export async function getChatMessages(sessionId: string) {
  const { data } = await api.get<ChatMessage[]>(`/agent/chat/sessions/${sessionId}/messages`)
  return data
}

export async function getChatRuntime() {
  const { data } = await api.get<ChatRuntimeInfo>('/agent/chat/runtime')
  return data
}

export async function getMcpHealth() {
  const { data } = await api.get<Record<string, { status: string; detail: string }>>('/agent/chat/runtime/mcp-health')
  return data
}

export async function deleteChatSession(sessionId: string) {
  const { data } = await api.delete<{ id: string; deleted: boolean }>(`/agent/chat/sessions/${sessionId}`)
  return data
}

export async function stopChat(sessionId: string) {
  const { data } = await api.post<{ id: string; interrupted: boolean }>(
    `/agent/chat/sessions/${sessionId}/stop`
  )
  return data
}

export async function sendChatMessage(
  sessionId: string,
  payload: {
    content: string
    account_kind: 'all' | 'invest' | 'stocks_isa'
    display_currency: 'GBP' | 'USD'
    redact_values?: boolean
  }
) {
  const { data } = await api.post<{
    session: ChatSession
    user_message: ChatMessage
    assistant_message: ChatMessage
  }>(`/agent/chat/sessions/${sessionId}/messages`, payload)
  return data
}

export type ChatStreamHandlers = {
  onAck?: (payload: { session: ChatSession; user_message: ChatMessage }) => void
  onStatus?: (payload: { phase: string; message: string; toolInput?: Record<string, unknown> }) => void
  onDelta?: (payload: { delta: string }) => void
  onDone?: (payload: {
    session: ChatSession
    assistant_message: ChatMessage
    stop_reason?: string | null
    result_subtype?: string | null
  }) => void
  onError?: (message: string) => void
}

function websocketBaseUrl() {
  const raw = String(api.defaults.baseURL || '/api')
  const base = new URL(raw, window.location.origin)
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
  return base.toString().replace(/\/$/, '')
}

export type StreamHandle = {
  done: Promise<void>
  abort: () => void
}

export function streamChatMessage(
  sessionId: string,
  payload: {
    content: string
    account_kind: 'all' | 'invest' | 'stocks_isa'
    display_currency: 'GBP' | 'USD'
    redact_values?: boolean
  },
  handlers: ChatStreamHandlers = {}
): StreamHandle {
  let ws: WebSocket | null = null
  let settled = false

  const done = new Promise<void>((resolve, reject) => {
    const url = `${websocketBaseUrl()}/agent/chat/sessions/${encodeURIComponent(sessionId)}/stream`
    ws = new WebSocket(url)

    const fail = (message: string) => {
      if (settled) return
      settled = true
      handlers.onError?.(message)
      try {
        ws?.close()
      } catch {
        // no-op
      }
      reject(new Error(message))
    }

    ws.onopen = () => {
      ws!.send(JSON.stringify(payload))
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(String(event.data || '{}')) as Record<string, unknown>
        const type = String(data.type || '')
        if (type === 'ack') {
          handlers.onAck?.({
            session: data.session as ChatSession,
            user_message: data.user_message as ChatMessage,
          })
          return
        }
        if (type === 'status') {
          handlers.onStatus?.({
            phase: String(data.phase || ''),
            message: String(data.message || ''),
            toolInput: data.tool_input as Record<string, unknown> | undefined,
          })
          return
        }
        if (type === 'delta') {
          handlers.onDelta?.({ delta: String(data.delta || '') })
          return
        }
        if (type === 'done') {
          handlers.onDone?.({
            session: data.session as ChatSession,
            assistant_message: data.assistant_message as ChatMessage,
            stop_reason: (data.stop_reason as string) ?? null,
            result_subtype: (data.result_subtype as string) ?? null,
          })
          settled = true
          try {
            ws?.close()
          } catch {
            // no-op
          }
          resolve()
          return
        }
        if (type === 'error') {
          fail(String(data.error || 'Chat stream failed'))
          return
        }
      } catch {
        fail('Invalid chat stream payload')
      }
    }

    ws.onerror = () => {
      fail('Chat websocket connection failed')
    }

    ws.onclose = () => {
      if (!settled) {
        fail('Chat websocket closed before completion')
      }
    }
  })

  const abort = () => {
    if (settled) return
    settled = true
    try {
      ws?.close()
    } catch {
      // no-op
    }
  }

  return { done, abort }
}

export async function runBacktest(payload: {
  symbol: string
  lookback_days: number
  fast_window: number
  slow_window: number
}) {
  const { data } = await api.post<BacktestResult>('/strategy/backtest', payload)
  return data
}

export async function getLeveragedSnapshot() {
  const { data } = await api.get<LeveragedSnapshot>('/leveraged/snapshot')
  return data
}

export async function patchLeveragedPolicy(payload: Partial<LeveragedConfig>) {
  const { data } = await api.patch<LeveragedConfig>('/leveraged/policy', payload)
  return data
}

export async function runLeveragedScan() {
  const { data } = await api.post('/leveraged/scan')
  return data
}

export async function runLeveragedCycle() {
  const { data } = await api.post('/leveraged/cycle')
  return data
}

export async function executeLeveragedSignal(signalId: string) {
  const { data } = await api.post(`/leveraged/signals/${signalId}/execute`)
  return data
}

export async function closeLeveragedTrade(tradeId: string, reason = 'manual') {
  const { data } = await api.post(`/leveraged/trades/${tradeId}/close`, { reason })
  return data
}

export async function refreshInstrumentCache() {
  const { data } = await api.post('/leveraged/cache/instruments')
  return data
}

export interface RegimeState {
  regime: 'risk_on' | 'risk_off' | 'neutral'
  label: string
  score: number
  long_bias: number
  inverse_bias: number
  vix: number | null
  vix_state: string
  breadth: number | null
  rationale: string
  stale: boolean
  as_of: string
}

export interface UniverseRow {
  underlying: string
  underlying_name: string | null
  is_stock?: boolean
  direction: 'long' | 'inverse'
  etp_ticker: string
  etp_name: string
  factor: number | null
  currency: string | null
  /** Signed % distance of the underlying from its 50-day average (the real move). */
  move_pct?: number
  move_score: number
  trend: string
  regime_aligned: boolean
}

export interface UniverseResponse {
  regime: RegimeState
  ranked: UniverseRow[]
  evaluated: number
  available_underlyings: number
  degraded?: boolean
  error_reason?: string | null
  errors: string[]
}

export interface RebalanceTrade {
  account_kind: string
  ticker: string
  instrument_code: string
  name: string | null
  side: 'sell' | 'buy'
  quantity: number
  est_notional: number
  current_weight: number | null
  target_weight: number | null
  reason: string
}

export interface RebalancePlan {
  account_kind: string
  objective: string
  total_value: number
  trades: RebalanceTrade[]
  turnover: number
  before: { concentration_hhi: number; top_position_weight: number; names: number }
  after: { concentration_hhi: number; top_position_weight: number; names: number }
  rationale: string
  proposed_count?: number
}

export async function getRebalancePreview(accountKind = 'all') {
  const { data } = await api.get<RebalancePlan>('/portfolio/rebalance', { params: { account_kind: accountKind } })
  return data
}

export async function proposeRebalance(accountKind = 'all') {
  const { data } = await api.post<RebalancePlan>('/portfolio/rebalance/propose', null, { params: { account_kind: accountKind } })
  return data
}

export async function getLeveragedRegime() {
  const { data } = await api.get<RegimeState>('/leveraged/regime')
  return data
}

export async function getLeveragedUniverse(topN = 8) {
  const { data } = await api.get<UniverseResponse>('/leveraged/universe', { params: { top_n: topN } })
  return data
}

export async function getSchedulerTasks() {
  const { data } = await api.get<SchedulerTask[]>('/scheduler/tasks')
  return data
}

export async function createSchedulerTask(payload: {
  name: string
  cron_expr: string
  timezone: string
  model: string
  prompt: string
  enabled: boolean
  meta?: Record<string, unknown>
}) {
  const { data } = await api.post<SchedulerTask>('/scheduler/tasks', payload)
  return data
}

export async function updateSchedulerTask(taskId: string, payload: Partial<SchedulerTask>) {
  const { data } = await api.patch<SchedulerTask>(`/scheduler/tasks/${taskId}`, payload)
  return data
}

export async function deleteSchedulerTask(taskId: string) {
  const { data } = await api.delete<{ id: string; deleted: boolean }>(`/scheduler/tasks/${taskId}`)
  return data
}

export async function runSchedulerTask(taskId: string) {
  const { data } = await api.post(`/scheduler/tasks/${taskId}/run`)
  return data
}

export async function getSchedulerTaskLogs(taskId: string, limit = 40) {
  const { data } = await api.get<SchedulerTaskLog[]>(`/scheduler/tasks/${taskId}/logs`, { params: { limit } })
  return data
}

export async function seedSchedulerDefaults() {
  const { data } = await api.post<{ message: string }>('/scheduler/seed-defaults')
  return data
}

export type ApiError = {
  response?: {
    data?: {
      detail?: string
    }
  }
}

export interface ResearchRunRequest {
  objective: string
  subject?: string
  hypothesis?: string
  horizon_days?: number
  account_kind?: string
  create_thesis?: boolean
}

export interface ResearchRunResult {
  ok: boolean
  markdown: string
  verdict: 'support' | 'refute' | 'mixed' | string | null
  confidence: number | null
  summary: string | null
  suggested_action: string | null
  invalidation: string | null
  artifact_path: string | null
  thesis_id: string | null
  error?: string | null
}

export async function runResearch(payload: ResearchRunRequest) {
  // Analyst runs orchestrate subagents + tools + Kronos; allow several minutes.
  const { data } = await api.post<ResearchRunResult>('/research/run', payload, { timeout: 280000 })
  return data
}
