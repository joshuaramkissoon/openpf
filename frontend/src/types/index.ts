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
  instrument_code: string
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
}

export interface BrokerConfig {
  broker_mode: 'paper' | 'live'
  autopilot_enabled: boolean
  t212_base_env: 'demo' | 'live'
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

export interface AppConfig {
  risk: RiskConfig
  broker: BrokerConfig
  watchlist: string[]
  telegram: TelegramConfig
  credentials: {
    invest: { account_kind: 'invest'; enabled: boolean; configured: boolean }
    stocks_isa: { account_kind: 'stocks_isa'; enabled: boolean; configured: boolean }
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
  scan_symbols: string[]
  instrument_priority: string[]
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
  approved_at?: string | null
  executed_at?: string | null
  broker_order_id?: string | null
  execution_price?: number | null
  failure_reason?: string | null
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

export interface ChatSession {
  id: string
  created_at: string
  updated_at: string
  title: string
}

export interface ToolCallEntry {
  phase: 'tool_start' | 'tool_result' | string
  message: string
  tool_input?: Record<string, unknown>
}

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
}

export interface ArtifactDetail {
  path: string
  content: string
  metadata: Record<string, any>
}
