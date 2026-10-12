export type PlayStatus = 'draft' | 'active' | 'paused' | 'closed'
export type Conviction = 'low' | 'medium' | 'high'
export type PlayAccount = 'invest' | 'stocks_isa'
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
  source_session_id: string | null
  meta: {
    idea?: string
    benchmark?: string
    key_risks?: string[]
    overlap?: OverlapRow[]
    conviction_source?: 'user' | 'archie'
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

export interface DraftJob {
  job_id: string
  status: 'running' | 'done' | 'error'
  progress: { at: string; text: string }[]
  play_id: string | null
  error: string | null
  started_at: string
  finished_at: string | null
}

export interface DraftRequest {
  idea: string
  budget_gbp?: number | null
  conviction?: Conviction | null
  horizon_months?: number | null
  account_kind?: PlayAccount | null
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
