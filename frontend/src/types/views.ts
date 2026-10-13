export type ViewVersion = { version: number; created_at: string; note: string; bytes: number }
export type ViewHistoryItem = { kind: 'create' | 'edit' | 'fix'; prompt: string; at: string }

export type ViewMeta = {
  id: string
  title: string
  description: string
  icon: string
  pinned: boolean
  order: number
  created_at: string | null
  updated_at: string | null
  current_version: number
  versions: ViewVersion[]
  history: ViewHistoryItem[]
  starter: string | null
  kind: 'spec' | 'code'
  last_good_version?: number | null
  /** Last build outcome (persisted, survives restarts). */
  build?: { status: 'queued' | 'running' | 'done' | 'error'; error?: string | null; prompt?: string; suggestions?: ViewSuggestion[]; at?: string } | null
  active_job?: ViewJob | null
}

export type ViewSuggestion = { slug: string; title: string; why?: string }

export type ViewJob = {
  id: string
  view_id: string
  kind: 'create' | 'edit' | 'fix'
  prompt: string
  status: 'queued' | 'running' | 'done' | 'error'
  progress: { at: number; text: string }[]
  error: string | null
  version: number | null
  elapsed_s: number
  cost_usd: number | null
  suggestions?: ViewSuggestion[]
  engine?: 'spec' | 'code'
}

export type ViewDocument = { view_id: string; version: number; kind: 'spec' | 'code'; html: string; spec?: import('@/components/viz').VizSpec | null; problems: string[] }

export type ViewPreset = { slug: string; title: string; description: string; icon: string; installed: boolean; why?: string | null; score?: number; suggested?: boolean }

/** 422 body from POST /views/generate when a request can't be a view. */
export type ViewDecline = { code: 'empty' | 'nonsense' | 'trade' | 'unavailable' | 'injection' | 'too_long'; message: string; suggestions: ViewSuggestion[]; examples: string[] }

