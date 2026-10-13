export type PlayStatus = 'draft' | 'active' | 'paused' | 'closed'
export type Conviction = 'low' | 'medium' | 'high'
export type PlayAccount = 'invest' | 'stocks_isa' | 'crypto'
export type LegRole = 'core' | 'satellite' | 'hedge'
export type Verdict = 'on_track' | 'watch' | 'broken'
export type KillState = 'intact' | 'near' | 'tripped' | 'unknown' | 'manual'

export interface PlayLeg {
  ticker: string
  instrument_code?: string | null
  instrument_verified?: boolean
  name: string
  role: LegRole
  target_weight: number
  rationale: string
  currency?: string | null
}

export interface KillRule {
  type: 'price_below' | 'drawdown_pct' | 'weight_above' | 'date_after'
  ticker?: string | null
  value: number | string
}

export interface KillCriterion {
  text: string
  rule?: KillRule | null
}

export interface Tranche {
  label: string
  pct_of_budget: number
  trigger: string
}

export interface StagedTranche {
  index: number
  staged_at: string
  intent_ids: string[]
  notional_gbp: number
}

export interface LegAllocation {
  ticker: string
  target_gbp: number
  held_gbp: number
  new_gbp: number
  weight: number
  pct_of_budget: number
}

export interface PlaySizing {
  budget_gbp?: number
  budget_pct_of_book?: number
  suggested_gbp?: number
  target_pct_of_book?: number
  base_pct?: number
  vol_factor?: number
  basket_vol?: number | null
  overlap_gbp?: number
  max_play_pct?: number
  max_loss_gbp?: number
  tranches?: Tranche[]
  allocations?: LegAllocation[]
  rationale?: string
  steps?: string[]
  warnings?: string[]
  budget_override?: boolean
  staged?: StagedTranche[]
}

export interface ReviewEntry {
  at: string
  verdict: Verdict
  notes: string
  source: 'monitor' | 'archie'
}

export interface PlayReview {
  last_reviewed_at?: string
  verdict?: Verdict
  notes?: string
  history?: ReviewEntry[]
}

export interface OverlapRow {
  ticker: string
  value_gbp: number
  weight_of_book: number
  accounts: string[]
}

export interface Play {
  id: string
  created_at: string
  updated_at: string
  activated_at: string | null
  title: string
  thesis: string
  status: PlayStatus
  conviction: Conviction
  horizon_months: number
  account_kind: PlayAccount
  budget_gbp: number
  budget_pct_of_book: number
  legs: PlayLeg[]
  sizing: PlaySizing
  kill_criteria: KillCriterion[]
  what_would_change_my_mind: string
  review: PlayReview
  related?: { ticker: string; why: string }[]
  bench?: { ticker: string; name?: string; why: string; instrument_verified?: boolean }[]
  source_session_id: string | null
  meta: {
    idea?: string
    benchmark?: string
    key_risks?: string[]
    overlap?: OverlapRow[]
    conviction_source?: 'user' | 'archie'
    origin?: 'adopted'
    dropped_legs?: string[]
    /** Legs the draft couldn't keep, with why (not on Trading 212 / you excluded it). */
    dropped?: { ticker: string; name?: string; reason: string }[]
    /** Archie's conviction when it differs from the one the user picked. */
    suggested_conviction?: Conviction
    /** Must-have names the draft couldn't keep, with Archie's reason. */
    left_out?: { ticker: string; reason: string }[]
    include?: { query: string; symbol: string; name: string | null; tradable: boolean | null }[]
  }
}

export interface LegPerformance extends PlayLeg {
  value_gbp: number
  ppl_gbp: number
  weight_of_book: number
  actual_weight: number
  drift: number
}

export interface PlayPerformance {
  play_id: string
  held_gbp: number
  target_exposure_gbp: number
  basket_built: boolean
  budget_gbp: number
  exposure_pct_of_budget: number | null
  legs: LegPerformance[]
  benchmark: string
  since: string
  points: { date: string; basket: number; benchmark: number | null }[]
  basket_return: number | null
  benchmark_return: number | null
  drawdown_pct: number | null
  max_drawdown_pct?: number
  tranches_total: number
  tranches_staged: number
  staged: StagedTranche[]
  kill_status: (KillCriterion & { state: KillState; detail: string })[]
}

export type DraftPhase = 'portfolio' | 'research' | 'prices' | 'fundamentals' | 'compare' | 'sizing' | 'done'

/** One drafting progress event. Newer jobs tag each with its phase, data source and tickers. */
export interface DraftProgress {
  at: string
  text: string
  phase?: DraftPhase
  source?: 'portfolio' | 'web' | 'market' | 'fundamentals' | 'quant'
  tickers?: string[]
  basket?: { ticker: string; name?: string; weight?: number; role?: LegRole }[]
}

export interface DraftJob {
  job_id: string
  status: 'running' | 'done' | 'error'
  progress: DraftProgress[]
  play_id: string | null
  error: string | null
  error_kind?: 'unclear_idea' | 'no_instruments' | 'interrupted' | 'model' | 'failed' | null
  retryable?: boolean
  idea?: string | null
  request?: DraftRequest
  started_at: string
  finished_at: string | null
}

// ── The book by play ─────────────────────────────────────────────────────────

export type OwnerKind = 'play' | 'core' | 'unsorted'

export interface HoldingOwner {
  kind: OwnerKind
  play_id: string | null
  title: string
  status: PlayStatus | null
  /** user | archie | suggestion | system (explicit) · basket | default (derived) */
  source: string
}

export interface BookHolding {
  key: string
  account_kind: PlayAccount
  ticker: string
  instrument_code: string | null
  name: string
  value_gbp: number
  ppl_gbp: number
  weight: number
  owner: HoldingOwner
  related_to: { play_id: string; title: string; why: string }[]
}

export interface OwnedHolding extends BookHolding {
  is_leg?: boolean
  target_weight?: number | null
  share_of_play?: number
  why?: string
}

export interface RelatedHolding {
  key: string
  ticker: string
  account_kind: PlayAccount
  name: string
  value_gbp: number
  weight: number
  owner: HoldingOwner
  why: string
  source: 'basket' | 'user'
}

export interface BenchItem {
  ticker: string
  name: string
  why: string
  in_plan: boolean
  target_weight: number | null
  instrument_verified?: boolean
}

export type NeedKind = 'kill_tripped' | 'kill_near' | 'tranche_due' | 'drift_over' | 'drift_under' | 'empty' | 'awaiting' | 'draft'

export interface Need {
  kind: NeedKind
  tone: 'critical' | 'warn' | 'info'
  text: string
}

export interface BookGroup {
  kind: OwnerKind
  id: string
  play_id: string | null
  title: string
  status: PlayStatus | null
  conviction?: Conviction
  account_kind?: PlayAccount
  color_index?: number
  value_gbp: number
  ppl_gbp: number
  ppl_pct: number | null
  weight: number
  holdings_count: number
  target_pct?: number
  effective_value_gbp: number | null
  effective_weight: number | null
  tranches_total?: number
  tranches_staged?: number
  next_tranche?: { index: number; label: string; due: string | null; is_due: boolean } | null
  verdict?: Verdict | null
  needs: Need[]
  owned: OwnedHolding[]
  related: RelatedHolding[]
  bench: BenchItem[]
}

export interface ThemeRisk {
  play_id: string
  title: string
  weight: number
  effective_weight: number | null
  tone: 'warn' | 'neutral'
  text: string
}

export interface Book {
  as_of: string
  total_gbp: number
  cash_gbp: number
  cash_weight: number
  groups: BookGroup[]
  holdings: BookHolding[]
  theme_risk: ThemeRisk[]
  unsorted_count: number
  unsorted_weight: number
}

export interface Suggestion {
  id: string
  kind: 'play' | 'core' | 'existing'
  play_id: string | null
  title: string
  tickers: string[]
  why: string
  thesis: string
  value_gbp: number
  weight: number
  holdings: { ticker: string; name: string; value_gbp: number; weight: number }[]
}

export interface Suggestions {
  generated_at: string | null
  source: 'archie' | 'rules' | 'none' | null
  /** Why the theme rules stood in for Archie (model failed or took too long). */
  refine_error?: string | null
  items: Suggestion[]
  unsorted: { ticker: string; name: string; value_gbp: number; weight: number; accounts: string[] }[]
  unsorted_weight: number
}

export interface DraftRequest {
  idea: string
  budget_gbp?: number | null
  conviction?: Conviction | null
  horizon_months?: number | null
  account_kind?: PlayAccount | null
  /** Must-have names (market symbols or company names). */
  include?: string[]
  exclude?: string[]
}

export interface StageResult {
  play_id: string
  tranche_index: number
  tranche: Tranche
  notional_gbp: number
  intents: { intent_id: string; ticker: string; quantity: number; notional_gbp: number; warnings: string[] }[]
  skipped: { ticker: string; reason: string }[]
  rail_breaches: { ticker: string; breaches: string[]; notional_gbp: number }[]
  warnings: string[]
}

export type PlayPatch = Partial<{
  title: string
  thesis: string
  status: PlayStatus
  conviction: Conviction
  horizon_months: number
  account_kind: PlayAccount
  budget_gbp: number
  legs: PlayLeg[]
  kill_criteria: KillCriterion[]
  what_would_change_my_mind: string
  resize: boolean
}>

// ── Rebalance ────────────────────────────────────────────────────────────────

export interface RebalanceLeg {
  ticker: string
  name: string
  value_gbp: number
  after_value_gbp?: number
  current_weight: number
  target_weight: number
  after_weight: number
  drift: number
  action: 'buy' | 'sell' | 'hold'
}

export interface RebalanceTrade {
  ticker: string
  name: string
  side: 'buy' | 'sell'
  account_kind: PlayAccount
  notional_gbp: number
  shares: number | null
  price_gbp: number | null
  sell_all: boolean
  instrument_code: string | null
  currency: string | null
  fee_gbp: number
  note?: string | null
  /** Over the per-order limit: a proposal is held back; placing it yourself is a warning. */
  over_order_limit?: boolean
}

export interface RebalancePlan {
  play_id: string
  account_kind: PlayAccount
  value_gbp: number
  use_cash: boolean
  min_drift: number
  min_trade_gbp: number
  free_cash_gbp: number
  legs: RebalanceLeg[]
  trades: RebalanceTrade[]
  ignored: { ticker: string; drift: number; reason: string }[]
  turnover_gbp: number
  est_fees_gbp: number
  cash_used_gbp: number
  warnings: string[]
  empty_reason?: string
  /** live | paper | demo | sandbox — "Place them myself" needs a live confirm. */
  broker_mode?: string
}

export interface RebalanceDone {
  ticker: string
  side: 'buy' | 'sell'
  account_kind: string
  notional_gbp: number
  intent_id: string
}

export interface RebalanceFailed {
  ticker: string
  side: 'buy' | 'sell'
  reason: string
}
