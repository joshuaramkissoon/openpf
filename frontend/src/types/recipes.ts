export type RecipeTrigger =
  | { kind: 'manual' }
  | { kind: 'schedule'; cron: string; timezone: string }
  | { kind: 'event'; event: 'earnings_week' | 'big_move'; threshold_pct?: number; timezone?: string }

export type RecipeRun = {
  id: number
  at: string
  status: 'ok' | 'error' | 'running' | string
  message: string
  summary: string
  output_path: string | null
  version: number | null
  trigger_context: string
  telegram: boolean
  /** The run's report summary (headline, stats…), when it has one. */
  card?: import('./index').ReportSummary | null
  /** Started with Run now. */
  manual?: boolean
}

export type Recipe = {
  slug: string
  skill: string
  title: string
  summary: string
  icon: string
  body: string
  source: 'builtin' | 'user'
  created_by: 'builtin' | 'user' | 'archie'
  category: string
  enabled: boolean
  trigger: RecipeTrigger
  trigger_summary: string
  delivery: { home: boolean; telegram: boolean }
  current_version: number
  created_at: string
  updated_at: string
  next_run_at: string | null
  running: boolean
  run_count: number
  last_run: RecipeRun | null
  pending_suggestions: number
  modified: boolean
}

export type DiffLine = { type: 'add' | 'del' | 'ctx' | 'hunk'; text: string }

export type RecipeVersion = {
  version: number
  created_at: string
  author: 'builtin' | 'user' | 'archie'
  note: string
  title: string
  summary: string
  trigger_summary: string
}

export type RecipeSuggestion = {
  id: string
  slug: string | null
  action: 'create' | 'update' | 'enable' | 'disable'
  title: string
  reason: string
  status: string
  created_at: string
  diff: DiffLine[]
}

export type RecipeDetail = Recipe & {
  runs: RecipeRun[]
  versions: RecipeVersion[]
  suggestions: RecipeSuggestion[]
}

export type RecipeDraftFields = {
  title: string
  summary: string
  icon: string
  body: string
  trigger: RecipeTrigger
  delivery: { home?: boolean; telegram: boolean }
  trigger_summary?: string
}

export type DraftResult =
  | { ok: true; draft: RecipeDraftFields; note: string; warnings: string[]; diff?: DiffLine[] }
  | { ok: false; kind: string; reason: string; alternative: string }
