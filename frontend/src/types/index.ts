export interface AccountSummary {
  fetched_at: string
  account_kind: 'all' | 'invest' | 'stocks_isa' | string
  currency: string
  free_cash: number
  invested: number
  pie_cash: number
  total: number
  ppl: number
}

export interface PositionItem {
  account_kind: 'invest' | 'stocks_isa' | string
  ticker: string
  /** Real current market ticker (T212 shortName) for display; falls back to `ticker`. */
  display_ticker?: string | null
  instrument_code: string
  name?: string | null
  /** yfinance symbol resolved from venue metadata (e.g. NUCGl_EQ → NUCG.L). */
  yfinance_ticker?: string | null
  /** Venue quote currency (USD/GBX/GBP/EUR…); GBX = pence. */
  instrument_currency?: string | null
  quantity: number
  average_price: number
  current_price: number
  total_cost: number
  value: number
  ppl: number
  weight: number
  momentum_63d?: number | null
  rsi_14?: number | null
  trend_score?: number | null
  risk_flag?: string | null
}

export interface PortfolioMetrics {
  total_value: number
  free_cash: number
  cash_ratio: number
  concentration_hhi: number
  top_position_weight: number
  estimated_beta: number
  estimated_volatility: number
  /** Share of the book on the crypto exchange (0 when none is connected). */
  crypto_weight?: number
}

export interface PortfolioSnapshot {
  account: AccountSummary
  accounts: AccountSummary[]
  positions: PositionItem[]
  metrics: PortfolioMetrics
}

export interface RiskConfig {
  max_single_order_notional: number
  max_daily_notional: number
  max_position_weight: number
  duplicate_order_window_seconds: number
  /** Max share of the whole book in crypto (0.05 = 5%). */
  max_crypto_pct_of_book?: number
}

export interface BrokerConfig {
  broker_mode: 'paper' | 'live'
  t212_base_env: 'demo' | 'live'
  scheduler_enabled?: boolean
}

export interface TelegramConfig {
  enabled: boolean
  poll_enabled: boolean
  chat_id: string
  high_conviction_threshold: number
  notify_general_updates: boolean
  allowed_user_ids: number[]
  bot_token_configured: boolean
}

export interface AccountCredentialView {
  account_kind: 'invest' | 'stocks_isa'
  enabled: boolean
  configured: boolean
  exec_enabled: boolean
  exec_configured: boolean
}

export interface AppConfig {
  risk: RiskConfig
  broker: BrokerConfig
  telegram: TelegramConfig
  credentials: {
    invest: AccountCredentialView
    stocks_isa: AccountCredentialView
  }
  leveraged: LeveragedConfig
}

export interface LeveragedConfig {
  enabled: boolean
  account_kind: 'stocks_isa'
  auto_execute_enabled: boolean
  per_position_notional: number
  max_total_exposure: number
  max_open_positions: number
  take_profit_pct: number
  stop_loss_pct: number
  close_time_uk: string
  allow_overnight: boolean
  max_hold_days: number
  scan_symbols: string[]
  instrument_priority: string[]
}

export interface HeldLeveragedPosition {
  instrument_code: string
  symbol: string
  name: string
  account_kind: string
  underlying?: string | null
  direction?: string | null
  factor?: number | null
  quantity: number
  avg_price: number
  current_price?: number | null
  notional: number
  unrealized_pnl_value: number
  unrealized_pnl_pct: number
  tracked: boolean
  trade_id?: string | null
  days_held?: number | null
  stop_loss_pct?: number | null
  take_profit_pct?: number | null
}

export interface LeveragedSignal {
  id: string
  created_at: string
  updated_at: string
  status: string
  symbol: string
  instrument_code: string
  account_kind: string
  direction: string
  entry_side: string
  target_notional: number
  reference_price: number
  stop_loss_pct: number
  take_profit_pct: number
  confidence: number
  expected_edge: number
  rationale: string
  strategy_tag: string
  linked_intent_id?: string | null
  linked_trade_id?: string | null
  source_task_id?: string | null
  meta: Record<string, unknown>
}

export interface LeveragedTrade {
  id: string
  created_at: string
  updated_at: string
  signal_id?: string | null
  status: string
  symbol: string
  instrument_code: string
  account_kind: string
  direction: string
  quantity: number
  entry_price: number
  entry_notional: number
  entered_at: string
  stop_loss_pct: number
  take_profit_pct: number
  entry_intent_id?: string | null
  exit_intent_id?: string | null
  exit_price?: number | null
  exit_notional?: number | null
  exited_at?: string | null
  close_reason?: string | null
  pnl_value: number
  pnl_pct: number
  meta: Record<string, unknown>
  current_price?: number | null
  current_value?: number | null
  current_pnl_value?: number | null
  current_pnl_pct?: number | null
}

export interface LeveragedSummary {
  open_positions: number
  open_exposure: number
  max_total_exposure: number
  open_unrealized_pnl: number
  closed_realized_pnl: number
  win_rate: number
  wins: number
  losses: number
  closed_trades: number
}

export interface SchedulerTaskLog {
  id: number
  task_id: string
  created_at: string
  status: string
  message: string
  output_path?: string | null
  payload: Record<string, unknown>
}

export interface LeveragedSnapshot {
  policy: LeveragedConfig
  summary: LeveragedSummary
  open_trades: LeveragedTrade[]
  closed_trades: LeveragedTrade[]
  signals: LeveragedSignal[]
  recent_task_logs: SchedulerTaskLog[]
}

export interface SchedulerTask {
  id: string
  created_at: string
  updated_at: string
  name: string
  cron_expr: string
  timezone: string
  model: string
  prompt: string
  enabled: boolean
  next_run_at?: string | null
  last_run_at?: string | null
  last_status: string
  run_count: number
  failure_count: number
  meta: Record<string, unknown>
}

export interface TimelineRun {
  log_id: number
  ran_at: string
  status: string
  message: string
  has_output: boolean
  output_path?: string | null
}

export interface TimelinePastGroup {
  task_id: string
  name: string
  task_kind: string
  run_count: number
  first_ran_at: string
  last_ran_at: string
  status_summary: Record<string, number>
  runs: TimelineRun[]
}

export interface TimelineUpcoming {
  task_id: string
  name: string
  task_kind: string
  cron_expr: string
  next_fire_at: string
  remaining_today: number
  fires: string[]
}

export interface SchedulerToday {
  date: string
  timezone: string
  now: string
  past: TimelinePastGroup[]
  upcoming: TimelineUpcoming[]
}

export interface AgentRun {
  id: string
  created_at: string
  market_regime: string
  portfolio_score: number
  status: string
}

export interface AgentRunDetail {
  run_id: string
  created_at: string
  market_regime: string
  portfolio_score: number
  summary_markdown: string
  intents_created: number
  theses_created?: number
}

export interface TradeIntent {
  id: string
  created_at: string
  status: string
  symbol: string
  instrument_code: string
  side: 'buy' | 'sell'
  order_type: string
  quantity: number
  estimated_notional: number
  expected_edge: number
  confidence: number
  risk_score: number
  rationale: string
  broker_mode: string
  account_kind?: string | null
  source?: string | null
  play_id?: string | null
  play_title?: string | null
  /** "For which play?" — 'play' | 'core' | null (not part of any play). */
  owner_kind?: 'play' | 'core' | null
  owner_play_id?: string | null
  owner_label?: string | null
  owner_inferred?: boolean
  approved_at?: string | null
  /** "human", or "permission:<id>" when a standing permission approved it. */
  approved_by?: string | null
  permission_label?: string | null
  category?: string | null
  executed_at?: string | null
  broker_order_id?: string | null
  execution_price?: number | null
  failure_reason?: string | null
  /** Crypto proposals: exchange + cost estimates shown on the card. */
  crypto?: CryptoEstimate | null
}

export interface CryptoEstimate {
  exchange: string
  label: string
  quote: string
  market: string
  price: number
  price_gbp: number
  fee_rate: number
  fee_source: string
  slippage_pct: number
  fee_gbp?: number
  slippage_gbp?: number
  all_in_cost_gbp?: number
  all_in_cost_pct?: number
}

export interface ExecutionEvent {
  created_at: string
  intent_id: string
  level: string
  message: string
  payload: Record<string, unknown>
}

export interface BacktestPoint {
  date: string
  strategy: number
  benchmark: number
}

export interface BacktestResult {
  symbol: string
  lookback_days: number
  fast_window: number
  slow_window: number
  trades: number
  cagr: number
  max_drawdown: number
  sharpe: number
  win_rate: number
  equity_curve: BacktestPoint[]
}

export interface Thesis {
  id: string
  created_at: string
  updated_at: string
  source_run_id?: string | null
  symbol: string
  account_kind: string
  title: string
  thesis: string
  catalysts: string[]
  invalidation: string
  confidence: number
  status: string
  meta: Record<string, unknown>
}

export interface WatchlistFlag {
  id: string
  title: string
  detail: string
  severity: 'info' | 'warning' | 'critical' | string
  source: string
  created_at: string | null
  url: string | null
}

export interface WatchlistItem {
  id: string
  created_at: string | null
  updated_at: string | null
  symbol: string
  name: string
  note: string
  source: 'manual' | 'archie' | 'agent_run' | 'watchlist_review' | string
  conviction: 'low' | 'medium' | 'high' | null
  status: 'watching' | 'acted' | 'archived' | string
  target_price: number | null
  target_direction: 'above' | 'below' | null
  monitor: boolean
  last_reviewed_at: string | null
  // Live enrichment (request-time; null on failure).
  price: number | null
  change_pct: number | null
  currency: string | null
  /** ~3 months of daily closes, oldest first (empty when unavailable). */
  spark?: number[]
  // Board "stays alive" signals.
  open_flags: number
  latest_flag: string | null
  latest_severity: 'info' | 'warning' | 'critical' | null
  flags: WatchlistFlag[]
}

export interface ChatSession {
  id: string
  created_at: string
  updated_at: string
  title: string
}

export interface RegularToolCallEntry {
  phase: 'tool_start' | 'tool_result' | string
  message: string
  tool_input?: Record<string, unknown>
}

export interface SubagentToolCallEntry {
  phase: 'subagent_start'
  message: string
  subagent_type: string
  subagent_id: string
  nested_calls: Array<{ phase: 'tool_start' | 'tool_result'; message: string; tool_input?: Record<string, unknown> }>
}

export type ToolCallEntry = RegularToolCallEntry | SubagentToolCallEntry

/** AskUserQuestion (clarifying questions) payload streamed from the backend. */
export interface ChatQuestionOption {
  label: string
  description?: string
}

export interface ChatQuestionSpec {
  question: string
  header?: string
  options: ChatQuestionOption[]
  multiSelect?: boolean
}

export interface ChatQuestionPrompt {
  questionId: string
  questions: ChatQuestionSpec[]
}

/** Answers keyed by question text: a single label, a list of labels (multi-select), or free text. */
export type ChatQuestionAnswers = Record<string, string | string[]>

export interface ChatMessage {
  id: number
  session_id: string
  created_at: string
  role: 'user' | 'assistant' | string
  content: string
  tool_calls?: ToolCallEntry[] | null
}

export interface ChatRuntimeInfo {
  project_root: string
  cwd: string
  setting_sources: string[]
  skills_dir: string
  skill_files: string[]
  claude_model: string
  claude_memory_model: string
  memory_file: string
  memory_source_file?: string | null
  memory_strategy?: string | null
  mcp_servers: string[]
  allowed_tools: string[]
  permission_mode?: string | null
  runtime: string
}

export interface ArtifactItem {
  path: string
  title: string
  type: string
  created_at: string
  task_name?: string
  tags?: string[]
  size_bytes: number
  /** The report's ```summary block (or a derived fallback for older reports). */
  summary?: ReportSummary | null
}

/** What Home shows for a report. `signal` is null for derived (pre-summary) reports. */
export interface ReportSummary {
  headline: string
  points: string[]
  /** Markdown: a `[label](proposal:id)` / `[label](#/plays/id)` link, or plain text. */
  action: string | null
  signal: 'none' | 'low' | 'high' | null
  derived?: boolean
  /** Up to 4 headline numbers, drawn as stat tiles on the result card. */
  stats?: ReportStat[]
  /** One small viz spec (components/viz) for a picture that helps. */
  viz?: unknown
  /** Per-name cards (earnings prep, plays, watchlist). */
  holdings?: ReportHolding[]
  /** Short names of data that wasn't available ("timing", "surprise history"). */
  gaps?: string[]
}

export type ReportStatKind = 'money' | 'pct' | 'signedPct' | 'date' | 'count' | 'text'

export interface ReportStat {
  label: string
  /** Number for money/pct/signedPct/count (pct as a fraction), YYYY-MM-DD for date, text otherwise. */
  value: number | string
  kind: ReportStatKind
  tone?: 'positive' | 'negative' | 'neutral'
  /** A short second line (markdown-lite: tickers chip, £ figures blur in privacy mode). */
  sub?: string
  tickers?: string[]
  spark?: number[]
  bar?: { value: number; max: number }
  timeline?: { date: string; label: string }[]
}

export interface ReportHolding {
  ticker: string
  name?: string
  date?: string
  timing?: string
  verdict?: 'hold' | 'trim' | 'opportunity'
  value?: number
  weight?: number
  gain_pct?: number
  forward_pe?: number
  usual_move?: number
  watch?: string[]
  note?: string
  /** Parsed from a markdown section (older reports): the raw "when" text. */
  when?: string
  /** Parsed from a markdown section: bullets that didn't map to a field. */
  extra?: string[]
}

export interface ArtifactDetail {
  path: string
  content: string
  metadata: Record<string, any>
}

export interface UsageRecord {
  id: number
  recorded_at: string
  source: 'chat' | 'scheduled' | 'agent_run' | string
  source_id: string
  model: string
  total_cost_usd: number | null
  duration_ms: number | null
  num_turns: number | null
}

export interface CostBySource {
  chat: number
  scheduled: number
  agent_run: number
  other?: number
}

export interface CostSummary {
  all_time_usd: number
  this_month_usd: number
  this_week_usd: number
  by_source: CostBySource
  record_count: number
}
